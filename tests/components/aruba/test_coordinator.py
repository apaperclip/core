"""Tests for the Aruba coordinator."""

from unittest.mock import AsyncMock

from aioarubainstant import (
    ArubaInstantAuthenticationError,
    ArubaInstantCommandError,
    ArubaInstantConnectionError,
    ArubaInstantError,
    ArubaInstantNotMasterError,
    ArubaInstantParseError,
    ArubaInstantRestDisabledError,
)
import pytest

from homeassistant.components.aruba.coordinator import ArubaDataUpdateCoordinator
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import SNAPSHOT, ZERO_CLIENT_SNAPSHOT

from tests.common import MockConfigEntry


@pytest.mark.parametrize(
    ("error", "expected_error"),
    [
        pytest.param(
            ArubaInstantAuthenticationError("auth"),
            ConfigEntryAuthFailed,
            id="authentication",
        ),
        pytest.param(
            ArubaInstantConnectionError("connection"),
            UpdateFailed,
            id="connection",
        ),
        pytest.param(
            ArubaInstantRestDisabledError("rest"), UpdateFailed, id="rest-disabled"
        ),
        pytest.param(
            ArubaInstantNotMasterError("master"), UpdateFailed, id="not-master"
        ),
        pytest.param(ArubaInstantCommandError("command"), UpdateFailed, id="command"),
        pytest.param(ArubaInstantParseError("parse"), UpdateFailed, id="parse"),
        pytest.param(ArubaInstantError("other"), UpdateFailed, id="other"),
    ],
)
async def test_exception_mapping(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    error: Exception,
    expected_error: type[Exception],
) -> None:
    """Test coordinator exception mapping."""
    mock_aruba_client.async_get_snapshot.side_effect = error
    coordinator = ArubaDataUpdateCoordinator(hass, mock_config_entry)

    with pytest.raises(expected_error):
        await coordinator._async_update_data()


async def test_zero_client_snapshot(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test an explicit zero-client snapshot clears the client lookup."""
    coordinator = ArubaDataUpdateCoordinator(hass, mock_config_entry)
    await coordinator._async_update_data()
    assert coordinator.clients

    mock_aruba_client.async_get_snapshot.return_value = ZERO_CLIENT_SNAPSHOT
    snapshot = await coordinator._async_update_data()

    assert snapshot.cluster.client_count == 0
    assert coordinator.clients == {}


async def test_reported_count_is_not_derived(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test the controller-reported count is retained."""
    mismatched_snapshot = SNAPSHOT.__class__(
        cluster=SNAPSHOT.cluster.__class__(
            management_address=SNAPSHOT.cluster.management_address,
            client_count=8,
        ),
        access_points=SNAPSHOT.access_points,
        clients=SNAPSHOT.clients,
        _raw_output={},
    )
    mock_aruba_client.async_get_snapshot.return_value = mismatched_snapshot
    coordinator = ArubaDataUpdateCoordinator(hass, mock_config_entry)

    snapshot = await coordinator._async_update_data()

    assert snapshot.cluster.client_count == 8
    assert len(coordinator.clients) == 1


async def test_unexpected_error_does_not_log_details(
    hass: HomeAssistant,
    mock_aruba_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test unexpected errors do not expose their message."""
    mock_aruba_client.async_get_snapshot.side_effect = RuntimeError(
        "test-password should not be logged"
    )
    coordinator = ArubaDataUpdateCoordinator(hass, mock_config_entry)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    assert "RuntimeError" in caplog.text
    assert "test-password" not in caplog.text
