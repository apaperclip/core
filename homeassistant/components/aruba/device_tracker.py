"""Device tracker platform for Aruba Instant."""

from aioarubainstant import ArubaClient
import voluptuous as vol

from homeassistant.components.device_tracker import (
    DOMAIN as DEVICE_TRACKER_DOMAIN,
    PLATFORM_SCHEMA as DEVICE_TRACKER_PLATFORM_SCHEMA,
    ScannerEntity,
)
from homeassistant.config_entries import SOURCE_IMPORT
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import (
    config_validation as cv,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import ArubaConfigEntry
from .const import DOMAIN
from .coordinator import ArubaDataUpdateCoordinator

PLATFORM_SCHEMA = DEVICE_TRACKER_PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Required(CONF_USERNAME): cv.string,
    }
)


async def async_import_config(hass: HomeAssistant, config: ConfigType) -> None:
    """Import a legacy Aruba device tracker configuration."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_IMPORT},
        data={
            CONF_HOST: config[CONF_HOST],
            CONF_USERNAME: config[CONF_USERNAME],
            CONF_PASSWORD: config[CONF_PASSWORD],
        },
    )
    import_failed = (
        result["type"] is FlowResultType.ABORT
        and result["reason"] != "already_configured"
    )
    ir.async_create_issue(
        hass,
        DOMAIN,
        "deprecated_yaml",
        breaks_in_ha_version="2027.5.0",
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=(
            "deprecated_yaml_import_failed" if import_failed else "deprecated_yaml"
        ),
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ArubaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Aruba client tracker entities."""
    coordinator = entry.runtime_data
    tracked: set[str] = set()
    unique_id_prefix = f"{entry.unique_id}_"

    @callback
    def add_new_entities() -> None:
        new_macs = set(coordinator.clients) - tracked
        if not new_macs:
            return
        tracked.update(new_macs)
        async_add_entities(
            ArubaScannerEntity(coordinator, entry, mac, coordinator.clients[mac])
            for mac in sorted(new_macs)
        )

    add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_new_entities))

    entity_registry = er.async_get(hass)
    restored: list[ArubaScannerEntity] = []
    for entity_entry in entity_registry.entities.get_entries_for_config_entry_id(
        entry.entry_id
    ):
        if (
            entity_entry.domain != DEVICE_TRACKER_DOMAIN
            or entity_entry.platform != DOMAIN
            or not entity_entry.unique_id.startswith(unique_id_prefix)
        ):
            continue
        mac = entity_entry.unique_id.removeprefix(unique_id_prefix)
        if mac in tracked:
            continue
        tracked.add(mac)
        restored.append(ArubaScannerEntity(coordinator, entry, mac))

    async_add_entities(restored)


class ArubaScannerEntity(CoordinatorEntity[ArubaDataUpdateCoordinator], ScannerEntity):
    """Representation of a client connected to Aruba Instant."""

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        entry: ArubaConfigEntry,
        mac: str,
        client: ArubaClient | None = None,
    ) -> None:
        """Initialize an Aruba client tracker."""
        super().__init__(coordinator)
        self._mac = mac
        self._unique_id = f"{entry.unique_id}_{mac}"
        self._attr_mac_address = mac
        self._update_client(client)

    @property
    def unique_id(self) -> str:
        """Return a unique ID scoped to the Aruba cluster."""
        return self._unique_id

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Enable discovered trackers to preserve legacy behavior."""
        return True

    @property
    def is_connected(self) -> bool:
        """Return whether the client is in the latest successful snapshot."""
        return self._mac in self.coordinator.clients

    @callback
    def _update_client(self, client: ArubaClient | None) -> None:
        """Update the last known client details."""
        if client is None:
            self._attr_name = f"Aruba {self._mac}"
            self._attr_hostname = None
            self._attr_ip_address = None
            return
        self._attr_name = client.hostname or f"Aruba {self._mac}"
        self._attr_hostname = client.hostname
        self._attr_ip_address = client.ip_address

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle an updated Aruba snapshot."""
        if client := self.coordinator.clients.get(self._mac):
            self._update_client(client)
        super()._handle_coordinator_update()
