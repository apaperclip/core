"""Data update coordinator for SNMP device tracking."""

from contextlib import suppress
from dataclasses import dataclass, field
from datetime import timedelta
from ipaddress import IPv4Address
import logging
from typing import Any

from pysnmp.error import PySnmpError
from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData,
    ObjectIdentity,
    ObjectType,
    Udp6TransportTarget,
    UdpTransportTarget,
    UsmUserData,
    bulk_walk_cmd,
    get_cmd,
    is_end_of_mib,
)
from pysnmp.proto.rfc1905 import EndOfMibView, NoSuchInstance, NoSuchObject

from homeassistant.components.device_tracker import CONF_SCAN_INTERVAL, SCAN_INTERVAL
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_AUTH_KEY,
    CONF_BASEOID,
    CONF_COMMUNITY,
    CONF_PRIV_KEY,
    CONF_VERSION,
    DEFAULT_AUTH_PROTOCOL,
    DEFAULT_PORT,
    DEFAULT_PRIV_PROTOCOL,
    DEFAULT_TIMEOUT,
    DEFAULT_VERSION,
    DOMAIN,
    SNMP_VERSIONS,
)
from .util import RequestArgsType, async_create_request_cmd_args

_LOGGER = logging.getLogger(__name__)
ARUBA_INSTANT_ACCESS_POINT_ENTRY_OID = (
    1,
    3,
    6,
    1,
    4,
    1,
    14823,
    2,
    3,
    3,
    1,
    2,
    1,
    1,
)
ARUBA_INSTANT_WLAN_ENTRY_OID = (
    1,
    3,
    6,
    1,
    4,
    1,
    14823,
    2,
    3,
    3,
    1,
    2,
    3,
    1,
)
ARUBA_INSTANT_CLIENT_TABLE_OID = (
    1,
    3,
    6,
    1,
    4,
    1,
    14823,
    2,
    3,
    3,
    1,
    2,
    4,
)
ARUBA_INSTANT_CLIENT_ENTRY_OID = (*ARUBA_INSTANT_CLIENT_TABLE_OID, 1)
ARUBA_INSTANT_CLIENT_MAC_OID = (*ARUBA_INSTANT_CLIENT_ENTRY_OID, 1)
ARUBA_INSTANT_ACCESS_POINT_COLUMNS = (1, 2, 13)
ARUBA_INSTANT_CLIENT_COLUMNS = (1, 2, 3, 5, 17, 18)
ARUBA_INSTANT_WLAN_COLUMNS = (1, 4)
ARUBA_INSTANT_CLIENT_OIDS = {
    ".".join(map(str, oid))
    for oid in (
        ARUBA_INSTANT_CLIENT_TABLE_OID,
        ARUBA_INSTANT_CLIENT_ENTRY_OID,
        ARUBA_INSTANT_CLIENT_MAC_OID,
    )
}
ARUBA_PHY_TYPES = {
    1: "802.11a",
    2: "802.11b",
    3: "802.11g",
    4: "802.11a/g",
    5: "wired",
}
ARUBA_HT_MODES = {
    1: "none",
    2: "HT20",
    3: "HT40",
}
ARUBA_CLUSTER_MASTER = "cluster master"
ARUBA_CLUSTER_SLAVE = "cluster slave"
SYSTEM_OIDS = (
    ("description", "1.3.6.1.2.1.1.1.0"),
    ("object_id", "1.3.6.1.2.1.1.2.0"),
    ("uptime", "1.3.6.1.2.1.1.3.0"),
    ("name", "1.3.6.1.2.1.1.5.0"),
)
SNMP_BULK_MAX_REPETITIONS = 20


@dataclass(frozen=True, slots=True)
class SnmpSystemInfo:
    """Optional information from the SNMP system group."""

    description: str | None = None
    object_id: str | None = None
    uptime: float | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class SnmpClientInfo:
    """Information about a client returned by an SNMP table."""

    mac_address: str
    ip_address: str | None = None
    hostname: str | None = None
    bssid: str | None = None
    access_point: str | None = None
    access_point_mac: str | None = None
    phy_type: str | None = None
    ht_mode: str | None = None


@dataclass(frozen=True, slots=True)
class SnmpAccessPointInfo:
    """Information about an Aruba Instant access point."""

    mac_address: str
    name: str
    role: str | None = None
    connected_clients: int = 0


