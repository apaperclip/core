"""Tests for the SNMP device tracker coordinator."""

from collections.abc import AsyncIterator
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from pysnmp.error import PySnmpError
from pysnmp.proto.rfc1902 import Integer32, ObjectIdentifier, OctetString, TimeTicks
from pysnmp.proto.rfc1905 import NoSuchObject
import pytest

from homeassistant.components.snmp.const import (
    CONF_AUTH_KEY,
    CONF_PRIV_KEY,
    CONF_VERSION,
    DEFAULT_COMMUNITY,
)
from homeassistant.components.snmp.coordinator import (
    ARUBA_CLUSTER_MASTER,
    ARUBA_CLUSTER_SLAVE,
    ARUBA_INSTANT_ACCESS_POINT_ENTRY_OID,
    ARUBA_INSTANT_CLIENT_ENTRY_OID,
    ARUBA_INSTANT_CLIENT_TABLE_OID,
    ARUBA_INSTANT_WLAN_ENTRY_OID,
    SNMP_BULK_MAX_REPETITIONS,
    SnmpAccessPointInfo,
    SnmpClient,
    SnmpClientInfo,
    SnmpSystemInfo,
    SnmpWalkError,
    async_create_snmp_client,
)
from homeassistant.components.snmp.util import RequestArgsType
from homeassistant.core import HomeAssistant

from .const import (
    CONFIG,
    MAC,
    SYSTEM_DESCRIPTION,
    SYSTEM_INFO,
    SYSTEM_NAME,
    SYSTEM_OBJECT_ID,
    SYSTEM_UPTIME,
)

REQUEST_ARGS = cast(RequestArgsType, (object(), object(), object(), object(), object()))


async def _walk_result(
    result: list[tuple[object, object]],
) -> AsyncIterator[tuple[None, None, int, list[tuple[object, object]]]]:
    """Yield one successful walk result."""
    yield None, None, 0, result


async def test_walk_returns_normalized_unique_macs() -> None:
    """Test SNMP values are normalized and de-duplicated."""
    result = [
        (object(), OctetString(hexValue="aabbccddeeff")),
        (object(), OctetString(hexValue="aabbccddeeff")),
        (object(), OctetString(hexValue="aabb")),
        (object(), object()),
    ]
    client = SnmpClient(REQUEST_ARGS)

    with (
        patch(
            "homeassistant.components.snmp.coordinator.bulk_walk_cmd",
            return_value=_walk_result(result),
        ) as mock_bulk_walk,
        patch(
            "homeassistant.components.snmp.coordinator.is_end_of_mib",
            return_value=False,
        ),
    ):
        macs = await client.async_get_mac_addresses()

    assert macs == frozenset({MAC})
    assert mock_bulk_walk.call_args.args[5] == SNMP_BULK_MAX_REPETITIONS


async def test_walk_error_indication() -> None:
    """Test an SNMP error indication raises a walk error."""

    async def walk(*args: Any, **kwargs: Any) -> AsyncIterator[tuple]:
        yield "transport failed", None, 0, []

    client = SnmpClient(REQUEST_ARGS)
    with (
        patch(
            "homeassistant.components.snmp.coordinator.bulk_walk_cmd",
            return_value=walk(),
        ),
        pytest.raises(SnmpWalkError, match="transport failed"),
    ):
        await client.async_get_mac_addresses()


