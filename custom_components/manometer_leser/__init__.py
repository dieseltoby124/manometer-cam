"""Manometer-Leser: Heizungsdruck per Kamera – komplett in Home Assistant.

Holt Bilder von einer Kamera-Entität (ESP32-CAM mit ESPHome), erkennt den Zeiger,
stellt Sensoren bereit und bringt ein Seitenleisten-Panel zum Kalibrieren mit.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from .const import CONF_CAMERA, CONF_LIGHT, DOMAIN, PANEL_URL, STATIC_URL, VERSION
from .coordinator import ManometerCoordinator
from .core import ManometerCore
from .http_api import ManometerApiView

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.CAMERA, Platform.BUTTON]


def _opt(entry: ConfigEntry, key: str):
    return entry.options.get(key, entry.data.get(key))


async def _async_setup_frontend(hass: HomeAssistant) -> None:
    """HTTP-Schnittstelle, Panel-Dateien und Seitenleisten-Eintrag (einmalig)."""
    data = hass.data.setdefault(DOMAIN, {})
    if not data.get("http_ready"):
        hass.http.register_view(ManometerApiView())
        from homeassistant.components.http import StaticPathConfig  # noqa: PLC0415
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(Path(__file__).parent / "frontend"), False)])
        data["http_ready"] = True
    from homeassistant.components import panel_custom  # noqa: PLC0415
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL,
        webcomponent_name="manometer-leser-panel",
        sidebar_title="Manometer",
        sidebar_icon="mdi:gauge",
        module_url=f"{STATIC_URL}/panel.js?v={VERSION}",
        embed_iframe=True,
        require_admin=True,
        config={},
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    core = ManometerCore(hass, entry.entry_id, _opt(entry, CONF_CAMERA), _opt(entry, CONF_LIGHT))
    await core.async_load()
    coordinator = ManometerCoordinator(hass, entry, core)
    hass.data.setdefault(DOMAIN, {}).setdefault("entries", {})[entry.entry_id] = {"core": core, "coordinator": coordinator}
    entry.runtime_data = coordinator

    # erste Messung im Hintergrund, damit der Start von Home Assistant nicht wartet
    coordinator.async_set_updated_data(core.status())
    entry.async_create_background_task(hass, coordinator.async_refresh(), "manometer_leser_first_measure")

    entry.async_on_unload(async_track_time_interval(hass, core.async_led_tick, timedelta(seconds=5)))
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await _async_setup_frontend(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data.get(DOMAIN, {}).get("entries", {}).pop(entry.entry_id, None)
        if not hass.data.get(DOMAIN, {}).get("entries"):
            from homeassistant.components.frontend import async_remove_panel  # noqa: PLC0415
            async_remove_panel(hass, PANEL_URL)
    return ok


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Version 1 (externer Manometer-Leser) lässt sich nicht übernehmen."""
    if entry.version < 2:
        _LOGGER.error("Manometer-Leser: Bitte die alte Integration entfernen und neu hinzufügen "
                      "(ab Version 2 läuft alles in Home Assistant, Kamera-Entität statt Adresse).")
        return False
    return True
