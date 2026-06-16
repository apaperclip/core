"""The Aruba integration."""

from contextlib import suppress
from functools import partial
import logging

from aioarubainstant import ArubaInstantError
from yarl import URL

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import format_mac

from .const import MANUFACTURER
from .coordinator import ArubaDataUpdateCoordinator
from .helpers import (
    access_point_device_identifier,
    api_device_identifier,
    virtual_controller_device_identifier,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = (
    Platform.BINARY_SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.SENSOR,
)

type ArubaConfigEntry = ConfigEntry[ArubaDataUpdateCoordinator]


@callback
def _update_devices(
    hass: HomeAssistant,
    entry: ArubaConfigEntry,
) -> None:
    """Update the Aruba device hierarchy."""
    coordinator = entry.runtime_data
    device_registry = dr.async_get(hass)
    host = entry.data[CONF_HOST]
    api_identifier = api_device_identifier(entry.entry_id)
    virtual_controller_identifier = virtual_controller_device_identifier(entry.entry_id)

    api_device = device_registry.async_get_device(identifiers={api_identifier})
    virtual_controller = device_registry.async_get_device(
        identifiers={virtual_controller_identifier}
    )
    if (
        api_device is not None
        and virtual_controller is not None
        and api_device.id != virtual_controller.id
    ):
        entity_registry = er.async_get(hass)
        for entity in er.async_entries_for_device(
            entity_registry, api_device.id, include_disabled_entities=True
        ):
            entity_registry.async_update_entity(
                entity.entity_id, device_id=virtual_controller.id
            )
        device_registry.async_remove_device(api_device.id)

    cluster = coordinator.data.cluster
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        configuration_url=URL.build(
            scheme="https",
            host=host,
            port=entry.data[CONF_PORT],
        ),
        identifiers={api_identifier, virtual_controller_identifier},
        manufacturer=MANUFACTURER,
        name=cluster.name or entry.title,
        sw_version=cluster.version,
    )

    for access_point_id, access_point in coordinator.access_points.items():
        connections = (
            {(dr.CONNECTION_NETWORK_MAC, format_mac(access_point.mac))}
            if access_point.mac
            else set()
        )
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            connections=connections,
            identifiers={
                access_point_device_identifier(entry.entry_id, access_point_id)
            },
            manufacturer=MANUFACTURER,
            model=access_point.model,
            name=(
                access_point.name
                or access_point.serial
                or access_point.mac
                or "Aruba access point"
            ),
            serial_number=access_point.serial,
            sw_version=access_point.firmware,
            via_device=virtual_controller_identifier,
        )


async def async_setup_entry(hass: HomeAssistant, entry: ArubaConfigEntry) -> bool:
    """Set up Aruba from a config entry."""
    coordinator = ArubaDataUpdateCoordinator(hass, entry)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        with suppress(ArubaInstantError):
            await coordinator.client.async_close()
        raise

    entry.runtime_data = coordinator
    _update_devices(hass, entry)
    entry.async_on_unload(
        coordinator.async_add_listener(partial(_update_devices, hass, entry))
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ArubaConfigEntry) -> bool:
    """Unload an Aruba config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    try:
        await entry.runtime_data.client.async_close()
    except ArubaInstantError:
        _LOGGER.debug("Error logging out from the Aruba controller")
    return True