async def test_walk_aruba_clients() -> None:
    """Test Aruba Instant table rows are combined into enriched clients."""
    client_mac = OctetString(hexValue="aabbccddeeff")
    ap_mac = OctetString(hexValue="001122334455")
    ap_mac_2 = OctetString(hexValue="aabbccddeeff")
    bssid = OctetString(hexValue="66778899aabb")
    row_index = (1,)
    row_index_2 = (2,)
    client_rows = [
        (
            ObjectIdentifier((*ARUBA_INSTANT_CLIENT_ENTRY_OID, 1, *row_index)),
            client_mac,
        ),
        (
            ObjectIdentifier((*ARUBA_INSTANT_CLIENT_ENTRY_OID, 2, *row_index)),
            bssid,
        ),
        (
            ObjectIdentifier((*ARUBA_INSTANT_CLIENT_ENTRY_OID, 3, *row_index)),
            OctetString(hexValue="c0a801b5"),
        ),
        (
            ObjectIdentifier((*ARUBA_INSTANT_CLIENT_ENTRY_OID, 5, *row_index)),
            OctetString("laptop"),
        ),
        (
            ObjectIdentifier((*ARUBA_INSTANT_CLIENT_ENTRY_OID, 17, *row_index)),
            Integer32(3),
        ),
        (
            ObjectIdentifier((*ARUBA_INSTANT_CLIENT_ENTRY_OID, 18, *row_index)),
            Integer32(2),
        ),
    ]
    ap_rows = [
        (
            ObjectIdentifier((*ARUBA_INSTANT_ACCESS_POINT_ENTRY_OID, 1, *row_index)),
            ap_mac,
        ),
        (
            ObjectIdentifier((*ARUBA_INSTANT_ACCESS_POINT_ENTRY_OID, 2, *row_index)),
            OctetString("Office AP"),
        ),
        (
            ObjectIdentifier((*ARUBA_INSTANT_ACCESS_POINT_ENTRY_OID, 13, *row_index)),
            OctetString(" Cluster Master "),
        ),
        (
            ObjectIdentifier((*ARUBA_INSTANT_ACCESS_POINT_ENTRY_OID, 1, *row_index_2)),
            ap_mac_2,
        ),
        (
            ObjectIdentifier((*ARUBA_INSTANT_ACCESS_POINT_ENTRY_OID, 2, *row_index_2)),
            OctetString("Lobby AP"),
        ),
        (
            ObjectIdentifier((*ARUBA_INSTANT_ACCESS_POINT_ENTRY_OID, 13, *row_index_2)),
            OctetString("CLUSTER SLAVE"),
        ),
    ]
    wlan_rows = [
        (
            ObjectIdentifier((*ARUBA_INSTANT_WLAN_ENTRY_OID, 1, *row_index)),
            ap_mac,
        ),
        (
            ObjectIdentifier((*ARUBA_INSTANT_WLAN_ENTRY_OID, 4, *row_index)),
            bssid,
        ),
    ]
    client = SnmpClient(
        REQUEST_ARGS, ".".join(map(str, ARUBA_INSTANT_CLIENT_TABLE_OID))
    )

    with patch.object(
        client,
        "_async_walk_columns",
        new=AsyncMock(side_effect=[client_rows, ap_rows, wlan_rows]),
    ) as mock_walk_columns:
        clients = await client.async_get_clients()

    assert clients == {
        MAC: SnmpClientInfo(
            mac_address=MAC,
            ip_address="192.168.1.181",
            hostname="laptop",
            bssid="66:77:88:99:aa:bb",
            access_point="Office AP",
            access_point_mac="00:11:22:33:44:55",
            phy_type="802.11g",
            ht_mode="HT20",
        )
    }
    assert client.access_points == {
        "00:11:22:33:44:55": SnmpAccessPointInfo(
            mac_address="00:11:22:33:44:55",
            name="Office AP",
            role=ARUBA_CLUSTER_MASTER,
            connected_clients=1,
        ),
        "aa:bb:cc:dd:ee:ff": SnmpAccessPointInfo(
            mac_address="aa:bb:cc:dd:ee:ff",
            name="Lobby AP",
            role=ARUBA_CLUSTER_SLAVE,
            connected_clients=0,
        ),
    }
    assert [call.args for call in mock_walk_columns.await_args_list] == [
        (ARUBA_INSTANT_CLIENT_ENTRY_OID, (1, 2, 3, 5, 17, 18)),
        (ARUBA_INSTANT_ACCESS_POINT_ENTRY_OID, (1, 2, 13)),
        (ARUBA_INSTANT_WLAN_ENTRY_OID, (1, 4)),
    ]


async def test_walk_error_status() -> None:
    """Test an SNMP error status raises a walk error with its OID."""
    error_status = MagicMock()
    error_status.__bool__.return_value = True
    error_status.prettyPrint.return_value = "authorizationError"

    async def walk(*args: Any, **kwargs: Any) -> AsyncIterator[tuple]:
        yield None, error_status, 1, [("1.2.3", object())]

    client = SnmpClient(REQUEST_ARGS)
    with (
        patch(
            "homeassistant.components.snmp.coordinator.bulk_walk_cmd",
            return_value=walk(),
        ),
        pytest.raises(SnmpWalkError, match="authorizationError at 1.2.3"),
    ):
        await client.async_get_mac_addresses()


async def test_create_v1_client(hass: HomeAssistant) -> None:
    """Test the legacy community authentication behavior is preserved."""
    target = MagicMock()
    auth_data = MagicMock()
    with (
        patch(
            "homeassistant.components.snmp.coordinator.UdpTransportTarget.create",
            new=AsyncMock(return_value=target),
        ),
        patch(
            "homeassistant.components.snmp.coordinator.CommunityData",
            return_value=auth_data,
        ) as mock_community_data,
        patch(
            "homeassistant.components.snmp.coordinator.async_create_request_cmd_args",
            new=AsyncMock(return_value=REQUEST_ARGS),
        ) as mock_create_args,
    ):
        client = await async_create_snmp_client(hass, CONFIG)

    assert isinstance(client, SnmpClient)
    mock_community_data.assert_called_once_with(DEFAULT_COMMUNITY, mpModel=0)
    mock_create_args.assert_awaited_once_with(
        hass, auth_data, target, CONFIG["baseoid"]
    )


