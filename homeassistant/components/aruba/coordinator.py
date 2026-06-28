"""Data update coordinator for Aruba Instant."""

import logging

from aioarubainstant import (
    ArubaAccessPoint,
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
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, SCAN_INTERVAL
from .helpers import access_point_key
from .ssl import get_ssl_context

_LOGGER = logging.getLogger(__name__)


class ArubaDataUpdateCoordinator(DataUpdateCoordinator[ArubaInstantSnapshot]):
    """Coordinate Aruba Instant snapshots."""

    access_points: dict[str, ArubaAccessPoint]
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
            verify_ssl=get_ssl_context(config_entry.data[CONF_VERIFY_SSL]),
        )
        self.access_points = {}
        self.clients = {}

    @property
    def master_access_point_name(self) -> str | None:
        """Return the current master access point name when known."""
        if master_ap := self.data.cluster.master_ap:
            return master_ap
        master = next(
            (
                access_point
                for access_point in self.access_points.values()
                if access_point.is_master
            ),
            None,
        )
        if master is None:
            return None
        return master.name or master.ip_address or master.mac

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
            _LOGGER.debug(
                "Invalid response from Aruba controller: %s",
                err,
                exc_info=True,
            )
            try:
                await self.client.async_logout()
            except ArubaInstantError:
                _LOGGER.debug("Error resetting the Aruba controller session")
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

        self.access_points = {
            key: access_point
            for access_point in snapshot.access_points
            if (key := access_point_key(access_point)) is not None
        }
        self.clients = {format_mac(client.mac): client for client in snapshot.clients}
        return snapshot
