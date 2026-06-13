"""Tests for the SNMP config flow."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.components.device_tracker import (
    CONF_CONSIDER_HOME,
    CONF_SCAN_INTERVAL,
)
from homeassistant.components.snmp.const import (
    CONF_AUTH_KEY,
    CONF_BASEOID,
    CONF_COMMUNITY,
    CONF_PRIV_KEY,
    CONF_VERSION,
    DEFAULT_VERSION,
    DOMAIN,
)
from homeassistant.components.snmp.coordinator import SnmpWalkError
from homeassistant.config_entries import SOURCE_IMPORT, SOURCE_USER
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .const import BASE_OID, CONFIG, HOST

from tests.common import MockConfigEntry

FLOW_CONFIG = {**CONFIG, CONF_VERSION: DEFAULT_VERSION}


async def test_user_flow(hass: HomeAssistant, mock_snmp_client: AsyncMock) -> None:
    """Test creating an SNMP tracker config entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}
    assert (
        result["data_schema"]({CONF_HOST: HOST, CONF_BASEOID: BASE_OID}) == FLOW_CONFIG
    )

    with patch(
        "homeassistant.components.snmp.async_setup_entry", return_value=True
    ) as mock_setup_entry:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: f" {HOST} ", CONF_BASEOID: f" {BASE_OID} "},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == HOST
    assert result["data"] == FLOW_CONFIG
    assert mock_setup_entry.call_count == 1
    mock_snmp_client.async_get_mac_addresses.assert_awaited_once_with()


@pytest.mark.parametrize(
    ("error", "flow_error"),
    [
        pytest.param(SnmpWalkError("walk failed"), "cannot_connect", id="connection"),
        pytest.param(RuntimeError("unexpected"), "unknown", id="unknown"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant,
    mock_snmp_client: AsyncMock,
    error: Exception,
    flow_error: str,
) -> None:
    """Test errors while validating user input."""
    mock_snmp_client.async_get_mac_addresses.side_effect = error
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], FLOW_CONFIG
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": flow_error}


async def test_duplicate(hass: HomeAssistant, mock_snmp_client: AsyncMock) -> None:
    """Test duplicate host and base OID combinations are rejected."""
    MockConfigEntry(domain=DOMAIN, data=CONFIG).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {**CONFIG, CONF_COMMUNITY: "different"},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    mock_snmp_client.async_get_mac_addresses.assert_not_awaited()


async def test_import(hass: HomeAssistant, mock_snmp_client: AsyncMock) -> None:
    """Test importing a legacy device tracker configuration."""
    with patch("homeassistant.components.snmp.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_IMPORT},
            data={
                **CONFIG,
                CONF_SCAN_INTERVAL: timedelta(seconds=30),
                CONF_CONSIDER_HOME: timedelta(minutes=4),
            },
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == HOST
    assert result["data"] == FLOW_CONFIG
    assert result["options"] == {
        CONF_SCAN_INTERVAL: 30,
        CONF_CONSIDER_HOME: 240,
    }


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        pytest.param(SnmpWalkError("walk failed"), "cannot_connect", id="connection"),
        pytest.param(RuntimeError("unexpected"), "unknown", id="unknown"),
    ],
)
async def test_import_errors(
    hass: HomeAssistant,
    mock_snmp_client: AsyncMock,
    error: Exception,
    reason: str,
) -> None:
    """Test errors while importing YAML."""
    mock_snmp_client.async_get_mac_addresses.side_effect = error

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_IMPORT}, data=CONFIG
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason


async def test_reconfigure(hass: HomeAssistant, mock_snmp_client: AsyncMock) -> None:
    """Test reconfiguring an SNMP tracker."""
    entry = MockConfigEntry(domain=DOMAIN, title=HOST, data=CONFIG)
    entry.add_to_hass(hass)
    new_data = {
        CONF_HOST: "192.168.1.33",
        CONF_BASEOID: "1.3.6.1.2.1.4.22.1.2",
        CONF_COMMUNITY: "private",
        CONF_VERSION: "2c",
        CONF_AUTH_KEY: "auth-key",
        CONF_PRIV_KEY: "priv-key",
    }

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(result["flow_id"], new_data)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.title == new_data[CONF_HOST]
    assert entry.data == new_data


async def test_reconfigure_error(
    hass: HomeAssistant, mock_snmp_client: AsyncMock
) -> None:
    """Test an error while reconfiguring an SNMP tracker."""
    entry = MockConfigEntry(domain=DOMAIN, title=HOST, data=CONFIG)
    entry.add_to_hass(hass)
    mock_snmp_client.async_get_mac_addresses.side_effect = SnmpWalkError("failed")

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CONFIG)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_options_flow(hass: HomeAssistant) -> None:
    """Test updating polling and consider-home options reloads the entry."""
    entry = MockConfigEntry(domain=DOMAIN, data=CONFIG)
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["data_schema"]({}) == {
        CONF_SCAN_INTERVAL: 12,
        CONF_CONSIDER_HOME: 180,
    }

    with patch.object(
        hass.config_entries, "async_reload", return_value=True
    ) as mock_reload:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_SCAN_INTERVAL: 45, CONF_CONSIDER_HOME: 300},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_SCAN_INTERVAL: 45, CONF_CONSIDER_HOME: 300}
    mock_reload.assert_awaited_once_with(entry.entry_id)
