"""The Aruba integration."""

from contextlib import suppress
import logging

from aioarubainstant import ArubaInstantError

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import ArubaDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = (Platform.DEVICE_TRACKER,)

type ArubaConfigEntry = ConfigEntry[ArubaDataUpdateCoordinator]


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
