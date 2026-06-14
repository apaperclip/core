"""Data update coordinator for Aruba Instant."""

import logging

from aioarubainstant import (
    ArubaClient,
    ArubaInstantAuthenticationError,
    ArubaInstantClient,
    ArubaInstantCommandError,
    ArubaInstantConnectionError,
    ArubaInstantError,
    ArubaInstantNotMasterError,
    ArubaInstantParseError,
    ArubaInstantRestDisabledError,
    ArubaInstantSnapshot,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class ArubaDataUpdateCoordinator(DataUpdateCoordinator[ArubaInstantSnapshot]):
    """Coordinate Aruba Instant snapshots."""

    clients: dict[str, ArubaClient]

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry[ArubaDataUpdateCoordinator],
    ) -> None:
        """Initialize the Aruba coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
            always_update=False,
        )
        self.client = ArubaInstantClient(
            config_entry.data[CONF_HOST],
            config_entry.data[CONF_USERNAME],
            config_entry.data[CONF_PASSWORD],
            port=config_entry.data[CONF_PORT],
            verify_ssl=config_entry.data[CONF_VERIFY_SSL],
            session=async_get_clientsession(hass),
        )
        self.clients = {}

    async def _async_update_data(self) -> ArubaInstantSnapshot:
        """Fetch one complete snapshot from the controller."""
        try:
            snapshot = await self.client.async_get_snapshot()
        except ArubaInstantAuthenticationError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="authentication_error",
            ) from err
        except ArubaInstantConnectionError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="connection_error",
            ) from err
        except ArubaInstantRestDisabledError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="rest_disabled",
            ) from err
        except ArubaInstantNotMasterError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="not_master",
            ) from err
        except (ArubaInstantCommandError, ArubaInstantParseError) as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="invalid_response",
            ) from err
        except ArubaInstantError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_error",
            ) from err
        except Exception as err:
            _LOGGER.error(
                "Unexpected %s while updating Aruba Instant data",
                type(err).__name__,
            )
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_error",
            ) from err

        self.clients = {format_mac(client.mac): client for client in snapshot.clients}
        return snapshot
