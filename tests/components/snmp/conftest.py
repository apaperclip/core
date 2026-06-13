"""Conftest for SNMP tests."""

from collections.abc import Generator
import socket
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.components.snmp.coordinator import SnmpClient, SnmpClientInfo

from .const import MAC, SYSTEM_INFO


@pytest.fixture(autouse=True)
def patch_getaddrinfo() -> Generator[None]:
    """Patch getaddrinfo to avoid DNS lookups in SNMP tests."""
    with patch.object(socket, "getaddrinfo"):
        yield


@pytest.fixture
def mock_snmp_client() -> Generator[AsyncMock]:
    """Return a mocked SNMP client."""
    client = AsyncMock(spec=SnmpClient)
    client.async_get_clients.return_value = {MAC: SnmpClientInfo(mac_address=MAC)}
    client.async_get_mac_addresses.return_value = frozenset({MAC})
    client.async_get_system_info.return_value = SYSTEM_INFO
    client.access_points = {}
    with (
        patch(
            "homeassistant.components.snmp.async_create_snmp_client",
            return_value=client,
        ),
        patch(
            "homeassistant.components.snmp.config_flow.async_create_snmp_client",
            return_value=client,
        ),
    ):
        yield client
