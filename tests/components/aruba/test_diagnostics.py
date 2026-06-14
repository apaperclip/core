"""Tests for Aruba diagnostics."""

from unittest.mock import AsyncMock

from homeassistant.components.aruba.diagnostics import (
    async_get_config_entry_diagnostics,
)
from homeassistant.core import HomeAssistant

from .const import CONFIG, SNAPSHOT

from tests.common import MockConfigEntry


async def test_diagnostics(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test diagnostics redact private controller data."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    serialized = str(diagnostics)

    assert diagnostics["cluster"]["client_count"] == SNAPSHOT.cluster.client_count
    assert diagnostics["parsed_client_count"] == len(SNAPSHOT.clients)
    assert diagnostics["access_points"][0]["connected_clients"] == 1
    assert CONFIG["password"] not in serialized
    assert CONFIG["username"] not in serialized
    assert CONFIG["host"] not in serialized
    assert "private controller output" not in serialized
    assert "_raw_output" not in serialized
    assert "aa:bb:cc:dd:ee:ff" not in serialized
