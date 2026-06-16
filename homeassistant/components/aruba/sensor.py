"""Sensors for Aruba Instant."""

from collections.abc import Callable
from dataclasses import dataclass

from aioarubainstant import ArubaAccessPoint, ArubaInstantSnapshot

from homeassistant.components.sensor import (
    DOMAIN as SENSOR_DOMAIN,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import ArubaConfigEntry
from .coordinator import ArubaDataUpdateCoordinator
from .entity import ArubaAccessPointEntity, ArubaVirtualControllerEntity
from .helpers import virtual_controller_device_identifier

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class ArubaSnapshotSensorEntityDescription(SensorEntityDescription):
    """Describe an Aruba snapshot sensor."""

    value_fn: Callable[[ArubaInstantSnapshot], StateType]


@dataclass(frozen=True, kw_only=True)
class ArubaAccessPointSensorEntityDescription(SensorEntityDescription):
    """Describe an Aruba access point sensor."""

    value_fn: Callable[[ArubaAccessPoint], StateType]


VIRTUAL_CONTROLLER_SENSORS: tuple[ArubaSnapshotSensorEntityDescription, ...] = (
    ArubaSnapshotSensorEntityDescription(
        key="connected_clients",
        translation_key="connected_clients",
        icon="mdi:account-multiple",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda snapshot: snapshot.cluster.client_count,
    ),
    ArubaSnapshotSensorEntityDescription(
        key="master_access_point",
        translation_key="master_access_point",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:access-point-network",
        value_fn=lambda snapshot: snapshot.cluster.master_ap,
    ),
    ArubaSnapshotSensorEntityDescription(
        key="access_points",
        translation_key="access_points",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:access-point-network",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda snapshot: snapshot.cluster.ap_count,
    ),
)

ACCESS_POINT_SENSORS: tuple[ArubaAccessPointSensorEntityDescription, ...] = (
    ArubaAccessPointSensorEntityDescription(
        key="connected_clients",
        translation_key="connected_clients",
        icon="mdi:account-multiple",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda access_point: access_point.connected_clients,
    ),
    ArubaAccessPointSensorEntityDescription(
        key="ip_address",
        translation_key="ip_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:ip-network",
        value_fn=lambda access_point: access_point.ip_address,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ArubaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Aruba sensors."""
    coordinator = entry.runtime_data
    entity_registry = er.async_get(hass)
    if obsolete_entity_id := entity_registry.async_get_entity_id(
        SENSOR_DOMAIN,
        entry.domain,
        f"{entry.entry_id}_virtual_controller_ip_address",
    ):
        entity_registry.async_remove(obsolete_entity_id)

    async_add_entities(
        ArubaVirtualControllerSensor(coordinator, entry, description)
        for description in VIRTUAL_CONTROLLER_SENSORS
    )

    tracked: set[tuple[str, str]] = set()
    controller_tracked: set[str] = set()

    @callback
    def add_access_point_entities() -> None:
        new_entities: list[SensorEntity] = []
        for access_point_id, access_point in coordinator.access_points.items():
            for description in ACCESS_POINT_SENSORS:
                if (access_point_id, description.key) in tracked:
                    continue
                tracked.add((access_point_id, description.key))
                new_entities.append(
                    ArubaAccessPointSensor(
                        coordinator,
                        entry,
                        access_point_id,
                        description,
                        access_point,
                    )
                )
            if access_point_id not in controller_tracked:
                controller_tracked.add(access_point_id)
                new_entities.append(
                    ArubaControllerAccessPointClientsSensor(
                        coordinator,
                        entry,
                        access_point_id,
                        access_point,
                    )
                )
        if not new_entities:
            return
        async_add_entities(new_entities)

    add_access_point_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_access_point_entities))

    unique_id_prefix = f"{entry.entry_id}_ap_"
    restored: list[ArubaAccessPointSensor] = []
    for entity_entry in entity_registry.entities.get_entries_for_config_entry_id(
        entry.entry_id
    ):
        if (
            entity_entry.domain != SENSOR_DOMAIN
            or entity_entry.platform != entry.domain
            or not entity_entry.unique_id.startswith(unique_id_prefix)
        ):
            continue
        for description in ACCESS_POINT_SENSORS:
            suffix = f"_{description.key}"
            if not entity_entry.unique_id.endswith(suffix):
                continue
            access_point_id = entity_entry.unique_id.removeprefix(
                unique_id_prefix
            ).removesuffix(suffix)
            entity_key = (access_point_id, description.key)
            if not access_point_id or entity_key in tracked:
                break
            tracked.add(entity_key)
            restored.append(
                ArubaAccessPointSensor(
                    coordinator,
                    entry,
                    access_point_id,
                    description,
                    coordinator.access_points.get(access_point_id),
                )
            )
            break

    async_add_entities(restored)

    unique_id_prefix = f"{entry.entry_id}_virtual_controller_ap_"
    controller_restored: list[ArubaControllerAccessPointClientsSensor] = []
    for entity_entry in entity_registry.entities.get_entries_for_config_entry_id(
        entry.entry_id
    ):
        if (
            entity_entry.domain != SENSOR_DOMAIN
            or entity_entry.platform != entry.domain
            or not entity_entry.unique_id.startswith(unique_id_prefix)
            or not entity_entry.unique_id.endswith("_connected_clients")
        ):
            continue
        access_point_id = entity_entry.unique_id.removeprefix(
            unique_id_prefix
        ).removesuffix("_connected_clients")
        if not access_point_id or access_point_id in controller_tracked:
            continue
        controller_tracked.add(access_point_id)
        controller_restored.append(
            ArubaControllerAccessPointClientsSensor(
                coordinator,
                entry,
                access_point_id,
                coordinator.access_points.get(access_point_id),
            )
        )

    async_add_entities(controller_restored)


class ArubaVirtualControllerSensor(ArubaVirtualControllerEntity, SensorEntity):
    """Represent an Aruba virtual controller statistic."""

    entity_description: ArubaSnapshotSensorEntityDescription

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        entry: ArubaConfigEntry,
        description: ArubaSnapshotSensorEntityDescription,
    ) -> None:
        """Initialize an Aruba virtual controller sensor."""
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_virtual_controller_{description.key}"

    @property
    def available(self) -> bool:
        """Return whether the statistic is available."""
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> StateType:
        """Return the statistic value."""
        if self.entity_description.key == "master_access_point":
            return self.coordinator.master_access_point_name
        return self.entity_description.value_fn(self.coordinator.data)


class ArubaAccessPointSensor(ArubaAccessPointEntity, SensorEntity):
    """Represent an Aruba access point statistic."""

    entity_description: ArubaAccessPointSensorEntityDescription

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        entry: ArubaConfigEntry,
        access_point_id: str,
        description: ArubaAccessPointSensorEntityDescription,
        access_point: ArubaAccessPoint | None = None,
    ) -> None:
        """Initialize an Aruba access point sensor."""
        super().__init__(coordinator, entry, access_point_id, access_point)
        self.entity_description = description
        self._attr_unique_id = (
            f"{entry.entry_id}_ap_{access_point_id}_{description.key}"
        )

    @property
    def available(self) -> bool:
        """Return whether the access point statistic is available."""
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> StateType:
        """Return the access point statistic."""
        if (access_point := self.access_point) is None:
            return None
        return self.entity_description.value_fn(access_point)


class ArubaControllerAccessPointClientsSensor(ArubaAccessPointEntity, SensorEntity):
    """Represent an access point client count on the virtual controller device."""

    _attr_icon = "mdi:account-multiple"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        entry: ArubaConfigEntry,
        access_point_id: str,
        access_point: ArubaAccessPoint | None = None,
    ) -> None:
        """Initialize an Aruba access point client count on the controller."""
        super().__init__(coordinator, entry, access_point_id, access_point)
        self._attr_device_info = DeviceInfo(
            identifiers={virtual_controller_device_identifier(entry.entry_id)}
        )
        self._attr_has_entity_name = False
        self._attr_name = (
            f"{_access_point_name(access_point_id, access_point)} connected clients"
        )
        self._attr_unique_id = (
            f"{entry.entry_id}_virtual_controller_ap_{access_point_id}"
            "_connected_clients"
        )

    @property
    def available(self) -> bool:
        """Return whether the access point statistic is available."""
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> StateType:
        """Return the access point connected client count."""
        if (access_point := self.access_point) is None:
            return None
        return access_point.connected_clients


def _access_point_name(
    access_point_id: str, access_point: ArubaAccessPoint | None
) -> str:
    """Return the best available access point name."""
    if access_point is None:
        return access_point_id
    return (
        access_point.name
        or access_point.serial
        or access_point.mac
        or access_point.ip_address
        or access_point_id
    )
