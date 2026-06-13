"""The SNMP integration."""

from pysnmp.error import PySnmpError

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .coordinator import SnmpDataUpdateCoordinator, async_create_snmp_client
from .util import async_get_snmp_engine

type SnmpConfigEntry = ConfigEntry[SnmpDataUpdateCoordinator]

PLATFORMS = (
    Platform.BINARY_SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.SENSOR,
)

__all__ = ["async_get_snmp_engine"]


async def async_setup_entry(hass: HomeAssistant, entry: SnmpConfigEntry) -> bool:
    """Set up SNMP from a config entry."""
    try:
        client = await async_create_snmp_client(hass, dict(entry.data))
    except PySnmpError as err:
        raise ConfigEntryNotReady(
            f"Unable to create an SNMP connection to {entry.data[CONF_HOST]}: {err}"
        ) from err

    coordinator = SnmpDataUpdateCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SnmpConfigEntry) -> bool:
    """Unload an SNMP config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
