"""Common fixtures for Aruba tests."""

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.components.aruba.const import DOMAIN

from .const import CONFIG, MANAGEMENT_ADDRESS, SNAPSHOT

from tests.common import MockConfigEntry


@pytest.fixture
def mock_aruba_client() -> Generator[AsyncMock]:
    """Return a mocked aioarubainstant client."""
    client = AsyncMock()
    client.async_get_snapshot.return_value = SNAPSHOT
    client_class = MagicMock(return_value=client)
    with (
        patch(
            "homeassistant.components.aruba.coordinator.ArubaInstantClient",
            new=client_class,
        ),
        patch(
            "homeassistant.components.aruba.config_flow.ArubaInstantClient",
            new=client_class,
        ),
    ):
        client.client_class = client_class
        yield client


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return an Aruba config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test cluster",
        unique_id=MANAGEMENT_ADDRESS,
        data=CONFIG,
    )
