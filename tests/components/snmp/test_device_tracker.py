"""Tests for the SNMP device tracker."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.device_tracker import (
    CONF_CONSIDER_HOME,
    CONF_SCAN_INTERVAL,
    DOMAIN as DEVICE_TRACKER_DOMAIN,
)
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.snmp.const import (
    CONF_BASEOID,
    CONF_VERSION,
    DEFAULT_VERSION,
    DOMAIN,
)
from homeassistant.components.snmp.coordinator import (
    ARUBA_CLUSTER_MASTER,
    ARUBA_CLUSTER_SLAVE,
    ARUBA_INSTANT_CLIENT_TABLE_OID,
    SnmpAccessPointInfo,
    SnmpClientInfo,
    SnmpCoordinatorData,
    SnmpSystemInfo,
    SnmpWalkError,
)
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntryState
from homeassistant.const import (
    CONF_PLATFORM,
    STATE_HOME,
    STATE_NOT_HOME,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    EntityCategory,
)
from homeassistant.core import DOMAIN as HOMEASSISTANT_DOMAIN, HomeAssistant, State
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.setup import async_setup_component

from .const import (
    CONFIG,
    HOST,
    MAC,
    MAC_2,
    SYSTEM_DESCRIPTION,
    SYSTEM_INFO,
    SYSTEM_NAME,
    SYSTEM_OBJECT_ID,
    SYSTEM_UPTIME,
)

from tests.common import MockConfigEntry, async_fire_time_changed

ENTITY_ID = "device_tracker.snmp_aa_bb_cc_dd_ee_ff"
ENTITY_ID_2 = "device_tracker.snmp_11_22_33_44_55_66"


def _state(hass: HomeAssistant, entity_id: str) -> State:
    """Return an entity state that must exist."""
    state = hass.states.get(entity_id)
    assert state is not None
    return state


async def _async_setup_entry(
    hass: HomeAssistant,
    *,
    options: dict[str, int] | None = None,
    entry: MockConfigEntry | None = None,
) -> MockConfigEntry:
    """Set up an SNMP config entry."""
    if entry is None:
        entry = MockConfigEntry(
            domain=DOMAIN, title=HOST, data=CONFIG, options=options or {}
        )
        entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_unknown_client_disabled(
    hass: HomeAssistant,
    mock_snmp_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test newly discovered unknown clients are disabled by default."""
    entry = await _async_setup_entry(hass)

    entity_id = entity_registry.async_get_entity_id(
        DEVICE_TRACKER_DOMAIN, DOMAIN, f"{MAC}_{entry.entry_id}"
    )
    assert entity_id == ENTITY_ID
    entity_entry = entity_registry.async_get(entity_id)
    assert entity_entry is not None
    assert entity_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert entity_entry.original_name == f"SNMP {MAC}"
    assert hass.states.get(entity_id) is None


