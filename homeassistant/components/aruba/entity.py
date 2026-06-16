"""Base entities for Aruba Instant."""

from aioarubainstant import ArubaAccessPoint
from yarl import URL

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo, format_mac
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import ArubaConfigEntry
from .const import MANUFACTURER
from .coordinator import ArubaDataUpdateCoordinator
from .helpers import (
    access_point_device_identifier,
    api_device_identifier,
    virtual_controller_device_identifier,
)


class ArubaApiEntity(CoordinatorEntity[ArubaDataUpdateCoordinator]):
    """Base entity for the combined Aruba controller."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        entry: ArubaConfigEntry,
    ) -> None:
        """Initialize an Aruba API endpoint entity."""
        super().__init__(coordinator)
        host = entry.data[CONF_HOST]
        cluster = coordinator.data.cluster
        self._attr_device_info = DeviceInfo(
            configuration_url=URL.build(
                scheme="https",
                host=host,
                port=entry.data[CONF_PORT],
            ),
            identifiers={
                api_device_identifier(entry.entry_id),
                virtual_controller_device_identifier(entry.entry_id),
            },
            manufacturer=MANUFACTURER,
            name=cluster.name or entry.title,
            sw_version=cluster.version,
        )


class ArubaVirtualControllerEntity(CoordinatorEntity[ArubaDataUpdateCoordinator]):
    """Base entity for the Aruba virtual controller."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        entry: ArubaConfigEntry,
    ) -> None:
        """Initialize an Aruba virtual controller entity."""
        super().__init__(coordinator)
        cluster = coordinator.data.cluster
        host = entry.data[CONF_HOST]
        self._attr_device_info = DeviceInfo(
            configuration_url=URL.build(
                scheme="https",
                host=host,
                port=entry.data[CONF_PORT],
            ),
            identifiers={
                api_device_identifier(entry.entry_id),
                virtual_controller_device_identifier(entry.entry_id),
            },
            manufacturer=MANUFACTURER,
            name=cluster.name or entry.title,
            sw_version=cluster.version,
        )


class ArubaAccessPointEntity(CoordinatorEntity[ArubaDataUpdateCoordinator]):
    """Base entity for an Aruba access point."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        entry: ArubaConfigEntry,
        access_point_id: str,
        access_point: ArubaAccessPoint | None = None,
    ) -> None:
        """Initialize an Aruba access point entity."""
        super().__init__(coordinator)
        self._access_point_id = access_point_id
        connections = (
            {(dr.CONNECTION_NETWORK_MAC, format_mac(access_point.mac))}
            if access_point and access_point.mac
            else set()
        )
        self._attr_device_info = DeviceInfo(
            connections=connections,
            identifiers={
                access_point_device_identifier(entry.entry_id, access_point_id)
            },
            manufacturer=MANUFACTURER,
            model=access_point.model if access_point else None,
            name=access_point.name if access_point else None,
            serial_number=access_point.serial if access_point else None,
            sw_version=access_point.firmware if access_point else None,
            via_device=virtual_controller_device_identifier(entry.entry_id),
        )

    @property
    def access_point(self) -> ArubaAccessPoint | None:
        """Return current information for this access point."""
        return self.coordinator.access_points.get(self._access_point_id)
