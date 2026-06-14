"""Diagnostics support for Aruba Instant."""

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import ArubaConfigEntry

CONFIG_REDACT = {CONF_HOST, CONF_PASSWORD, CONF_USERNAME}
CLUSTER_REDACT = {"management_address", "master_ap", "name"}
ACCESS_POINT_REDACT = {"ip_address", "mac", "name", "serial"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ArubaConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for an Aruba config entry."""
    coordinator = entry.runtime_data
    result: dict[str, Any] = {
        "config": async_redact_data(entry.data, CONFIG_REDACT),
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_exception": (
                type(coordinator.last_exception).__name__
                if coordinator.last_exception
                else None
            ),
        },
    }
    snapshot = coordinator.data
    result["cluster"] = async_redact_data(asdict(snapshot.cluster), CLUSTER_REDACT)
    result["access_points"] = [
        async_redact_data(asdict(access_point), ACCESS_POINT_REDACT)
        for access_point in snapshot.access_points
    ]
    result["parsed_client_count"] = len(snapshot.clients)
    return result
