"""Support for displaying collected data over SNMP."""

from datetime import timedelta
import logging
from struct import unpack

from pyasn1.codec.ber import decoder
from pysnmp.error import PySnmpError
import pysnmp.hlapi.v3arch.asyncio as hlapi
from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData,
    Udp6TransportTarget,
    UdpTransportTarget,
    UsmUserData,
    get_cmd,
)
from pysnmp.proto.rfc1902 import Opaque
from pysnmp.proto.rfc1905 import NoSuchObject
import voluptuous as vol

from homeassistant.components.sensor import (
    CONF_STATE_CLASS,
    PLATFORM_SCHEMA as SENSOR_PLATFORM_SCHEMA,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    CONF_DEVICE_CLASS,
    CONF_HOST,
    CONF_ICON,
    CONF_NAME,
    CONF_PORT,
    CONF_UNIQUE_ID,
    CONF_UNIT_OF_MEASUREMENT,
    CONF_USERNAME,
    CONF_VALUE_TEMPLATE,
    STATE_UNKNOWN,
    EntityCategory,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import (
    AddConfigEntryEntitiesCallback,
    AddEntitiesCallback,
)
from homeassistant.helpers.template import Template
from homeassistant.helpers.trigger_template_entity import (
    CONF_AVAILABILITY,
    CONF_PICTURE,
    TEMPLATE_SENSOR_BASE_SCHEMA,
    ManualTriggerSensorEntity,
    ValueTemplate,
)
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import SnmpConfigEntry
from .const import (
    CONF_ACCEPT_ERRORS,
    CONF_AUTH_KEY,
    CONF_AUTH_PROTOCOL,
    CONF_BASEOID,
    CONF_COMMUNITY,
    CONF_DEFAULT_VALUE,
    CONF_PRIV_KEY,
    CONF_PRIV_PROTOCOL,
    CONF_VERSION,
    DEFAULT_AUTH_PROTOCOL,
    DEFAULT_COMMUNITY,
    DEFAULT_HOST,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_PRIV_PROTOCOL,
    DEFAULT_TIMEOUT,
    DEFAULT_VERSION,
    MAP_AUTH_PROTOCOLS,
    MAP_PRIV_PROTOCOLS,
    SNMP_VERSIONS,
)
from .coordinator import (
    ARUBA_CLUSTER_MASTER,
    ARUBA_INSTANT_CLIENT_OIDS,
    SnmpAccessPointInfo,
    SnmpDataUpdateCoordinator,
)
from .entity import SnmpAccessPointEntity, SnmpHostEntity
from .util import async_create_request_cmd_args

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=10)

TRIGGER_ENTITY_OPTIONS = (
    CONF_AVAILABILITY,
    CONF_DEVICE_CLASS,
    CONF_ICON,
    CONF_PICTURE,
    CONF_UNIQUE_ID,
    CONF_STATE_CLASS,
    CONF_UNIT_OF_MEASUREMENT,
)

