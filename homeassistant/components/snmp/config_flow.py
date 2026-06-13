"""Config flow for the SNMP integration."""

from datetime import timedelta
import logging
from typing import Any

from pysnmp.error import PySnmpError
import voluptuous as vol

from homeassistant.components.device_tracker import (
    CONF_CONSIDER_HOME,
    CONF_SCAN_INTERVAL,
    DEFAULT_CONSIDER_HOME,
    SCAN_INTERVAL,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_AUTH_KEY,
    CONF_BASEOID,
    CONF_COMMUNITY,
    CONF_PRIV_KEY,
    CONF_VERSION,
    DEFAULT_COMMUNITY,
    DEFAULT_VERSION,
    DOMAIN,
    SNMP_VERSIONS,
)
from .coordinator import SnmpWalkError, async_create_snmp_client

_LOGGER = logging.getLogger(__name__)

PASSWORD_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
)


def _connection_schema() -> vol.Schema:
    """Return the SNMP connection schema."""
    return vol.Schema(
        {
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_BASEOID): str,
            vol.Optional(CONF_COMMUNITY, default=DEFAULT_COMMUNITY): str,
            vol.Optional(
                CONF_VERSION, default=DEFAULT_VERSION
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[version for version in SNMP_VERSIONS if version != "3"]
                )
            ),
            vol.Inclusive(CONF_AUTH_KEY, "keys"): PASSWORD_SELECTOR,
            vol.Inclusive(CONF_PRIV_KEY, "keys"): PASSWORD_SELECTOR,
        }
    )


def _normalize_connection_data(user_input: dict[str, Any]) -> dict[str, Any]:
    """Normalize SNMP connection data."""
    data = dict(user_input)
    data[CONF_HOST] = data[CONF_HOST].strip()
    data[CONF_BASEOID] = data[CONF_BASEOID].strip()
    data[CONF_COMMUNITY] = data.get(CONF_COMMUNITY, DEFAULT_COMMUNITY).strip()
    data[CONF_VERSION] = data.get(CONF_VERSION, DEFAULT_VERSION)
    return data


def _options_from_import(import_data: dict[str, Any]) -> dict[str, int]:
    """Extract tracker options from imported YAML."""
    scan_interval = import_data.get(CONF_SCAN_INTERVAL, SCAN_INTERVAL)
    consider_home = import_data.get(CONF_CONSIDER_HOME, DEFAULT_CONSIDER_HOME)
    if isinstance(scan_interval, timedelta):
        scan_interval = scan_interval.total_seconds()
    if isinstance(consider_home, timedelta):
        consider_home = consider_home.total_seconds()
    return {
        CONF_SCAN_INTERVAL: int(scan_interval),
        CONF_CONSIDER_HOME: int(consider_home),
    }


class SnmpConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle an SNMP config flow."""

    VERSION = 1

    async def _async_validate_input(self, data: dict[str, Any]) -> str | None:
        """Validate SNMP connection data."""
        try:
            client = await async_create_snmp_client(self.hass, data)
            await client.async_get_mac_addresses()
        except PySnmpError, SnmpWalkError:
            return "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected exception while validating SNMP connection")
            return "unknown"
        return None

    def _abort_if_connection_configured(self, data: dict[str, Any]) -> None:
        """Abort if the SNMP table is already configured."""
        self._async_abort_entries_match(
            {
                CONF_HOST: data[CONF_HOST],
                CONF_BASEOID: data[CONF_BASEOID],
            }
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the user step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = _normalize_connection_data(user_input)
            self._abort_if_connection_configured(user_input)
            if error := await self._async_validate_input(user_input):
                errors["base"] = error
            else:
                return self.async_create_entry(
                    title=user_input[CONF_HOST], data=user_input
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                _connection_schema(), user_input
            ),
            errors=errors,
        )

    async def async_step_import(self, import_data: dict[str, Any]) -> ConfigFlowResult:
        """Import an SNMP device tracker from YAML."""
        data = _normalize_connection_data(
            {
                key: import_data[key]
                for key in (
                    CONF_HOST,
                    CONF_BASEOID,
                    CONF_COMMUNITY,
                    CONF_VERSION,
                    CONF_AUTH_KEY,
                    CONF_PRIV_KEY,
                )
                if key in import_data
            }
        )
        self._abort_if_connection_configured(data)
        if error := await self._async_validate_input(data):
            return self.async_abort(reason=error)
        return self.async_create_entry(
            title=data[CONF_HOST],
            data=data,
            options=_options_from_import(import_data),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure an SNMP connection."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = _normalize_connection_data(user_input)
            self._abort_if_connection_configured(user_input)
            if error := await self._async_validate_input(user_input):
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data=user_input,
                    title=user_input[CONF_HOST],
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                _connection_schema(), user_input or entry.data
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SnmpOptionsFlow:
        """Return the options flow."""
        return SnmpOptionsFlow()


class SnmpOptionsFlow(OptionsFlowWithReload):
    """Handle SNMP tracker options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage SNMP tracker options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    # Approved exemption: legacy tracker polling interval is preserved.
                    # pylint: disable-next=home-assistant-config-flow-polling-field
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=options.get(
                            CONF_SCAN_INTERVAL, int(SCAN_INTERVAL.total_seconds())
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=5,
                            max=3600,
                            step=1,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_CONSIDER_HOME,
                        default=options.get(
                            CONF_CONSIDER_HOME,
                            int(DEFAULT_CONSIDER_HOME.total_seconds()),
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1,
                            max=86400,
                            step=1,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
        )
