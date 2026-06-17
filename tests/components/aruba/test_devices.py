"""Tests for Aruba devices and statistics."""

from unittest.mock import AsyncMock

from aioarubainstant import ArubaAccessPoint, ArubaInstantConnectionError

from homeassistant.components.aruba.const import DOMAIN
from homeassistant.components.aruba.helpers import (
    access_point_device_identifier,
    api_device_identifier,
    virtual_controller_device_identifier,
)
from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, EntityCategory
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import AP_KEY, AP_MAC, SNAPSHOT, create_snapshot

from tests.common import MockConfigEntry


def _state(hass: HomeAssistant, entity_id: str) -> State:
    """Return an entity state that must exist."""
    state = hass.states.get(entity_id)
    assert state is not None
    return state


def _entity_id(
    entity_registry: er.EntityRegistry,
    domain: str,
    entry: MockConfigEntry,
    unique_id: str,
) -> str:
    """Return an entity ID that must exist."""
    entity_id = entity_registry.async_get_entity_id(domain, DOMAIN, unique_id)
    assert entity_id is not None
    return entity_id


async def _async_setup_entry(
    hass: HomeAssistant, entry: MockConfigEntry
) -> MockConfigEntry:
    """Set up an Aruba config entry."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_device_hierarchy_and_statistics(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test the combined controller and access point devices."""
    mock_aruba_client.async_get_snapshot.return_value = create_snapshot(
        access_points=(
            ArubaAccessPoint(
                mac=AP_MAC,
                name="Office AP",
                ip_address="192.0.2.11",
                model="AP-515",
                serial="TESTSERIAL",
                firmware="8.6.0.22",
                connected_clients=3,
                is_master=True,
            ),
        ),
        client_count=7,
    )
    entry = await _async_setup_entry(hass, mock_config_entry)

    controller = device_registry.async_get_device(
        identifiers={api_device_identifier(entry.entry_id)}
    )
    assert controller is not None
    assert controller.name == "Test cluster"
    assert controller.manufacturer == "Aruba"
    assert controller.sw_version == "8.6.0.22"
    assert controller.via_device_id is None

    virtual_controller = device_registry.async_get_device(
        identifiers={virtual_controller_device_identifier(entry.entry_id)}
    )
    assert virtual_controller == controller

    access_point = device_registry.async_get_device(
        identifiers={access_point_device_identifier(entry.entry_id, AP_KEY)}
    )
    assert access_point is not None
    assert access_point.name == "Office AP"
    assert access_point.manufacturer == "Aruba"
    assert access_point.model == "AP-515"
    assert access_point.serial_number == "TESTSERIAL"
    assert access_point.sw_version == "8.6.0.22"
    assert access_point.via_device_id == controller.id
    assert (dr.CONNECTION_NETWORK_MAC, AP_MAC) in access_point.connections

    virtual_sensor_states = {
        "connected_clients": "7",
        "master_access_point": "Office AP",
        "access_points": "1",
    }
    for sensor_key, expected_state in virtual_sensor_states.items():
        entity_id = _entity_id(
            entity_registry,
            SENSOR_DOMAIN,
            entry,
            f"{entry.entry_id}_virtual_controller_{sensor_key}",
        )
        assert _state(hass, entity_id).state == expected_state
        entity = entity_registry.async_get(entity_id)
        assert entity is not None
        assert entity.device_id == controller.id
        assert entity.entity_category is (
            None if sensor_key == "connected_clients" else EntityCategory.DIAGNOSTIC
        )

    assert (
        entity_registry.async_get_entity_id(
            SENSOR_DOMAIN,
            DOMAIN,
            f"{entry.entry_id}_virtual_controller_ip_address",
        )
        is None
    )

    access_point_sensor_states = {
        "connected_clients": "3",
        "ip_address": "192.0.2.11",
    }
    for sensor_key, expected_state in access_point_sensor_states.items():
        entity_id = _entity_id(
            entity_registry,
            SENSOR_DOMAIN,
            entry,
            f"{entry.entry_id}_ap_{AP_KEY}_{sensor_key}",
        )
        assert _state(hass, entity_id).state == expected_state
        entity = entity_registry.async_get(entity_id)
        assert entity is not None
        assert entity.device_id == access_point.id

    controller_access_point_clients_id = _entity_id(
        entity_registry,
        SENSOR_DOMAIN,
        entry,
        f"{entry.entry_id}_virtual_controller_ap_{AP_KEY}_connected_clients",
    )
    assert _state(hass, controller_access_point_clients_id).state == "3"
    assert _state(hass, controller_access_point_clients_id).name.endswith(
        "Office AP connected clients"
    )
    controller_access_point_clients = entity_registry.async_get(
        controller_access_point_clients_id
    )
    assert controller_access_point_clients is not None
    assert controller_access_point_clients.device_id == controller.id

    api_connected_id = _entity_id(
        entity_registry,
        BINARY_SENSOR_DOMAIN,
        entry,
        f"{entry.entry_id}_api_connected",
    )
    assert _state(hass, api_connected_id).state == STATE_ON
    api_connected = entity_registry.async_get(api_connected_id)
    assert api_connected is not None
    assert api_connected.device_id == controller.id

    access_point_connected_id = _entity_id(
        entity_registry,
        BINARY_SENSOR_DOMAIN,
        entry,
        f"{entry.entry_id}_ap_{AP_KEY}_connected",
    )
    assert _state(hass, access_point_connected_id).state == STATE_ON