PLATFORM_SCHEMA = SENSOR_PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_BASEOID): cv.string,
        vol.Optional(CONF_ACCEPT_ERRORS, default=False): cv.boolean,
        vol.Optional(CONF_COMMUNITY, default=DEFAULT_COMMUNITY): cv.string,
        vol.Optional(CONF_DEFAULT_VALUE): cv.string,
        vol.Optional(CONF_HOST, default=DEFAULT_HOST): cv.string,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
        vol.Optional(CONF_VALUE_TEMPLATE): vol.All(
            cv.template, ValueTemplate.from_template
        ),
        vol.Optional(CONF_VERSION, default=DEFAULT_VERSION): vol.In(SNMP_VERSIONS),
        vol.Optional(CONF_USERNAME): cv.string,
        vol.Optional(CONF_AUTH_KEY): cv.string,
        vol.Optional(CONF_AUTH_PROTOCOL, default=DEFAULT_AUTH_PROTOCOL): vol.In(
            MAP_AUTH_PROTOCOLS
        ),
        vol.Optional(CONF_PRIV_KEY): cv.string,
        vol.Optional(CONF_PRIV_PROTOCOL, default=DEFAULT_PRIV_PROTOCOL): vol.In(
            MAP_PRIV_PROTOCOLS
        ),
    }
).extend(TEMPLATE_SENSOR_BASE_SCHEMA.schema)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the SNMP sensor."""
    host = config.get(CONF_HOST)
    port = config.get(CONF_PORT)
    community = config.get(CONF_COMMUNITY)
    baseoid: str = config[CONF_BASEOID]
    version = config[CONF_VERSION]
    username = config.get(CONF_USERNAME)
    authkey = config.get(CONF_AUTH_KEY)
    authproto = config[CONF_AUTH_PROTOCOL]
    privkey = config.get(CONF_PRIV_KEY)
    privproto = config[CONF_PRIV_PROTOCOL]
    accept_errors = config.get(CONF_ACCEPT_ERRORS)
    default_value = config.get(CONF_DEFAULT_VALUE)

    try:
        # Try IPv4 first.
        target = await UdpTransportTarget.create((host, port), timeout=DEFAULT_TIMEOUT)
    except PySnmpError:
        # Then try IPv6.
        try:
            target = Udp6TransportTarget((host, port), timeout=DEFAULT_TIMEOUT)
        except PySnmpError as err:
            _LOGGER.error("Invalid SNMP host: %s", err)
            return

    if version == "3":
        if not authkey:
            authproto = "none"
        if not privkey:
            privproto = "none"
        auth_data = UsmUserData(
            username,
            authKey=authkey or None,
            privKey=privkey or None,
            authProtocol=getattr(hlapi, MAP_AUTH_PROTOCOLS[authproto]),
            privProtocol=getattr(hlapi, MAP_PRIV_PROTOCOLS[privproto]),
        )
    else:
        auth_data = CommunityData(community, mpModel=SNMP_VERSIONS[version])

    request_args = await async_create_request_cmd_args(hass, auth_data, target, baseoid)
    get_result = await get_cmd(*request_args)
    errindication, _, _, _ = get_result

    if errindication and not accept_errors:
        _LOGGER.error(
            "Please check the details in the configuration file: %s",
            errindication,
        )
        return

    name = config.get(CONF_NAME, Template(DEFAULT_NAME, hass))
    trigger_entity_config = {CONF_NAME: name}
    for key in TRIGGER_ENTITY_OPTIONS:
        if key not in config:
            continue
        trigger_entity_config[key] = config[key]

    value_template: ValueTemplate | None = config.get(CONF_VALUE_TEMPLATE)

    data = SnmpData(request_args, baseoid, accept_errors, default_value)
    async_add_entities([SnmpSensor(hass, data, trigger_entity_config, value_template)])


class SnmpSensor(ManualTriggerSensorEntity):
    """Representation of a SNMP sensor."""

    _attr_should_poll = True

    def __init__(
        self,
        hass: HomeAssistant,
        data: SnmpData,
        config: ConfigType,
        value_template: ValueTemplate | None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(hass, config)
        self.data = data
        self._state = None
        self._value_template = value_template

    async def async_added_to_hass(self) -> None:
        """Handle adding to Home Assistant."""
        await super().async_added_to_hass()
        await self.async_update()

    async def async_update(self) -> None:
        """Get the latest data and updates the states."""
        await self.data.async_update()

        variables = self._template_variables_with_value(self.data.value)
        if (value := self.data.value) is None:
            value = STATE_UNKNOWN
        elif self._value_template is not None:
            value = self._value_template.async_render_as_value_template(
                self.entity_id, variables, STATE_UNKNOWN
            )

        self._set_native_value_with_possible_timestamp(value)
        self._process_manual_data(variables)


class SnmpData:
    """Get the latest data and update the states."""

    def __init__(self, request_args, baseoid, accept_errors, default_value) -> None:
        """Initialize the data object."""
        self._request_args = request_args
        self._baseoid = baseoid
        self._accept_errors = accept_errors
        self._default_value = default_value
        self.value = None

    async def async_update(self):
        """Get the latest data from the remote SNMP capable host."""

        get_result = await get_cmd(*self._request_args)
        errindication, errstatus, errindex, restable = get_result

        if errindication and not self._accept_errors:
            _LOGGER.error("SNMP error: %s", errindication)
        elif errstatus and not self._accept_errors:
            _LOGGER.error(
                "SNMP error: %s at %s",
                errstatus.prettyPrint(),
                restable[-1][int(errindex) - 1] if errindex else "?",
            )
        elif (errindication or errstatus) and self._accept_errors:
            self.value = self._default_value
        else:
            for resrow in restable:
                self.value = self._decode_value(resrow[-1])

    def _decode_value(self, value):
        """Decode the different results we could get into strings."""

        _LOGGER.debug(
            "SNMP OID %s received type=%s and data %s",
            self._baseoid,
            type(value),
            value,
        )
        if isinstance(value, NoSuchObject):
            _LOGGER.error(
                "SNMP error for OID %s: No Such Object currently exists at this OID",
                self._baseoid,
            )
            return self._default_value

        if isinstance(value, Opaque):
            # Float data type is not supported by the pyasn1 library,
            # so we need to decode this type ourselves based on:
            # https://tools.ietf.org/html/draft-perkins-opaque-01
            if bytes(value).startswith(b"\x9f\x78"):
                return str(unpack("!f", bytes(value)[3:])[0])
            # Otherwise Opaque types should be asn1 encoded
            try:
                decoded_value, _ = decoder.decode(bytes(value))
                return str(decoded_value)
            except Exception as decode_exception:  # noqa: BLE001
                _LOGGER.error(
                    "SNMP error in decoding opaque type: %s", decode_exception
                )
                return self._default_value
        return str(value)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SnmpConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up diagnostic sensors for the polled SNMP host."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        SnmpAssociatedClientsSensor(coordinator, entry),
        SnmpUptimeSensor(coordinator, entry),
    ]
    if entry.data[CONF_BASEOID].strip(".") in ARUBA_INSTANT_CLIENT_OIDS:
        entities.extend(
            (
                SnmpAccessPointsSensor(coordinator, entry),
                SnmpManagementMasterSensor(coordinator, entry),
            )
        )
    async_add_entities(entities)

    tracked_access_points: set[str] = set()

    @callback
    def add_access_point_entities() -> None:
        new_access_points = (
            coordinator.data.access_points.keys() - tracked_access_points
        )
        if not new_access_points:
            return
        tracked_access_points.update(new_access_points)
        entities: list[SensorEntity] = []
        for mac in sorted(new_access_points):
            access_point = coordinator.data.access_points[mac]
            entities.extend(
                (
                    SnmpAccessPointClientsSensor(coordinator, entry, access_point),
                    SnmpClusterAccessPointClientsSensor(
                        coordinator, entry, access_point
                    ),
                )
            )
        async_add_entities(entities)

    add_access_point_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_access_point_entities))


class SnmpAssociatedClientsSensor(SnmpHostEntity, SensorEntity):
    """Represent the number of clients returned by the SNMP table."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:account-multiple"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "associated_clients"

    def __init__(
        self,
        coordinator: SnmpDataUpdateCoordinator,
        entry: SnmpConfigEntry,
    ) -> None:
        """Initialize the associated clients sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_associated_clients"

    @property
    def native_value(self) -> int:
        """Return the number of associated clients."""
        return len(self.coordinator.data.mac_addresses)


class SnmpAccessPointsSensor(SnmpHostEntity, SensorEntity):
    """Represent access points associated with an Aruba Instant cluster."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:access-point-network"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "access_points"

    def __init__(
        self,
        coordinator: SnmpDataUpdateCoordinator,
        entry: SnmpConfigEntry,
    ) -> None:
        """Initialize the access points sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_access_points"

    @property
    def native_value(self) -> int:
        """Return the number of associated access points."""
        return len(self.coordinator.data.access_points)

    @property
    def extra_state_attributes(self) -> dict[str, list[dict[str, str]]]:
        """Return associated access point names and MAC addresses."""
        return {
            "access_points": [
                {"name": access_point.name, "mac_address": mac}
                for mac, access_point in sorted(
                    self.coordinator.data.access_points.items(),
                    key=lambda item: (item[1].name, item[0]),
                )
            ]
        }


class SnmpManagementMasterSensor(SnmpHostEntity, SensorEntity):
    """Represent the access point currently owning the management VIP."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:server-network"
    _attr_translation_key = "management_master"

    def __init__(
        self,
        coordinator: SnmpDataUpdateCoordinator,
        entry: SnmpConfigEntry,
    ) -> None:
        """Initialize the management master sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_management_master"

    @property
    def native_value(self) -> str | None:
        """Return the name of the access point owning the management VIP."""
        management_masters = [
            access_point.name
            for access_point in self.coordinator.data.access_points.values()
            if access_point.role == ARUBA_CLUSTER_MASTER
        ]
        if len(management_masters) != 1:
            return None
        return management_masters[0]

    @property
    def available(self) -> bool:
        """Return whether exactly one management master is reported."""
        return (
            super().available
            and sum(
                access_point.role == ARUBA_CLUSTER_MASTER
                for access_point in self.coordinator.data.access_points.values()
            )
            == 1
        )


class SnmpClusterAccessPointClientsSensor(SnmpHostEntity, SensorEntity):
    """Represent an AP client count on the virtual cluster device."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:account-multiple"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "access_point_connected_clients"

    def __init__(
        self,
        coordinator: SnmpDataUpdateCoordinator,
        entry: SnmpConfigEntry,
        access_point: SnmpAccessPointInfo,
    ) -> None:
        """Initialize a cluster AP client distribution sensor."""
        super().__init__(coordinator, entry)
        self._access_point_mac = access_point.mac_address
        self._attr_translation_placeholders = {"access_point": access_point.name}
        self._attr_unique_id = (
            f"{entry.entry_id}_{access_point.mac_address}_cluster_connected_clients"
        )

    @property
    def native_value(self) -> int | None:
        """Return the number of clients connected to this access point."""
        if (
            access_point := self.coordinator.data.access_points.get(
                self._access_point_mac
            )
        ) is None:
            return None
        return access_point.connected_clients


