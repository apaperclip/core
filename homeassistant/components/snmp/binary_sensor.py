"""Binary sensors for an SNMP host."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SnmpConfigEntry
from .coordinator import SnmpAccessPointInfo, SnmpDataUpdateCoordinator
from .entity import SnmpAccessPointEntity, SnmpHostEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SnmpConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up SNMP connectivity sensors."""
    coordinator = entry.runtime_data
    async_add_entities([SnmpConnectivityBinarySensor(coordinator, entry)])
    tracked_access_points: set[str] = set()

    @callback
    def add_access_point_entities() -> None:
        new_access_points = (
            coordinator.data.access_points.keys() - tracked_access_points
        )
        if not new_access_points:
            return
        tracked_access_points.update(new_access_points)
        async_add_entities(
            SnmpAccessPointConnectivityBinarySensor(
                coordinator, entry, coordinator.data.access_points[mac]
            )
            for mac in sorted(new_access_points)
        )

    add_access_point_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_access_point_entities))


class SnmpConnectivityBinarySensor(SnmpHostEntity, BinarySensorEntity):
    """Represent connectivity to the SNMP host."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "connected"

    def __init__(
        self,
        coordinator: SnmpDataUpdateCoordinator,
        entry: SnmpConfigEntry,
    ) -> None:
        """Initialize the connectivity sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_connected"

    @property
    def available(self) -> bool:
        """Keep reporting connectivity after a failed poll."""
        return True

    @property
    def is_on(self) -> bool:
        """Return whether the latest required SNMP poll succeeded."""
        return self.coordinator.last_update_success


class SnmpAccessPointConnectivityBinarySensor(
    SnmpAccessPointEntity, BinarySensorEntity
):
    """Represent whether an access point is present in the cluster."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "connected"

    def __init__(
        self,
        coordinator: SnmpDataUpdateCoordinator,
        entry: SnmpConfigEntry,
        access_point: SnmpAccessPointInfo,
    ) -> None:
        """Initialize an access point connectivity sensor."""
        super().__init__(coordinator, entry, access_point)
        self._attr_unique_id = f"{entry.entry_id}_{access_point.mac_address}_connected"

    @property
    def is_on(self) -> bool:
        """Return whether the access point is present in the latest poll."""
        return self.access_point is not None