@dataclass(frozen=True, slots=True)
class SnmpCoordinatorData:
    """Data returned by the SNMP coordinator."""

    mac_addresses: frozenset[str]
    system_info: SnmpSystemInfo
    clients: dict[str, SnmpClientInfo] = field(default_factory=dict)
    access_points: dict[str, SnmpAccessPointInfo] = field(default_factory=dict)


class SnmpWalkError(Exception):
    """Error while walking an SNMP table."""


class SnmpClient:
    """Client for fetching devices from an SNMP table."""

    def __init__(
        self, request_args: RequestArgsType, base_oid: str | None = None
    ) -> None:
        """Initialize the client."""
        self._request_args = request_args
        self._base_oid = base_oid.strip(".") if base_oid else None
        self._access_points: dict[str, SnmpAccessPointInfo] = {}

    @property
    def access_points(self) -> dict[str, SnmpAccessPointInfo]:
        """Return access points discovered during the latest client walk."""
        return self._access_points

    async def _async_walk(self, object_type: ObjectType) -> list[tuple[Any, Any]]:
        """Walk an SNMP table and return its variable bindings."""
        engine, auth_data, target, context_data, _ = self._request_args
        walker = bulk_walk_cmd(
            engine,
            auth_data,
            target,
            context_data,
            0,
            SNMP_BULK_MAX_REPETITIONS,
            object_type,
            lexicographicMode=False,
        )
        variable_bindings: list[tuple[Any, Any]] = []

        async for errindication, errstatus, errindex, result in walker:
            if errindication:
                raise SnmpWalkError(str(errindication))
            if errstatus:
                error_oid = (errindex and result[int(errindex) - 1][0]) or "unknown OID"
                raise SnmpWalkError(f"{errstatus.prettyPrint()} at {error_oid}")

            if not is_end_of_mib(result):
                variable_bindings.extend(result)

        return variable_bindings

    async def _async_walk_columns(
        self, root_oid: tuple[int, ...], columns: tuple[int, ...]
    ) -> list[tuple[Any, Any]]:
        """Walk selected columns from an SNMP table."""
        variable_bindings: list[tuple[Any, Any]] = []
        root = ".".join(map(str, root_oid))
        for column in columns:
            variable_bindings.extend(
                await self._async_walk(ObjectType(ObjectIdentity(f"{root}.{column}")))
            )
        return variable_bindings

    @staticmethod
    def _format_mac(value: Any) -> str | None:
        """Format an SNMP octet string as a MAC address."""
        try:
            octets = value.asOctets()
        except AttributeError, ValueError:
            return None
        if len(octets) != 6:
            return None
        return dr.format_mac(octets.hex())

    @staticmethod
    def _rows_by_index(
        variable_bindings: list[tuple[Any, Any]], root_oid: tuple[int, ...]
    ) -> dict[tuple[int, ...], dict[int, Any]]:
        """Group table columns by their shared row index."""
        rows: dict[tuple[int, ...], dict[int, Any]] = {}
        for oid, value in variable_bindings:
            try:
                oid_parts = tuple(oid.asTuple())
            except AttributeError:
                continue
            if oid_parts[: len(root_oid)] != root_oid or len(oid_parts) <= len(
                root_oid
            ):
                continue
            column = oid_parts[len(root_oid)]
            index = oid_parts[len(root_oid) + 1 :]
            rows.setdefault(index, {})[column] = value
        return rows

    @staticmethod
    def _enum_name(value: Any, names: dict[int, str]) -> str | None:
        """Return a display name for an integer MIB enumeration."""
        try:
            number = int(value)
        except TypeError, ValueError:
            return None
        return names.get(number, str(number))

    @staticmethod
    def _format_ip_address(value: Any) -> str | None:
        """Format an SNMP IPv4 value."""
        if value is None:
            return None
        with suppress(AttributeError, ValueError):
            octets = value.asOctets()
            if len(octets) == 4:
                address = str(IPv4Address(octets))
                return None if address == "0.0.0.0" else address
        address = str(value).strip()
        return address if address and address != "0.0.0.0" else None

    async def _async_get_generic_clients(self) -> dict[str, SnmpClientInfo]:
        """Fetch clients from a generic table containing MAC address values."""
        clients: dict[str, SnmpClientInfo] = {}
        for _oid, value in await self._async_walk(self._request_args[4]):
            if mac := self._format_mac(value):
                _LOGGER.debug("Found MAC address: %s", mac)
                clients[mac] = SnmpClientInfo(mac_address=mac)
        return clients

    async def _async_get_aruba_access_points(
        self,
    ) -> tuple[dict[str, tuple[str, str | None]], dict[str, str]]:
        """Return Aruba Instant AP details and BSSIDs mapped to AP MACs."""
        ap_rows = self._rows_by_index(
            await self._async_walk_columns(
                ARUBA_INSTANT_ACCESS_POINT_ENTRY_OID,
                ARUBA_INSTANT_ACCESS_POINT_COLUMNS,
            ),
            ARUBA_INSTANT_ACCESS_POINT_ENTRY_OID,
        )
        access_points: dict[str, tuple[str, str | None]] = {}
        for row in ap_rows.values():
            if (mac := self._format_mac(row.get(1))) is not None and (
                name := str(row.get(2, "")).strip()
            ):
                role_value = str(row.get(13, "")).strip().casefold()
                role = (
                    role_value
                    if role_value in (ARUBA_CLUSTER_MASTER, ARUBA_CLUSTER_SLAVE)
                    else None
                )
                access_points[mac] = (name, role)

        wlan_rows = self._rows_by_index(
            await self._async_walk_columns(
                ARUBA_INSTANT_WLAN_ENTRY_OID,
                ARUBA_INSTANT_WLAN_COLUMNS,
            ),
            ARUBA_INSTANT_WLAN_ENTRY_OID,
        )
        bssid_to_ap: dict[str, str] = {}
        for row in wlan_rows.values():
            if (
                (ap_mac := self._format_mac(row.get(1))) is not None
                and (bssid := self._format_mac(row.get(4))) is not None
                and ap_mac in access_points
            ):
                bssid_to_ap[bssid] = ap_mac
        return access_points, bssid_to_ap

    async def _async_get_aruba_clients(self) -> dict[str, SnmpClientInfo]:
        """Fetch enriched clients from the Aruba Instant client table."""
        client_rows = self._rows_by_index(
            await self._async_walk_columns(
                ARUBA_INSTANT_CLIENT_ENTRY_OID,
                ARUBA_INSTANT_CLIENT_COLUMNS,
            ),
            ARUBA_INSTANT_CLIENT_ENTRY_OID,
        )
        try:
            access_points, bssid_to_ap = await self._async_get_aruba_access_points()
        except (PySnmpError, SnmpWalkError) as err:
            _LOGGER.debug("Unable to fetch optional Aruba AP names: %s", err)
            access_points = {}
            bssid_to_ap = {}

        clients: dict[str, SnmpClientInfo] = {}
        client_counts = dict.fromkeys(access_points, 0)
        for row in client_rows.values():
            if (mac := self._format_mac(row.get(1))) is None:
                continue
            bssid = self._format_mac(row.get(2))
            ap_mac = bssid_to_ap.get(bssid) if bssid else None
            if ap_mac is not None:
                client_counts[ap_mac] += 1
            hostname = str(row.get(5, "")).strip() or None
            clients[mac] = SnmpClientInfo(
                mac_address=mac,
                ip_address=self._format_ip_address(row.get(3)),
                hostname=hostname,
                bssid=bssid,
                access_point=access_points[ap_mac][0] if ap_mac else None,
                access_point_mac=ap_mac,
                phy_type=self._enum_name(row.get(17), ARUBA_PHY_TYPES),
                ht_mode=self._enum_name(row.get(18), ARUBA_HT_MODES),
            )
            _LOGGER.debug("Found Aruba Instant client: %s", clients[mac])
        self._access_points = {
            mac: SnmpAccessPointInfo(
                mac_address=mac,
                name=details[0],
                role=details[1],
                connected_clients=client_counts[mac],
            )
            for mac, details in access_points.items()
        }
        return clients

    async def async_get_clients(self) -> dict[str, SnmpClientInfo]:
        """Fetch clients from the configured SNMP table."""
        if self._base_oid in ARUBA_INSTANT_CLIENT_OIDS:
            return await self._async_get_aruba_clients()
        self._access_points = {}
        return await self._async_get_generic_clients()

    async def async_get_mac_addresses(self) -> frozenset[str]:
        """Fetch MAC addresses from the configured SNMP table."""
        return frozenset(await self.async_get_clients())

    async def async_get_system_info(self) -> SnmpSystemInfo:
        """Fetch optional information from the SNMP system group."""
        engine, auth_data, target, context_data, _object_type = self._request_args
        try:
            errindication, errstatus, _errindex, result = await get_cmd(
                engine,
                auth_data,
                target,
                context_data,
                *(ObjectType(ObjectIdentity(oid)) for _key, oid in SYSTEM_OIDS),
            )
        except PySnmpError as err:
            _LOGGER.debug("Unable to fetch optional SNMP system information: %s", err)
            return SnmpSystemInfo()

        if errindication or errstatus:
            _LOGGER.debug(
                "Unable to fetch optional SNMP system information: %s",
                errindication or errstatus.prettyPrint(),
            )
            return SnmpSystemInfo()

        description: str | None = None
        object_id: str | None = None
        uptime: float | None = None
        name: str | None = None
        for (key, _oid), (_result_oid, value) in zip(SYSTEM_OIDS, result, strict=False):
            if isinstance(value, (EndOfMibView, NoSuchInstance, NoSuchObject)):
                continue
            if key == "description":
                description = str(value) or None
            elif key == "object_id":
                object_id = str(value) or None
            elif key == "uptime":
                with suppress(TypeError, ValueError):
                    uptime = int(value) / 100
            elif key == "name":
                name = str(value) or None

        return SnmpSystemInfo(
            description=description,
            object_id=object_id,
            uptime=uptime,
            name=name,
        )