class SnmpAccessPointClientsSensor(SnmpAccessPointEntity, SensorEntity):
    """Represent the number of clients connected to an access point."""

    _attr_icon = "mdi:account-multiple"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "connected_clients"

    def __init__(
        self,
        coordinator: SnmpDataUpdateCoordinator,
        entry: SnmpConfigEntry,
        access_point: SnmpAccessPointInfo,
    ) -> None:
        """Initialize an access point client sensor."""
        super().__init__(coordinator, entry, access_point)
        self._attr_unique_id = (
            f"{entry.entry_id}_{access_point.mac_address}_connected_clients"
        )

    @property
    def native_value(self) -> int | None:
        """Return the number of clients connected to this access point."""
        if (access_point := self.access_point) is None:
            return None
        return access_point.connected_clients


class SnmpUptimeSensor(SnmpHostEntity, SensorEntity):
    """Represent the optional SNMP system uptime."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_translation_key = "uptime"

    def __init__(
        self,
        coordinator: SnmpDataUpdateCoordinator,
        entry: SnmpConfigEntry,
    ) -> None:
        """Initialize the uptime sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_uptime"

    @property
    def available(self) -> bool:
        """Return whether system uptime is supported and current."""
        return (
            super().available and self.coordinator.data.system_info.uptime is not None
        )

    @property
    def native_value(self) -> float | None:
        """Return system uptime in seconds."""
        return self.coordinator.data.system_info.uptime