async def test_device_registry_client_enabled_and_renamed(
    hass: HomeAssistant,
    mock_snmp_client: AsyncMock,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test a known registry client is enabled and supports entity renaming."""
    other_entry = MockConfigEntry()
    other_entry.add_to_hass(hass)
    device_entry = device_registry.async_get_or_create(
        config_entry_id=other_entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, MAC)},
        name="Laptop",
    )

    await _async_setup_entry(hass)

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_HOME
    assert state.name == f"Laptop SNMP {MAC}"
    entity_entry = entity_registry.async_get(ENTITY_ID)
    assert entity_entry is not None
    assert entity_entry.device_id == device_entry.id

    entity_registry.async_update_entity(ENTITY_ID, name=f"Laptop {MAC}")
    await hass.async_block_till_done()
    assert _state(hass, ENTITY_ID).name == f"Laptop {MAC}"


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_dynamic_discovery(
    hass: HomeAssistant, mock_snmp_client: AsyncMock
) -> None:
    """Test clients discovered after setup create entities."""
    mock_snmp_client.async_get_clients.return_value = {}
    entry = await _async_setup_entry(hass)
    assert hass.states.get(ENTITY_ID_2) is None

    entry.runtime_data.async_set_updated_data(
        SnmpCoordinatorData(
            frozenset({MAC_2}),
            SYSTEM_INFO,
            {MAC_2: SnmpClientInfo(mac_address=MAC_2)},
        )
    )
    await hass.async_block_till_done()

    assert _state(hass, ENTITY_ID_2).state == STATE_HOME


async def test_restore_absent_client(
    hass: HomeAssistant,
    mock_snmp_client: AsyncMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test a registered client absent at startup is restored as not home."""
    mock_snmp_client.async_get_clients.return_value = {}
    entry = MockConfigEntry(domain=DOMAIN, title=HOST, data=CONFIG)
    entry.add_to_hass(hass)
    entity_registry.async_get_or_create(
        DEVICE_TRACKER_DOMAIN,
        DOMAIN,
        f"{MAC}_{entry.entry_id}",
        suggested_object_id=ENTITY_ID.removeprefix("device_tracker."),
        config_entry=entry,
    )

    await _async_setup_entry(hass, entry=entry)

    assert _state(hass, ENTITY_ID).state == STATE_NOT_HOME


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_consider_home(
    hass: HomeAssistant,
    mock_snmp_client: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a missing client remains home for the configured grace period."""
    entry = await _async_setup_entry(
        hass, options={CONF_SCAN_INTERVAL: 12, CONF_CONSIDER_HOME: 30}
    )
    assert _state(hass, ENTITY_ID).state == STATE_HOME

    mock_snmp_client.async_get_clients.return_value = {}
    await entry.runtime_data.async_refresh()
    assert _state(hass, ENTITY_ID).state == STATE_HOME

    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert _state(hass, ENTITY_ID).state == STATE_NOT_HOME


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_polling_failure_and_recovery(
    hass: HomeAssistant, mock_snmp_client: AsyncMock
) -> None:
    """Test polling failures make entities unavailable and later recover."""
    entry = await _async_setup_entry(hass)

    mock_snmp_client.async_get_clients.side_effect = SnmpWalkError("failed")
    await entry.runtime_data.async_refresh()
    assert _state(hass, ENTITY_ID).state == STATE_UNAVAILABLE

    mock_snmp_client.async_get_clients.side_effect = None
    mock_snmp_client.async_get_clients.return_value = {
        MAC: SnmpClientInfo(mac_address=MAC)
    }
    await entry.runtime_data.async_refresh()
    assert _state(hass, ENTITY_ID).state == STATE_HOME


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_reload_and_unload(
    hass: HomeAssistant, mock_snmp_client: AsyncMock
) -> None:
    """Test reloading and unloading an SNMP tracker entry."""
    entry = await _async_setup_entry(hass)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert _state(hass, ENTITY_ID).state == STATE_HOME

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert _state(hass, ENTITY_ID).state == STATE_UNAVAILABLE


async def test_yaml_import_does_not_load_known_devices(
    hass: HomeAssistant,
    mock_snmp_client: AsyncMock,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Test YAML is imported without reading known_devices.yaml."""
    config = {
        DEVICE_TRACKER_DOMAIN: [
            {
                CONF_PLATFORM: DOMAIN,
                **CONFIG,
                CONF_SCAN_INTERVAL: 20,
                CONF_CONSIDER_HOME: 90,
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
    assert entries[0].data == {**CONFIG, CONF_VERSION: DEFAULT_VERSION}
    assert entries[0].options == {
        CONF_SCAN_INTERVAL: 20,
        CONF_CONSIDER_HOME: 90,
    }
    assert (HOMEASSISTANT_DOMAIN, f"deprecated_yaml_{DOMAIN}") in issue_registry.issues


async def test_host_device_and_diagnostics(
    hass: HomeAssistant,
    mock_snmp_client: AsyncMock,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test host metadata and diagnostic entities."""
    entry = await _async_setup_entry(hass)

    device = device_registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert device is not None
    assert device.name == SYSTEM_NAME
    assert device.model == SYSTEM_DESCRIPTION
    assert device.model_id == SYSTEM_OBJECT_ID
    assert entry.entry_id in device.config_entries

    connected_id = entity_registry.async_get_entity_id(
        BINARY_SENSOR_DOMAIN, DOMAIN, f"{entry.entry_id}_connected"
    )
    associated_clients_id = entity_registry.async_get_entity_id(
        SENSOR_DOMAIN, DOMAIN, f"{entry.entry_id}_associated_clients"
    )
    uptime_id = entity_registry.async_get_entity_id(
        SENSOR_DOMAIN, DOMAIN, f"{entry.entry_id}_uptime"
    )
    assert connected_id is not None
    assert associated_clients_id is not None
    assert uptime_id is not None

    for entity_id in (connected_id, associated_clients_id, uptime_id):
        entity_entry = entity_registry.async_get(entity_id)
        assert entity_entry is not None
        assert entity_entry.device_id == device.id

    assert _state(hass, connected_id).state == STATE_ON
    assert _state(hass, associated_clients_id).state == "1"
    uptime_entry = entity_registry.async_get(uptime_id)
    assert uptime_entry is not None
    assert uptime_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(uptime_id) is None

    tracker_entry = entity_registry.async_get(ENTITY_ID)
    assert tracker_entry is not None
    assert tracker_entry.device_id is None

    mock_snmp_client.async_get_clients.side_effect = SnmpWalkError("failed")
    await entry.runtime_data.async_refresh()
    assert _state(hass, connected_id).state == STATE_OFF
    assert _state(hass, associated_clients_id).state == STATE_UNAVAILABLE

    mock_snmp_client.async_get_clients.side_effect = None
    mock_snmp_client.async_get_clients.return_value = {
        MAC: SnmpClientInfo(mac_address=MAC),
        MAC_2: SnmpClientInfo(mac_address=MAC_2),
    }
    await entry.runtime_data.async_refresh()
    assert _state(hass, connected_id).state == STATE_ON
    assert _state(hass, associated_clients_id).state == "2"


async def test_aruba_access_points_sensor(
    hass: HomeAssistant,
    mock_snmp_client: AsyncMock,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test an Aruba Instant cluster and its member access point devices."""
    mock_snmp_client.access_points = {
        "00:11:22:33:44:55": SnmpAccessPointInfo(
            mac_address="00:11:22:33:44:55",
            name="Office AP",
            role=ARUBA_CLUSTER_MASTER,
            connected_clients=1,
        ),
        "66:77:88:99:aa:bb": SnmpAccessPointInfo(
            mac_address="66:77:88:99:aa:bb",
            name="Lobby AP",
            role=ARUBA_CLUSTER_SLAVE,
            connected_clients=0,
        ),
    }
    mock_snmp_client.async_get_system_info.return_value = SnmpSystemInfo(
        name="Lobby AP"
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=HOST,
        data={
            **CONFIG,
            CONF_BASEOID: ".".join(map(str, ARUBA_INSTANT_CLIENT_TABLE_OID)),
        },
    )
    entry.add_to_hass(hass)

    await _async_setup_entry(hass, entry=entry)

    entity_id = entity_registry.async_get_entity_id(
        SENSOR_DOMAIN, DOMAIN, f"{entry.entry_id}_access_points"
    )
    assert entity_id is not None
    state = _state(hass, entity_id)
    assert state.state == "2"
    assert state.attributes["access_points"] == [
        {"name": "Lobby AP", "mac_address": "66:77:88:99:aa:bb"},
        {"name": "Office AP", "mac_address": "00:11:22:33:44:55"},
    ]
    master_id = entity_registry.async_get_entity_id(
        SENSOR_DOMAIN, DOMAIN, f"{entry.entry_id}_management_master"
    )
    assert master_id is not None
    assert _state(hass, master_id).state == "Office AP"

    cluster_device = device_registry.async_get_device(
        identifiers={(DOMAIN, entry.entry_id)}
    )
    assert cluster_device is not None
    assert cluster_device.name == HOST
    assert cluster_device.manufacturer == "Aruba"

    for mac, name, clients in (
        ("00:11:22:33:44:55", "Office AP", "1"),
        ("66:77:88:99:aa:bb", "Lobby AP", "0"),
    ):
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, f"{entry.entry_id}_{mac}")}
        )
        assert device is not None
        assert device.name == name
        assert device.manufacturer == "Aruba"
        assert device.via_device_id == cluster_device.id

        clients_id = entity_registry.async_get_entity_id(
            SENSOR_DOMAIN, DOMAIN, f"{entry.entry_id}_{mac}_connected_clients"
        )
        distribution_id = entity_registry.async_get_entity_id(
            SENSOR_DOMAIN,
            DOMAIN,
            f"{entry.entry_id}_{mac}_cluster_connected_clients",
        )
        connected_id = entity_registry.async_get_entity_id(
            BINARY_SENSOR_DOMAIN, DOMAIN, f"{entry.entry_id}_{mac}_connected"
        )
        assert clients_id is not None
        assert distribution_id is not None
        assert connected_id is not None
        assert _state(hass, clients_id).state == clients
        assert _state(hass, distribution_id).state == clients
        assert _state(hass, connected_id).state == STATE_ON

        distribution_entry = entity_registry.async_get(distribution_id)
        assert distribution_entry is not None
        assert distribution_entry.device_id == cluster_device.id
        assert distribution_entry.entity_category is EntityCategory.DIAGNOSTIC
        assert distribution_entry.original_name == f"{name} connected clients"


@pytest.mark.parametrize(
    ("roles", "warning"),
    [
        pytest.param(
            (ARUBA_CLUSTER_SLAVE, ARUBA_CLUSTER_SLAVE),
            False,
            id="no-master",
        ),
        pytest.param(
            (ARUBA_CLUSTER_MASTER, ARUBA_CLUSTER_MASTER),
            True,
            id="multiple-masters",
        ),
    ],
)
async def test_management_master_unavailable(
    hass: HomeAssistant,
    mock_snmp_client: AsyncMock,
    entity_registry: er.EntityRegistry,
    caplog: pytest.LogCaptureFixture,
    roles: tuple[str, str],
    warning: bool,
) -> None:
    """Test invalid Aruba management-master states are unavailable."""
    mock_snmp_client.access_points = {
        "00:11:22:33:44:55": SnmpAccessPointInfo(
            mac_address="00:11:22:33:44:55",
            name="Office AP",
            role=roles[0],
        ),
        "66:77:88:99:aa:bb": SnmpAccessPointInfo(
            mac_address="66:77:88:99:aa:bb",
            name="Lobby AP",
            role=roles[1],
        ),
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=HOST,
        data={
            **CONFIG,
            CONF_BASEOID: ".".join(map(str, ARUBA_INSTANT_CLIENT_TABLE_OID)),
        },
    )
    entry.add_to_hass(hass)

    await _async_setup_entry(hass, entry=entry)

    master_id = entity_registry.async_get_entity_id(
        SENSOR_DOMAIN, DOMAIN, f"{entry.entry_id}_management_master"
    )
    assert master_id is not None
    assert _state(hass, master_id).state == STATE_UNAVAILABLE
    assert ("Multiple Aruba management masters reported" in caplog.text) is warning


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_optional_uptime_sensor(
    hass: HomeAssistant, mock_snmp_client: AsyncMock, entity_registry: er.EntityRegistry
) -> None:
    """Test the optional system uptime sensor when the OID is supported."""
    entry = await _async_setup_entry(hass)

    uptime_id = entity_registry.async_get_entity_id(
        SENSOR_DOMAIN, DOMAIN, f"{entry.entry_id}_uptime"
    )
    assert uptime_id is not None
    assert _state(hass, uptime_id).state == str(SYSTEM_UPTIME)


async def test_optional_system_info_unsupported(
    hass: HomeAssistant,
    mock_snmp_client: AsyncMock,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Test unsupported system OIDs do not prevent host setup."""
    mock_snmp_client.async_get_system_info.return_value = SnmpSystemInfo()

    entry = await _async_setup_entry(hass)

    device = device_registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert device is not None
    assert device.name == HOST
    assert device.model is None
    assert device.model_id is None
