"""Einrichtung über die Oberfläche: Adresse und ggf. Passwort des Manometer-Lesers."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ManometerApi, ManometerAuthError, ManometerError, normalize_url
from .const import CONF_PASSWORD, CONF_URL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class ManometerConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _check(self, url: str, password: str | None) -> tuple[dict | None, str | None]:
        api = ManometerApi(async_get_clientsession(self.hass), url, password or None)
        try:
            return await api.status(), None
        except ManometerAuthError:
            return None, "invalid_auth"
        except ManometerError:
            return None, "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unerwarteter Fehler beim Verbinden")
            return None, "unknown"

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            url = normalize_url(user_input[CONF_URL])
            password = user_input.get(CONF_PASSWORD) or ""
            status, err = await self._check(url, password)
            if err:
                errors["base"] = err
            else:
                await self.async_set_unique_id(status.get("id") or url)
                self._abort_if_unique_id_configured(updates={CONF_URL: url, CONF_PASSWORD: password})
                return self.async_create_entry(title="Heizung Manometer",
                                               data={CONF_URL: url, CONF_PASSWORD: password})
        schema = vol.Schema({
            vol.Required(CONF_URL, default=(user_input or {}).get(CONF_URL, "")): str,
            vol.Optional(CONF_PASSWORD, default=""): str,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    # Passwort wurde im Manometer-Leser geändert -> Home Assistant fragt neu nach
    async def async_step_reauth(self, entry_data: Mapping[str, Any]):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            password = user_input.get(CONF_PASSWORD) or ""
            _, err = await self._check(entry.data[CONF_URL], password)
            if err:
                errors["base"] = err
            else:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_PASSWORD: password})
        return self.async_show_form(step_id="reauth_confirm",
                                    data_schema=vol.Schema({vol.Optional(CONF_PASSWORD, default=""): str}),
                                    errors=errors)
