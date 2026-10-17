"""Einrichtung: Kamera-Entität (ESP32-CAM) und optional die Licht-Entität der LED auswählen."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

from .const import CONF_CAMERA, CONF_LIGHT, DOMAIN


def _guess(hass: HomeAssistant, domain: str, words: tuple[str, ...]) -> str | None:
    ids = hass.states.async_entity_ids(domain)
    for w in words:
        for eid in ids:
            if w in eid:
                return eid
    return None


def _schema(hass: HomeAssistant, camera: str | None, light: str | None) -> vol.Schema:
    camera = camera or _guess(hass, "camera", ("manometer",))
    light = light or _guess(hass, "light", ("manometer", "beleuchtung"))
    return vol.Schema({
        vol.Required(CONF_CAMERA, description={"suggested_value": camera}):
            selector.EntitySelector(selector.EntitySelectorConfig(domain="camera")),
        vol.Optional(CONF_LIGHT, description={"suggested_value": light}):
            selector.EntitySelector(selector.EntitySelectorConfig(domain="light")),
    })


def _validate(hass: HomeAssistant, data: dict) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not data.get(CONF_CAMERA) or hass.states.get(data[CONF_CAMERA]) is None:
        errors[CONF_CAMERA] = "camera_missing"
    if data.get(CONF_LIGHT) and hass.states.get(data[CONF_LIGHT]) is None:
        errors[CONF_LIGHT] = "light_missing"
    return errors


class ManometerConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ManometerOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(self.hass, user_input)
            if not errors:
                return self.async_create_entry(title="Heizung Manometer", data={
                    CONF_CAMERA: user_input[CONF_CAMERA], CONF_LIGHT: user_input.get(CONF_LIGHT) or None})
        cur = user_input or {}
        return self.async_show_form(step_id="user", errors=errors,
                                    data_schema=_schema(self.hass, cur.get(CONF_CAMERA), cur.get(CONF_LIGHT)))


class ManometerOptionsFlow(OptionsFlow):
    """Kamera oder LED später ändern (Einstellungen → Geräte & Dienste → Manometer-Leser → Konfigurieren)."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(self.hass, user_input)
            if not errors:
                return self.async_create_entry(data={
                    CONF_CAMERA: user_input[CONF_CAMERA], CONF_LIGHT: user_input.get(CONF_LIGHT) or None})
        entry = self.config_entry
        cam = entry.options.get(CONF_CAMERA, entry.data.get(CONF_CAMERA))
        light = entry.options.get(CONF_LIGHT, entry.data.get(CONF_LIGHT))
        return self.async_show_form(step_id="init", errors=errors, data_schema=_schema(self.hass, cam, light))
