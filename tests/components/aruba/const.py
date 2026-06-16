"""Constants for Aruba tests."""

from aioarubainstant import (
    ArubaAccessPoint,
    ArubaClient,
    ArubaCluster,
    ArubaInstantSnapshot,
)

from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)

HOST = "controller.example.com"
MANAGEMENT_ADDRESS = "192.0.2.10"
MAC = "aa:bb:cc:dd:ee:ff"
MAC_2 = "11:22:33:44:55:66"
AP_MAC = "00:11:22:33:44:55"
AP_KEY = f"mac:{AP_MAC}"

CONFIG = {
    CONF_HOST: HOST,
    CONF_PASSWORD: "test-password",
    CONF_PORT: 4343,
    CONF_USERNAME: "admin",
    CONF_VERIFY_SSL: True,
}


def create_snapshot(
    clients: tuple[ArubaClient, ...] | None = None,
    *,
    access_points: tuple[ArubaAccessPoint, ...] | None = None,
    ap_count: int | None = 1,
    client_count: int = 1,
    management_address: str | None = MANAGEMENT_ADDRESS,
    master_ap: str | None = "Office AP",
) -> ArubaInstantSnapshot:
    """Create an Aruba snapshot."""
    if clients is None:
        clients = (
            ArubaClient(
                mac=MAC,
                hostname="Laptop",
                ip_address="192.0.2.20",
                ssid="Test network",
                bssid="00:11:22:33:44:55",
                associated_ap="Office AP",
                signal_strength=-48,
                link_speed=866,
                channel=36,
                phy_mode="802.11ax",
                role="employee",
            ),
        )
    if access_points is None:
        access_points = (
            ArubaAccessPoint(
                mac=AP_MAC,
                name="Office AP",
                ip_address="192.0.2.11",
                model="AP-515",
                serial="TESTSERIAL",
                firmware="8.6.0.22",
                connected_clients=client_count,
                is_master=True,
            ),
        )
    return ArubaInstantSnapshot(
        cluster=ArubaCluster(
            name="Test cluster",
            management_address=management_address,
            version="8.6.0.22",
            master_ap=master_ap,
            ap_count=ap_count,
            client_count=client_count,
        ),
        access_points=access_points,
        clients=clients,
        _raw_output={"show summary": "private controller output"},
    )


SNAPSHOT = create_snapshot()
ZERO_CLIENT_SNAPSHOT = create_snapshot((), client_count=0)
