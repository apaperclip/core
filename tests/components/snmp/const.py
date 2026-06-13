"""Constants for SNMP tests."""

from homeassistant.components.snmp.const import (
    CONF_BASEOID,
    CONF_COMMUNITY,
    DEFAULT_COMMUNITY,
)
from homeassistant.components.snmp.coordinator import SnmpSystemInfo
from homeassistant.const import CONF_HOST

HOST = "192.168.1.32"
BASE_OID = "1.3.6.1.2.1.3.1.1.2"
MAC = "aa:bb:cc:dd:ee:ff"
MAC_2 = "11:22:33:44:55:66"
SYSTEM_DESCRIPTION = "Test router firmware 1.0"
SYSTEM_NAME = "test-router"
SYSTEM_OBJECT_ID = "1.3.6.1.4.1.1234.1"
SYSTEM_UPTIME = 1234.56
SYSTEM_INFO = SnmpSystemInfo(
    description=SYSTEM_DESCRIPTION,
    object_id=SYSTEM_OBJECT_ID,
    uptime=SYSTEM_UPTIME,
    name=SYSTEM_NAME,
)

CONFIG = {
    CONF_HOST: HOST,
    CONF_BASEOID: BASE_OID,
    CONF_COMMUNITY: DEFAULT_COMMUNITY,
}
