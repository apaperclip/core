"""Config flow for Aruba Instant."""

from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from ipaddress import ip_address
import logging
from typing import Any
from urllib.parse import urlsplit

from aioarubainstant import (
    ArubaInstantAuthenticationError,
    ArubaInstantClient,
    ArubaInstantCommandError,
    ArubaInstantConnectionError,
    ArubaInstantError,
    ArubaInstantNotMasterError,
    ArubaInstantParseError,
    ArubaInstantRestDisabledError,
    ArubaInstantSnapshot,
)
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DEFAULT_PORT, DEFAULT_VERIFY_SSL, DOMAIN

_LOGGER = logging.getLogger(__name__)

PASSWORD_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(
        type=selector.TextSelectorType.PASSWORD,
        autocomplete="current-password",
    )
)

CONNECTION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): selector.TextSelector(),
        vol.Required(CONF_PORT, default=DEFAULT_PORT): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1,
                max=65535,
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
        vol.Required(CONF_USERNAME): selector.TextSelector(),
        vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
        vol.Required(
            CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL
        ): selector.BooleanSelector(),
    }
)

RECONFIGURE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): selector.TextSelector(),
        vol.Required(CONF_PORT): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1,
                max=65535,
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
        vol.Required(CONF_VERIFY_SSL): selector.BooleanSelector(),
    }
)


@dataclass(frozen=True, slots=True)
class ArubaConfigFlowResult:
    """Validated Aruba connection information."""

    snapshot: ArubaInstantSnapshot
    unique_id: str

    @property
    def title(self) -> str:
        """Return the config entry title."""
        return self.snapshot.cluster.name or self.unique_id


def _normalize_host(value: str) -> tuple[str, int | None]:
    """Normalize a host or HTTPS URL."""
    value = value.strip()
    if not value:
        raise ValueError

    if "://" not in value:
        with suppress(ValueError):
            return str(ip_address(value.strip("[]"))), None

    candidate = value if "://" in value else f"https://{value}"
    parsed = urlsplit(candidate)
    try:
        port = parsed.port
    except ValueError as err:
        raise ValueError from err

    if (
        parsed.scheme.casefold() != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError

    host = parsed.hostname.rstrip(".").encode("idna").decode().casefold()
    return host, port


def _normalize_data(data: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize config flow connection data."""
    normalized = dict(data)
    host, embedded_port = _normalize_host(normalized[CONF_HOST])
    normalized[CONF_HOST] = host
    if embedded_port is not None:
        normalized[CONF_PORT] = embedded_port
    normalized.setdefault(CONF_PORT, DEFAULT_PORT)
    normalized.setdefault(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
    return normalized


def _cluster_unique_id(snapshot: ArubaInstantSnapshot, fallback_host: str) -> str:
    """Return the best available stable cluster identity."""
    if management_address := snapshot.cluster.management_address:
        with suppress(ValueError):
            return _normalize_host(management_address)[0]
    return _normalize_host(fallback_host)[0]


async def _async_validate_input(
    flow: ConfigFlow, data: dict[str, Any]
) -> ArubaConfigFlowResult:
    """Validate connection data and return controller information."""
    client = ArubaInstantClient(
        data[CONF_HOST],
        data[CONF_USERNAME],
        data[CONF_PASSWORD],
        port=data[CONF_PORT],
        verify_ssl=data[CONF_VERIFY_SSL],
        session=async_get_clientsession(flow.hass),
    )
    try:
        snapshot = await client.async_get_snapshot()
    finally:
        with suppress(ArubaInstantError):
            await client.async_close()

    return ArubaConfigFlowResult(
        snapshot,
        _cluster_unique_id(snapshot, data[CONF_HOST]),
    )


class ArubaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle an Aruba config flow."""

    VERSION = 1

    async def _async_validate(
        self, data: dict[str, Any], errors: dict[str, str]
    ) -> ArubaConfigFlowResult | None:
        """Validate data and map library errors to config flow errors."""
        try:
            return await _async_validate_input(self, data)
        except ValueError:
            errors["base"] = "invalid_host"
        except ArubaInstantAuthenticationError:
            errors["base"] = "invalid_auth"
        except ArubaInstantConnectionError:
            errors["base"] = "cannot_connect"
        except ArubaInstantRestDisabledError:
            errors["base"] = "rest_disabled"
        except ArubaInstantNotMasterError:
            errors["base"] = "not_master"
        except ArubaInstantCommandError, ArubaInstantParseError:
            errors["base"] = "invalid_response"
        except ArubaInstantError:
            errors["base"] = "cannot_connect"
        except Exception as err:  # noqa: BLE001
            _LOGGER.error(
                "Unexpected %s while validating the Aruba controller",
                type(err).__name__,
            )
            errors["base"] = "unknown"
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a user-initiated config flow."""
        errors: dict[str, str] = {}
        normalized_input: dict[str, Any] | None = None
        if user_input is not None:
            try:
                normalized_input = _normalize_data(user_input)
            except ValueError:
                errors["base"] = "invalid_host"
            else:
                if result := await self._async_validate(normalized_input, errors):
                    await self.async_set_unique_id(result.unique_id)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=result.title,
                        data=normalized_input,
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                CONNECTION_SCHEMA, normalized_input or user_input
            ),
            errors=errors,
        )

    async def async_step_import(self, import_data: dict[str, Any]) -> ConfigFlowResult:
        """Import a legacy YAML device tracker configuration."""
        errors: dict[str, str] = {}
        try:
            data = _normalize_data(import_data)
        except ValueError:
            return self.async_abort(reason="invalid_host")
        if not (result := await self._async_validate(data, errors)):
            return self.async_abort(reason=errors["base"])

        await self.async_set_unique_id(result.unique_id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=result.title, data=data)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle updated credentials."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            data = {**entry.data, **user_input}
            if result := await self._async_validate(data, errors):
                await self.async_set_unique_id(result.unique_id)
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates=user_input,
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_USERNAME): selector.TextSelector(),
                        vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
                    }
                ),
                {CONF_USERNAME: entry.data[CONF_USERNAME]},
            ),
            errors=errors,
            description_placeholders={"name": entry.title},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure controller connection settings."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        normalized_input: dict[str, Any] | None = None
        if user_input is not None:
            try:
                normalized_input = _normalize_data({**entry.data, **user_input})
            except ValueError:
                errors["base"] = "invalid_host"
            else:
                if result := await self._async_validate(normalized_input, errors):
                    await self.async_set_unique_id(result.unique_id)
                    self._abort_if_unique_id_mismatch()
                    return self.async_update_reload_and_abort(
                        entry,
                        data=normalized_input,
                        title=result.title,
                    )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                RECONFIGURE_SCHEMA, normalized_input or entry.data
            ),
            errors=errors,
            description_placeholders={"name": entry.title},
        )
