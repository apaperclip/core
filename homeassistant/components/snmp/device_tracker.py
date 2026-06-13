"""SNMP device tracker platform."""

from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol

from homeassistant.components.device_tracker import (
    CONF_CONSIDER_HOME,
    DEFAULT_CONSIDER_HOME,
    DOMAIN as DEVICE_TRACKER_DOMAIN,
    PLATFORM_SCHEMA as DEVICE_TRACKER_PLATFORM_SCHEMA,
    ScannerEntity,
)
from homeassistant.config_entries import SOURCE_IMPORT
from homeassistant.const import CONF_HOST
from homeassistant.core import (
    CALLBACK_TYPE,
    DOMAIN as HOMEASSISTANT_DOMAIN,
    HomeAssistant,
    callback,
)
from homeassistant.helpers import (
    config_validation as cv,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import SnmpConfigEntry
from .const import (
    CONF_AUTH_KEY,
    CONF_BASEOID,
    CONF_COMMUNITY,
    CONF_PRIV_KEY,
    DEFAULT_COMMUNITY,
    DOMAIN,
)
from .coordinator import SnmpClientInfo, SnmpDataUpdateCoordinator

PLATFORM_SCHEMA = DEVICE_TRACKER_PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_BASEOID): cv.string,
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_COMMUNITY, default=DEFAULT_COMMUNITY): cv.string,
        vol.Inclusive(CONF_AUTH_KEY, "keys"): cv.string,
        vol.Inclusive(CONF_PRIV_KEY, "keys"): cv.string,
    }
)


async def async_import_config(hass: HomeAssistant, config: ConfigType) -> None:
    """Import a legacy SNMP tracker configuration."""
    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_IMPORT},
            data=config,
        )
    )
    ir.async_create_issue(
        hass,
        HOMEASSISTANT_DOMAIN,
        f"deprecated_yaml_{DOMAIN}",
        breaks_in_ha_version="2027.5.0",
        is_fixable=False,
        issue_domain=DOMAIN,
        severity=ir.IssueSeverity.WARNING,
        translation_key="deprecated_yaml",
        translation_placeholders={
            "domain": DOMAIN,
            "integration_title": "SNMP",
        },
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SnmpConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up SNMP device tracker entities."""
    coordinator = entry.runtime_data
    tracked: set[str] = set()

    @callback
    def add_new_entities() -> None:
        new_macs = coordinator.data.mac_addresses - tracked
        if not new_macs:
            return
        tracked.update(new_macs)
        async_add_entities(
            SnmpScannerEntity(coordinator, entry, mac, True) for mac in sorted(new_macs)
        )

    add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_new_entities))

    entity_registry = er.async_get(hass)
    suffix = f"_{entry.entry_id}"
    restored: list[SnmpScannerEntity] = []
    for entity_entry in entity_registry.entities.get_entries_for_config_entry_id(
        entry.entry_id
    ):
        if (
            entity_entry.domain != DEVICE_TRACKER_DOMAIN
            or entity_entry.platform != DOMAIN
            or not entity_entry.unique_id.endswith(suffix)
        ):
            continue
        mac = entity_entry.unique_id.removesuffix(suffix)
        if mac in tracked:
            continue
        tracked.add(mac)
        restored.append(SnmpScannerEntity(coordinator, entry, mac, False))

    async_add_entities(restored)


class SnmpScannerEntity(CoordinatorEntity[SnmpDataUpdateCoordinator], ScannerEntity):
    """Representation of a client found through SNMP."""

    _attr_should_poll = False

    def __init__(
        self,
        coordinator: SnmpDataUpdateCoordinator,
        entry: SnmpConfigEntry,
        mac: str,
        connected: bool,
    ) -> None:
        """Initialize an SNMP scanner entity."""
        super().__init__(coordinator)
        self._attr_mac_address = mac
        self._attr_name = f"SNMP {mac}"
        self._unique_id = f"{mac}_{entry.entry_id}"
        self._client_info: SnmpClientInfo | None = coordinator.data.clients.get(mac)
        self._consider_home = timedelta(
            seconds=entry.options.get(
                CONF_CONSIDER_HOME, DEFAULT_CONSIDER_HOME.total_seconds()
            )
        )
        self._last_seen = dt_util.utcnow() if connected else None
        self._disconnect_timer: CALLBACK_TYPE | None = None

    @property
    def unique_id(self) -> str:
        """Return a unique ID scoped to the config entry."""
        return self._unique_id

    @property
    def is_connected(self) -> bool:
        """Return whether the client is connected or still considered home."""
        return bool(
            self._last_seen is not None
            and dt_util.utcnow() - self._last_seen < self._consider_home
        )

    @property
    def ip_address(self) -> str | None:
        """Return the client IP address."""
        return self._client_info.ip_address if self._client_info else None

    @property
    def hostname(self) -> str | None:
        """Return the discovered client name."""
        return self._client_info.hostname if self._client_info else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return Aruba Instant client connection details."""
        if self._client_info is None:
            return None
        attributes = {
            "bssid": self._client_info.bssid,
            "access_point": self._client_info.access_point,
            "access_point_mac": self._client_info.access_point_mac,
            "phy_type": self._client_info.phy_type,
            "ht_mode": self._client_info.ht_mode,
        }
        return {key: value for key, value in attributes.items() if value is not None}

    @callback
    def _async_cancel_disconnect_timer(self) -> None:
        """Cancel a pending disconnect update."""
        if self._disconnect_timer is not None:
            self._disconnect_timer()
            self._disconnect_timer = None

    @callback
    def _async_mark_disconnected(self, _now: datetime) -> None:
        """Write state after the consider-home interval expires."""
        self._disconnect_timer = None
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle an updated SNMP MAC table."""
        now = dt_util.utcnow()
        if self._attr_mac_address in self.coordinator.data.mac_addresses:
            self._last_seen = now
            if client_info := self.coordinator.data.clients.get(self._attr_mac_address):
                self._client_info = client_info
            self._async_cancel_disconnect_timer()
        elif (
            self._last_seen is not None
            and self._disconnect_timer is None
            and (disconnect_at := self._last_seen + self._consider_home) > now
        ):
            self._disconnect_timer = async_track_point_in_utc_time(
                self.hass, self._async_mark_disconnected, disconnect_at
            )
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """Register entity cleanup."""
        await super().async_added_to_hass()
        self.async_on_remove(self._async_cancel_disconnect_timer)
