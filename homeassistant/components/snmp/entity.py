"""Base entities for SNMP config entries."""

from homeassistant.const import CONF_HOST
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SnmpConfigEntry
from .const import CONF_BASEOID, DOMAIN
from .coordinator import (
    ARUBA_INSTANT_CLIENT_OIDS,
    SnmpAccessPointInfo,
    SnmpDataUpdateCoordinator,
)


class SnmpHostEntity(CoordinatorEntity[SnmpDataUpdateCoordinator]):
    """Base entity for the polled SNMP host."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SnmpDataUpdateCoordinator,
        entry: SnmpConfigEntry,
    ) -> None:
        """Initialize an SNMP host entity."""
        super().__init__(coordinator)
        system_info = coordinator.data.system_info
        is_aruba_cluster = (
            entry.data[CONF_BASEOID].strip(".") in ARUBA_INSTANT_CLIENT_OIDS
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title
            if is_aruba_cluster
            else system_info.name or entry.data[CONF_HOST],
            manufacturer="Aruba" if is_aruba_cluster else None,
            model=system_info.description,
            model_id=system_info.object_id,
        )


class SnmpAccessPointEntity(CoordinatorEntity[SnmpDataUpdateCoordinator]):
    """Base entity for an Aruba Instant access point."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SnmpDataUpdateCoordinator,
        entry: SnmpConfigEntry,
        access_point: SnmpAccessPointInfo,
    ) -> None:
        """Initialize an Aruba Instant access point entity."""
        super().__init__(coordinator)
        self._access_point_mac = access_point.mac_address
        self._attr_device_info = DeviceInfo(
            connections={(dr.CONNECTION_NETWORK_MAC, access_point.mac_address)},
            identifiers={(DOMAIN, f"{entry.entry_id}_{access_point.mac_address}")},
            manufacturer="Aruba",
            name=access_point.name,
            via_device=(DOMAIN, entry.entry_id),
        )

    @property
    def access_point(self) -> SnmpAccessPointInfo | None:
        """Return current information for this access point."""
        return self.coordinator.data.access_points.get(self._access_point_mac)
