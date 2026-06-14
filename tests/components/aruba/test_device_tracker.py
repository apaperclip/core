"""Tests for Aruba device trackers."""

from unittest.mock import AsyncMock, patch

from aioarubainstant import ArubaClient, ArubaInstantConnectionError

from homeassistant.components.aruba.const import DOMAIN
from homeassistant.components.device_tracker import DOMAIN as DEVICE_TRACKER_DOMAIN
from homeassistant.config_entries import SOURCE_IMPORT
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PLATFORM,
    CONF_USERNAME,
    STATE_HOME,
    STATE_NOT_HOME,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.setup import async_setup_component

from .const import (
    CONFIG,
    HOST,
    MAC,
    MAC_2,
    SNAPSHOT,
    ZERO_CLIENT_SNAPSHOT,
    create_snapshot,
)

from tests.common import MockConfigEntry


def _state(hass: HomeAssistant, entity_id: str) -> State:
    """Return an entity state that must exist."""
    state = hass.states.get(entity_id)
    assert state is not None
    return state


async def _async_setup_entry(
    hass: HomeAssistant, entry: MockConfigEntry
) -> MockConfigEntry:
    """Set up an Aruba config entry."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _entity_id(
    entity_registry: er.EntityRegistry, entry: MockConfigEntry, mac: str
) -> str:
    """Return a tracker entity ID."""
    entity_id = entity_registry.async_get_entity_id(
        DEVICE_TRACKER_DOMAIN, DOMAIN, f"{entry.unique_id}_{mac}"
    )
    assert entity_id is not None
    return entity_id


async def test_client_tracker(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test client identity and attributes."""
    entry = await _async_setup_entry(hass, mock_config_entry)
    entity_id = await _entity_id(entity_registry, entry, MAC)
    state = _state(hass, entity_id)

    assert state.state == STATE_HOME
    assert state.name == "Laptop"
    assert state.attributes["ip"] == "192.0.2.20"
    assert state.attributes["mac"] == MAC
    assert state.attributes["host_name"] == "Laptop"


async def test_mac_normalization(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test client MAC addresses are normalized."""
    mock_aruba_client.async_get_snapshot.return_value = create_snapshot(
        (ArubaClient(mac="AA-BB-CC-DD-EE-FF", hostname="Laptop"),)
    )
    entry = await _async_setup_entry(hass, mock_config_entry)

    assert await _entity_id(entity_registry, entry, MAC)


async def test_dynamic_discovery_and_roaming(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test new clients and roaming retain stable entities."""
    entry = await _async_setup_entry(hass, mock_config_entry)
    first_entity_id = await _entity_id(entity_registry, entry, MAC)

    roaming_client = ArubaClient(
        mac=MAC,
        hostname="Laptop",
        ip_address="192.0.2.21",
        associated_ap="Lobby AP",
    )
    second_client = ArubaClient(mac=MAC_2, hostname="Phone")
    snapshot = create_snapshot((roaming_client, second_client), client_count=2)
    entry.runtime_data.clients = {
        MAC: roaming_client,
        MAC_2: second_client,
    }
    entry.runtime_data.async_set_updated_data(snapshot)
    await hass.async_block_till_done()

    assert await _entity_id(entity_registry, entry, MAC) == first_entity_id
    second_entity_id = await _entity_id(entity_registry, entry, MAC_2)
    assert _state(hass, second_entity_id).state == STATE_HOME
    assert (
        len(entity_registry.entities.get_entries_for_config_entry_id(entry.entry_id))
        == 2
    )


async def test_client_disappears(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test a missing client becomes disconnected."""
    entry = await _async_setup_entry(hass, mock_config_entry)
    entity_id = await _entity_id(entity_registry, entry, MAC)

    entry.runtime_data.clients = {}
    entry.runtime_data.async_set_updated_data(ZERO_CLIENT_SNAPSHOT)
    await hass.async_block_till_done()

    state = _state(hass, entity_id)
    assert state.state == STATE_NOT_HOME
    assert state.attributes["ip"] == "192.0.2.20"
    assert state.attributes["host_name"] == "Laptop"


async def test_restore_absent_client(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test a registry-known client is restored while offline."""
    mock_aruba_client.async_get_snapshot.return_value = ZERO_CLIENT_SNAPSHOT
    mock_config_entry.add_to_hass(hass)
    entity_registry.async_get_or_create(
        DEVICE_TRACKER_DOMAIN,
        DOMAIN,
        f"{mock_config_entry.unique_id}_{MAC}",
        suggested_object_id="offline_laptop",
        config_entry=mock_config_entry,
    )

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert _state(hass, "device_tracker.offline_laptop").state == STATE_NOT_HOME


async def test_failed_refresh_keeps_last_presence(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test a failed refresh marks entities unavailable without clearing clients."""
    entry = await _async_setup_entry(hass, mock_config_entry)
    entity_id = await _entity_id(entity_registry, entry, MAC)
    mock_aruba_client.async_get_snapshot.side_effect = ArubaInstantConnectionError(
        "connection failed"
    )

    await entry.runtime_data.async_refresh()

    assert _state(hass, entity_id).state == STATE_UNAVAILABLE
    assert MAC in entry.runtime_data.clients
    assert entry.runtime_data.data == SNAPSHOT


async def test_yaml_import(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Test legacy device tracker YAML is imported."""
    config = {
        DEVICE_TRACKER_DOMAIN: [
            {
                CONF_PLATFORM: DOMAIN,
                CONF_HOST: HOST,
                CONF_USERNAME: CONFIG[CONF_USERNAME],
                CONF_PASSWORD: CONFIG[CONF_PASSWORD],
            }
        ]
    }

    with patch(
        "homeassistant.components.device_tracker.legacy.async_load_config"
    ) as mock_load_known_devices:
        assert await async_setup_component(hass, DEVICE_TRACKER_DOMAIN, config)
        await hass.async_block_till_done(wait_background_tasks=True)

    mock_load_known_devices.assert_not_awaited()
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    assert entries[0].source == SOURCE_IMPORT
    assert entries[0].data == CONFIG
    assert (DOMAIN, "deprecated_yaml") in issue_registry.issues


async def test_yaml_import_failure(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Test failed YAML import creates an actionable repair issue."""
    mock_aruba_client.async_get_snapshot.side_effect = ArubaInstantConnectionError(
        "connection failed"
    )
    config = {
        DEVICE_TRACKER_DOMAIN: [
            {
                CONF_PLATFORM: DOMAIN,
                CONF_HOST: HOST,
                CONF_USERNAME: CONFIG[CONF_USERNAME],
                CONF_PASSWORD: CONFIG[CONF_PASSWORD],
            }
        ]
    }

    assert await async_setup_component(hass, DEVICE_TRACKER_DOMAIN, config)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert hass.config_entries.async_entries(DOMAIN) == []
    issue = issue_registry.async_get_issue(DOMAIN, "deprecated_yaml")
    assert issue is not None
    assert issue.translation_key == "deprecated_yaml_import_failed"