async def async_create_snmp_client(
    hass: HomeAssistant, config: dict[str, Any]
) -> SnmpClient:
    """Create an SNMP client from configuration."""
    host = config[CONF_HOST]
    try:
        target = await UdpTransportTarget.create(
            (host, DEFAULT_PORT), timeout=DEFAULT_TIMEOUT
        )
    except PySnmpError:
        target = await Udp6TransportTarget.create(
            (host, DEFAULT_PORT), timeout=DEFAULT_TIMEOUT
        )

    community = config[CONF_COMMUNITY]
    auth_key = config.get(CONF_AUTH_KEY)
    priv_key = config.get(CONF_PRIV_KEY)
    if auth_key is not None or priv_key is not None:
        auth_data = UsmUserData(
            community,
            authKey=auth_key or None,
            privKey=priv_key or None,
            authProtocol=DEFAULT_AUTH_PROTOCOL,
            privProtocol=DEFAULT_PRIV_PROTOCOL,
        )
    else:
        auth_data = CommunityData(
            community,
            mpModel=SNMP_VERSIONS[config.get(CONF_VERSION, DEFAULT_VERSION)],
        )

    request_args = await async_create_request_cmd_args(
        hass, auth_data, target, config[CONF_BASEOID]
    )
    return SnmpClient(request_args, config[CONF_BASEOID])


class SnmpDataUpdateCoordinator(DataUpdateCoordinator[SnmpCoordinatorData]):
    """Coordinate SNMP device tracker updates."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: SnmpClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=config_entry.options.get(
                    CONF_SCAN_INTERVAL, SCAN_INTERVAL.total_seconds()
                )
            ),
        )
        self._client = client

    async def _async_update_data(self) -> SnmpCoordinatorData:
        """Fetch the latest MAC address table."""
        try:
            clients = await self._client.async_get_clients()
        except (PySnmpError, SnmpWalkError) as err:
            raise UpdateFailed(
                f"Unable to fetch SNMP MAC address table: {err}"
            ) from err

        management_masters = [
            access_point.name
            for access_point in self._client.access_points.values()
            if access_point.role == ARUBA_CLUSTER_MASTER
        ]
        if len(management_masters) > 1:
            _LOGGER.warning(
                "Multiple Aruba management masters reported: %s",
                ", ".join(sorted(management_masters)),
            )

        return SnmpCoordinatorData(
            mac_addresses=frozenset(clients),
            system_info=await self._client.async_get_system_info(),
            clients=clients,
            access_points=dict(self._client.access_points),
        )
