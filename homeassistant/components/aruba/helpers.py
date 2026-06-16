"""Helpers for Aruba Instant devices."""

from aioarubainstant import ArubaAccessPoint

from homeassistant.helpers.device_registry import format_mac

from .const import DOMAIN


def api_device_identifier(entry_id: str) -> tuple[str, str]:
    """Return the API endpoint device identifier."""
    return (DOMAIN, f"{entry_id}_api")


def virtual_controller_device_identifier(entry_id: str) -> tuple[str, str]:
    """Return the virtual controller device identifier."""
    return (DOMAIN, f"{entry_id}_virtual_controller")


def access_point_key(access_point: ArubaAccessPoint) -> str | None:
    """Return a stable key for an access point."""
    if access_point.mac and (mac := access_point.mac.strip()):
        return f"mac:{format_mac(mac)}"
    if access_point.serial and (serial := access_point.serial.strip()):
        return f"serial:{serial}"
    return None


def access_point_device_identifier(
    entry_id: str, access_point_id: str
) -> tuple[str, str]:
    """Return an access point device identifier."""
    return (DOMAIN, f"{entry_id}_ap_{access_point_id}")