async def test_create_v2c_client(hass: HomeAssistant) -> None:
    """Test SNMPv2c community authentication."""
    target = MagicMock()
    auth_data = MagicMock()
    config = {**CONFIG, CONF_VERSION: "2c"}
    with (
        patch(
            "homeassistant.components.snmp.coordinator.UdpTransportTarget.create",
            new=AsyncMock(return_value=target),
        ),
        patch(
            "homeassistant.components.snmp.coordinator.CommunityData",
            return_value=auth_data,
        ) as mock_community_data,
        patch(
            "homeassistant.components.snmp.coordinator.async_create_request_cmd_args",
            new=AsyncMock(return_value=REQUEST_ARGS),
        ),
    ):
        await async_create_snmp_client(hass, config)

    mock_community_data.assert_called_once_with(DEFAULT_COMMUNITY, mpModel=1)


async def test_create_v3_client(hass: HomeAssistant) -> None:
    """Test the legacy v3 key and protocol behavior is preserved."""
    target = MagicMock()
    auth_data = MagicMock()
    config = {**CONFIG, CONF_AUTH_KEY: "auth-key", CONF_PRIV_KEY: "priv-key"}
    with (
        patch(
            "homeassistant.components.snmp.coordinator.UdpTransportTarget.create",
            new=AsyncMock(return_value=target),
        ),
        patch(
            "homeassistant.components.snmp.coordinator.UsmUserData",
            return_value=auth_data,
        ) as mock_user_data,
        patch(
            "homeassistant.components.snmp.coordinator.async_create_request_cmd_args",
            new=AsyncMock(return_value=REQUEST_ARGS),
        ),
    ):
        client = await async_create_snmp_client(hass, config)

    assert isinstance(client, SnmpClient)
    mock_user_data.assert_called_once_with(
        DEFAULT_COMMUNITY,
        authKey="auth-key",
        privKey="priv-key",
        authProtocol="none",
        privProtocol="none",
    )


async def test_ipv6_fallback(hass: HomeAssistant) -> None:
    """Test client creation falls back to an IPv6 transport target."""
    target = MagicMock()
    with (
        patch(
            "homeassistant.components.snmp.coordinator.UdpTransportTarget.create",
            new=AsyncMock(side_effect=PySnmpError("not IPv4")),
        ),
        patch(
            "homeassistant.components.snmp.coordinator.Udp6TransportTarget.create",
            new=AsyncMock(return_value=target),
        ) as mock_ipv6_create,
        patch(
            "homeassistant.components.snmp.coordinator.async_create_request_cmd_args",
            new=AsyncMock(return_value=REQUEST_ARGS),
        ),
    ):
        await async_create_snmp_client(hass, CONFIG)

    mock_ipv6_create.assert_awaited_once_with((CONFIG["host"], "161"), timeout=8)


async def test_get_system_info() -> None:
    """Test standard system OIDs are decoded when supported."""
    result = [
        (object(), OctetString(SYSTEM_DESCRIPTION)),
        (object(), ObjectIdentifier(SYSTEM_OBJECT_ID)),
        (object(), TimeTicks(int(SYSTEM_UPTIME * 100))),
        (object(), OctetString(SYSTEM_NAME)),
    ]
    client = SnmpClient(REQUEST_ARGS)

    with patch(
        "homeassistant.components.snmp.coordinator.get_cmd",
        new=AsyncMock(return_value=(None, None, 0, result)),
    ):
        system_info = await client.async_get_system_info()

    assert system_info == SYSTEM_INFO


async def test_get_system_info_unsupported() -> None:
    """Test unsupported standard system OIDs return empty optional data."""
    result = [(object(), NoSuchObject()) for _key in range(4)]
    client = SnmpClient(REQUEST_ARGS)

    with patch(
        "homeassistant.components.snmp.coordinator.get_cmd",
        new=AsyncMock(return_value=(None, None, 0, result)),
    ):
        system_info = await client.async_get_system_info()

    assert system_info == SnmpSystemInfo()


async def test_get_system_info_error() -> None:
    """Test optional system OID errors do not raise."""
    client = SnmpClient(REQUEST_ARGS)
    with patch(
        "homeassistant.components.snmp.coordinator.get_cmd",
        new=AsyncMock(return_value=(PySnmpError("failed"), None, 0, [])),
    ):
        system_info = await client.async_get_system_info()

    assert system_info == SnmpSystemInfo()
