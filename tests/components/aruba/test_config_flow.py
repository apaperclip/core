"""Tests for the Aruba config flow."""

import ssl
from unittest.mock import ANY, AsyncMock, patch

from aioarubainstant import (
    ArubaInstantAuthenticationError,
    ArubaInstantCommandError,
    ArubaInstantConnectionError,
    ArubaInstantError,
    ArubaInstantNotMasterError,
    ArubaInstantParseError,
    ArubaInstantRestDisabledError,
    ArubaInstantTimeoutError,
)
import pytest

from homeassistant.components.aruba.const import DOMAIN
from homeassistant.config_entries import SOURCE_IMPORT, SOURCE_USER
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .const import CONFIG, HOST, MANAGEMENT_ADDRESS, SNAPSHOT

from tests.common import MockConfigEntry


async def test_user_flow(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
) -> None:
    """Test a successful user flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    with patch(
        "homeassistant.components.aruba.async_setup_entry", return_value=True
    ) as mock_setup_entry:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                **CONFIG,
                CONF_HOST: "https://CONTROLLER.EXAMPLE.COM/",
                CONF_PORT: 4443.0,
            },
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test cluster"
    assert result["data"] == {
        **CONFIG,
        CONF_HOST: HOST,
        CONF_PORT: 4443,
    }
    assert result["result"].unique_id == MANAGEMENT_ADDRESS
    assert mock_setup_entry.call_count == 1
    mock_aruba_client.client_class.assert_called_once_with(
        HOST,
        CONFIG[CONF_USERNAME],
        CONFIG[CONF_PASSWORD],
        port=4443,
        verify_ssl=ANY,
    )
    ssl_context = mock_aruba_client.client_class.call_args.kwargs["verify_ssl"]
    assert isinstance(ssl_context, ssl.SSLContext)
    assert ssl_context.verify_mode is ssl.CERT_REQUIRED
    assert ssl_context.options & ssl.OP_IGNORE_UNEXPECTED_EOF
    mock_aruba_client.async_get_snapshot.assert_awaited_once_with()
    mock_aruba_client.async_close.assert_awaited_once_with()


async def test_fractional_port_is_rejected(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
) -> None:
    """Test a fractional port cannot reach the client URL builder."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**CONFIG, CONF_PORT: 4343.5}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_host"}
    mock_aruba_client.client_class.assert_not_called()


async def test_duplicate_cluster(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test duplicate prevention uses the cluster management address."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**CONFIG, CONF_HOST: "other-master.example.com"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_host_fallback_unique_id(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
) -> None:
    """Test normalized host fallback when no management address is reported."""
    mock_aruba_client.async_get_snapshot.return_value = SNAPSHOT.__class__(
        cluster=SNAPSHOT.cluster.__class__(
            name=None,
            management_address=None,
        ),
        access_points=(),
        clients=(),
        _raw_output={},
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CONFIG)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == HOST
    assert result["result"].unique_id == HOST


@pytest.mark.parametrize(
    ("error", "flow_error"),
    [
        pytest.param(
            ArubaInstantAuthenticationError("bad credentials"),
            "invalid_auth",
            id="authentication",
        ),
        pytest.param(
            ArubaInstantConnectionError("connection failed"),
            "cannot_connect",
            id="connection",
        ),
        pytest.param(
            ArubaInstantTimeoutError("timeout"), "cannot_connect", id="timeout"
        ),
        pytest.param(
            ArubaInstantRestDisabledError("disabled"),
            "rest_disabled",
            id="rest-disabled",
        ),
        pytest.param(
            ArubaInstantNotMasterError("not master"),
            "not_master",
            id="not-master",
        ),
        pytest.param(
            ArubaInstantCommandError("command"),
            "invalid_response",
            id="command",
        ),
        pytest.param(ArubaInstantParseError("parse"), "invalid_response", id="parse"),
        pytest.param(ArubaInstantError("other"), "cannot_connect", id="library-error"),
        pytest.param(RuntimeError("unexpected"), "unknown", id="unexpected"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
    error: Exception,
    flow_error: str,
) -> None:
    """Test config flow exception mapping."""
    mock_aruba_client.async_get_snapshot.side_effect = error
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CONFIG)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": flow_error}


async def test_invalid_host(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
) -> None:
    """Test invalid host input."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**CONFIG, CONF_HOST: "http://controller.example.com/path"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_host"}
    mock_aruba_client.async_get_snapshot.assert_not_awaited()


async def test_reauth(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test successful reauthentication."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    with patch("homeassistant.components.aruba.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_USERNAME: "new-admin",
                CONF_PASSWORD: "new-password",
            },
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data == {
        **CONFIG,
        CONF_USERNAME: "new-admin",
        CONF_PASSWORD: "new-password",
    }


async def test_reauth_failure(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test failed reauthentication."""
    mock_config_entry.add_to_hass(hass)
    mock_aruba_client.async_get_snapshot.side_effect = ArubaInstantAuthenticationError(
        "bad credentials"
    )
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_USERNAME: CONFIG[CONF_USERNAME],
            CONF_PASSWORD: "bad-password",
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert mock_config_entry.data == CONFIG


async def test_reconfigure(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test successful reconfiguration."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    new_data = {
        CONF_HOST: "new-master.example.com",
        CONF_PORT: 443,
        CONF_VERIFY_SSL: False,
    }

    with patch("homeassistant.components.aruba.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], new_data
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data == {**CONFIG, **new_data}
    assert mock_config_entry.title == "Test cluster"
    ssl_context = mock_aruba_client.client_class.call_args.kwargs["verify_ssl"]
    assert isinstance(ssl_context, ssl.SSLContext)
    assert ssl_context.verify_mode is ssl.CERT_NONE
    assert not ssl_context.check_hostname
    assert ssl_context.options & ssl.OP_IGNORE_UNEXPECTED_EOF


async def test_reconfigure_different_cluster(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test reconfiguration cannot switch to a different cluster."""
    mock_config_entry.add_to_hass(hass)
    mock_aruba_client.async_get_snapshot.return_value = SNAPSHOT.__class__(
        cluster=SNAPSHOT.cluster.__class__(management_address="192.0.2.99"),
        access_points=(),
        clients=(),
        _raw_output={},
    )
    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "different.example.com",
            CONF_PORT: 4343,
            CONF_VERIFY_SSL: True,
        },
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"
    assert mock_config_entry.data == CONFIG


async def test_reconfigure_connection_failure(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test failed reconfiguration leaves the entry unchanged."""
    mock_config_entry.add_to_hass(hass)
    mock_aruba_client.async_get_snapshot.side_effect = ArubaInstantConnectionError(
        f"could not connect with {CONFIG[CONF_PASSWORD]}"
    )
    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "unreachable.example.com",
            CONF_PORT: 4343,
            CONF_VERIFY_SSL: True,
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert mock_config_entry.data == CONFIG


async def test_import(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
) -> None:
    """Test importing legacy YAML data."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_IMPORT},
        data={
            CONF_HOST: HOST,
            CONF_USERNAME: CONFIG[CONF_USERNAME],
            CONF_PASSWORD: CONFIG[CONF_PASSWORD],
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == CONFIG
    assert result["result"].unique_id == MANAGEMENT_ADDRESS
