"""Tests for Aruba config entry setup."""

from unittest.mock import AsyncMock

from aioarubainstant import ArubaInstantAuthenticationError, ArubaInstantConnectionError

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .const import SNAPSHOT

from tests.common import MockConfigEntry


async def test_setup_and_unload(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test setup, first refresh, and unload."""
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert mock_config_entry.runtime_data.data == SNAPSHOT
    mock_aruba_client.async_get_snapshot.assert_awaited_once_with()

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    mock_aruba_client.async_close.assert_awaited_once_with()


async def test_setup_retry_and_cleanup(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test connection failures retry setup and clean up the client."""
    mock_aruba_client.async_get_snapshot.side_effect = ArubaInstantConnectionError(
        "connection failed"
    )
    mock_config_entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    mock_aruba_client.async_close.assert_awaited_once_with()


async def test_setup_auth_failure(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test authentication failure starts reauthentication."""
    mock_aruba_client.async_get_snapshot.side_effect = ArubaInstantAuthenticationError(
        "bad credentials"
    )
    mock_config_entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert any(
        flow["context"]["source"] == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )
    mock_aruba_client.async_close.assert_awaited_once_with()
