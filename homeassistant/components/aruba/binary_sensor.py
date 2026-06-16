"""Binary sensors for Aruba Instant."""

from aioarubainstant import ArubaAccessPoint

from homeassistant.components.binary_sensor import (
    DOMAIN as BINARY_SENSOR_DOMAIN,
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ArubaConfigEntry
from .coordinator import ArubaDataUpdateCoordinator
from .entity import ArubaAccessPointEntity, ArubaApiEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ArubaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Aruba connectivity sensors."""
    coordinator = entry.runtime_data
    async_add_entities([ArubaApiConnectivityBinarySensor(coordinator, entry)])

    tracked: set[str] = set()

    @callback
    def add_access_point_entities() -> None:
        new_access_points = coordinator.access_points.keys() - tracked
        if not new_access_points:
            return
        tracked.update(new_access_points)
        async_add_entities(
            ArubaAccessPointConnectivityBinarySensor(
                coordinator,
                entry,
                access_point_id,
                coordinator.access_points[access_point_id],
            )
            for access_point_id in sorted(new_access_points)
        )

    add_access_point_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_access_point_entities))

    unique_id_prefix = f"{entry.entry_id}_ap_"
    unique_id_suffix = "_connected"
    restored: list[ArubaAccessPointConnectivityBinarySensor] = []
    for entity_entry in er.async_get(hass).entities.get_entries_for_config_entry_id(
        entry.entry_id
    ):
        if (
            entity_entry.domain != BINARY_SENSOR_DOMAIN
            or entity_entry.platform != entry.domain
            or not entity_entry.unique_id.startswith(unique_id_prefix)
            or not entity_entry.unique_id.endswith(unique_id_suffix)
        ):
            continue
        access_point_id = entity_entry.unique_id.removeprefix(
            unique_id_prefix
        ).removesuffix(unique_id_suffix)
        if not access_point_id or access_point_id in tracked:
            continue
        tracked.add(access_point_id)
        restored.append(
            ArubaAccessPointConnectivityBinarySensor(
                coordinator,
                entry,
                access_point_id,
                coordinator.access_points.get(access_point_id),
            )
        )

    async_add_entities(restored)


class ArubaApiConnectivityBinarySensor(ArubaApiEntity, BinarySensorEntity):
    """Represent connectivity to the Aruba API endpoint."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "connected"

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        entry: ArubaConfigEntry,
    ) -> None:
        """Initialize Aruba API connectivity."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_api_connected"

    @property
    def available(self) -> bool:
        """Keep reporting API connectivity after a failed poll."""
        return True

    @property
    def is_on(self) -> bool:
        """Return whether the latest API update succeeded."""
        return self.coordinator.last_update_success


class ArubaAccessPointConnectivityBinarySensor(
    ArubaAccessPointEntity, BinarySensorEntity
):
    """Represent whether an access point is present in the latest snapshot."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "connected"

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        entry: ArubaConfigEntry,
        access_point_id: str,
        access_point: ArubaAccessPoint | None = None,
    ) -> None:
        """Initialize access point connectivity."""
        super().__init__(coordinator, entry, access_point_id, access_point)
        self._attr_unique_id = f"{entry.entry_id}_ap_{access_point_id}_connected"

    @property
    def is_on(self) -> bool:
        """Return whether the access point is in the latest snapshot."""
        return self.access_point is not None