async def test_master_access_point_name_falls_back_to_marked_ap(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test the marked AP supplies the master name when summary omits it."""
    mock_aruba_client.async_get_snapshot.return_value = create_snapshot(master_ap=None)
    entry = await _async_setup_entry(hass, mock_config_entry)

    master_id = _entity_id(
        entity_registry,
        SENSOR_DOMAIN,
        entry,
        f"{entry.entry_id}_virtual_controller_master_access_point",
    )
    assert _state(hass, master_id).state == "Office AP"


async def test_previous_controller_devices_are_combined(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test the previous API device and controller IP entity are migrated."""
    mock_config_entry.add_to_hass(hass)
    api_device = device_registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={api_device_identifier(mock_config_entry.entry_id)},
        name="Previous API device",
    )
    virtual_controller = device_registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={virtual_controller_device_identifier(mock_config_entry.entry_id)},
        name="Test cluster",
    )
    entity_registry.async_get_or_create(
        SENSOR_DOMAIN,
        DOMAIN,
        f"{mock_config_entry.entry_id}_virtual_controller_ip_address",
        suggested_object_id="test_cluster_ip_address",
        config_entry=mock_config_entry,
        device_id=virtual_controller.id,
    )

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    controller = device_registry.async_get_device(
        identifiers={api_device_identifier(mock_config_entry.entry_id)}
    )
    assert controller is not None
    assert controller.id == virtual_controller.id
    assert device_registry.async_get(api_device.id) is None
    assert (
        device_registry.async_get_device(
            identifiers={
                virtual_controller_device_identifier(mock_config_entry.entry_id)
            }
        )
        == controller
    )
    assert entity_registry.async_get("sensor.test_cluster_ip_address") is None


async def test_dynamic_access_point_disconnect(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test dynamically discovered access points remain as disconnected."""
    entry = await _async_setup_entry(hass, mock_config_entry)
    lobby_access_point = ArubaAccessPoint(
        mac="66:77:88:99:aa:bb",
        name="Lobby AP",
        ip_address="192.0.2.12",
        model="AP-505",
        serial="LOBBYSERIAL",
        firmware="8.6.0.23",
        connected_clients=2,
        is_master=True,
    )
    lobby_key = "mac:66:77:88:99:aa:bb"
    mock_aruba_client.async_get_snapshot.return_value = create_snapshot(
        access_points=(SNAPSHOT.access_points[0], lobby_access_point),
        ap_count=2,
        client_count=3,
        master_ap="Lobby AP",
    )

    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    lobby_device = device_registry.async_get_device(
        identifiers={access_point_device_identifier(entry.entry_id, lobby_key)}
    )
    assert lobby_device is not None
    virtual_controller = device_registry.async_get_device(
        identifiers={virtual_controller_device_identifier(entry.entry_id)}
    )
    assert virtual_controller is not None
    assert lobby_device.via_device_id == virtual_controller.id

    connected_id = _entity_id(
        entity_registry,
        BINARY_SENSOR_DOMAIN,
        entry,
        f"{entry.entry_id}_ap_{lobby_key}_connected",
    )
    clients_id = _entity_id(
        entity_registry,
        SENSOR_DOMAIN,
        entry,
        f"{entry.entry_id}_ap_{lobby_key}_connected_clients",
    )
    controller_clients_id = _entity_id(
        entity_registry,
        SENSOR_DOMAIN,
        entry,
        f"{entry.entry_id}_virtual_controller_ap_{lobby_key}_connected_clients",
    )
    master_id = _entity_id(
        entity_registry,
        SENSOR_DOMAIN,
        entry,
        f"{entry.entry_id}_virtual_controller_master_access_point",
    )
    assert _state(hass, connected_id).state == STATE_ON
    assert _state(hass, clients_id).state == "2"
    clients_entity = entity_registry.async_get(clients_id)
    assert clients_entity is not None
    assert clients_entity.device_id == lobby_device.id
    assert _state(hass, controller_clients_id).state == "2"
    controller_clients_entity = entity_registry.async_get(controller_clients_id)
    assert controller_clients_entity is not None
    assert controller_clients_entity.device_id == virtual_controller.id
    assert _state(hass, controller_clients_id).name.endswith(
        "Lobby AP connected clients"
    )
    assert _state(hass, master_id).state == "Lobby AP"

    mock_aruba_client.async_get_snapshot.return_value = create_snapshot()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert _state(hass, connected_id).state == STATE_OFF
    assert _state(hass, clients_id).state == STATE_UNAVAILABLE
    assert (
        device_registry.async_get_device(
            identifiers={access_point_device_identifier(entry.entry_id, lobby_key)}
        )
        == lobby_device
    )


async def test_restore_absent_access_point(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test a registry-known access point is restored while disconnected."""
    lobby_key = "mac:66:77:88:99:aa:bb"
    mock_aruba_client.async_get_snapshot.return_value = create_snapshot(
        (),
        access_points=(),
        ap_count=0,
        client_count=0,
        master_ap=None,
    )
    mock_config_entry.add_to_hass(hass)
    entity_registry.async_get_or_create(
        BINARY_SENSOR_DOMAIN,
        DOMAIN,
        f"{mock_config_entry.entry_id}_ap_{lobby_key}_connected",
        suggested_object_id="lobby_ap_connected",
        config_entry=mock_config_entry,
    )
    entity_registry.async_get_or_create(
        SENSOR_DOMAIN,
        DOMAIN,
        f"{mock_config_entry.entry_id}_ap_{lobby_key}_connected_clients",
        suggested_object_id="lobby_ap_connected_clients",
        config_entry=mock_config_entry,
    )

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert _state(hass, "binary_sensor.lobby_ap_connected").state == STATE_OFF
    assert _state(hass, "sensor.lobby_ap_connected_clients").state == STATE_UNAVAILABLE


async def test_restored_controller_access_point_clients_name_updates(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test a restored controller AP client sensor uses the current AP name."""
    serial_key = "serial:CK0113706"
    access_point_name = "20-a6-cd-c5-a0-b4-upstairs"
    mock_aruba_client.async_get_snapshot.return_value = create_snapshot(
        (),
        access_points=(),
        ap_count=0,
        client_count=0,
        master_ap=None,
    )
    mock_config_entry.add_to_hass(hass)
    entity_registry.async_get_or_create(
        SENSOR_DOMAIN,
        DOMAIN,
        f"{mock_config_entry.entry_id}_virtual_controller_ap_{serial_key}"
        "_connected_clients",
        suggested_object_id="ck0113706_connected_clients",
        config_entry=mock_config_entry,
    )

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert _state(hass, "sensor.ck0113706_connected_clients").name.endswith(
        "CK0113706 connected clients"
    )

    mock_aruba_client.async_get_snapshot.return_value = create_snapshot(
        (),
        access_points=(
            ArubaAccessPoint(
                mac=None,
                name=access_point_name,
                ip_address="192.0.2.12",
                model="AP-515",
                serial="CK0113706",
                firmware="8.6.0.22",
                connected_clients=4,
                is_master=True,
            ),
        ),
        ap_count=1,
        client_count=4,
        master_ap=access_point_name,
    )

    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert _state(hass, "sensor.ck0113706_connected_clients").state == "4"
    assert _state(hass, "sensor.ck0113706_connected_clients").name.endswith(
        f"{access_point_name} connected clients"
    )


async def test_failed_refresh_connectivity(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test failed updates distinguish API failure from AP disconnection."""
    entry = await _async_setup_entry(hass, mock_config_entry)
    api_connected_id = _entity_id(
        entity_registry,
        BINARY_SENSOR_DOMAIN,
        entry,
        f"{entry.entry_id}_api_connected",
    )
    access_point_connected_id = _entity_id(
        entity_registry,
        BINARY_SENSOR_DOMAIN,
        entry,
        f"{entry.entry_id}_ap_{AP_KEY}_connected",
    )
    mock_aruba_client.async_get_snapshot.side_effect = ArubaInstantConnectionError(
        "connection failed"
    )

    await entry.runtime_data.async_refresh()

    assert _state(hass, api_connected_id).state == STATE_OFF
    assert _state(hass, access_point_connected_id).state == STATE_UNAVAILABLE
