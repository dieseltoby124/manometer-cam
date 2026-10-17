"""HTTP-Schnittstelle für das Seitenleisten-Panel (nur für angemeldete Home-Assistant-Benutzer)."""
from __future__ import annotations

import json
import logging

from aiohttp import web

from homeassistant.components.http import HomeAssistantView

from .const import API_BASE, DOMAIN, HISTORY_DAYS
from .gauge import CalibrationError

_LOGGER = logging.getLogger(__name__)


def _core(hass):
    entries = hass.data.get(DOMAIN, {}).get("entries", {})
    return next(iter(entries.values()), None)


class ManometerApiView(HomeAssistantView):
    url = API_BASE + "/{action:[a-z_/.]+}"
    name = "api:manometer_leser"
    requires_auth = True

    def _err(self, msg: str, status: int = 400) -> web.Response:
        return self.json({"error": msg}, status_code=status)

    async def get(self, request: web.Request, action: str) -> web.Response:
        hass = request.app["hass"]
        entry_data = _core(hass)
        if entry_data is None:
            return self._err("Manometer-Leser ist nicht eingerichtet", 404)
        core = entry_data["core"]
        if action == "status":
            return self.json(core.status())
        if action == "history":
            try:
                hours = float(request.query.get("hours", "24"))
            except ValueError:
                hours = 24
            return self.json(core.history_since(min(hours, HISTORY_DAYS * 24)))
        if action == "calibration":
            return self.json(core.calibration())
        if action == "settings":
            return self.json(core.public_settings())
        if action in ("img/last.jpg", "img/calib.jpg"):
            if action == "img/last.jpg":
                data = core.last_jpg
            else:
                from .gauge import encode_jpeg  # noqa: PLC0415
                data = (await hass.async_add_executor_job(encode_jpeg, core.calib_img, 92)
                        if core.calib_img is not None else None)
            if not data:
                return self._err("Noch kein Bild", 404)
            return web.Response(body=data, content_type="image/jpeg", headers={"Cache-Control": "no-store"})
        return self._err("Nicht gefunden", 404)

    async def post(self, request: web.Request, action: str) -> web.Response:
        hass = request.app["hass"]
        entry_data = _core(hass)
        if entry_data is None:
            return self._err("Manometer-Leser ist nicht eingerichtet", 404)
        core, coordinator = entry_data["core"], entry_data["coordinator"]
        try:
            raw = await request.text()
            body = json.loads(raw) if raw else {}
            if action == "snapshot":
                size = await core.async_snapshot()
                return self.json({"ok": True, "image_size": size})
            if action == "calibration/test":
                return self.json(await hass.async_add_executor_job(core.calib_test, body))
            if action == "calibration/save":
                await core.async_calib_save(body)
                hass.async_create_task(coordinator.async_request_refresh())
                return self.json({"ok": True})
            if action == "rotate":
                return self.json(await core.async_set_rotation(body.get("rotate", 0), body.get("points")))
            if action == "measure":
                hass.async_create_task(coordinator.async_request_refresh())
                return self.json({"ok": True})
            if action == "settings":
                await core.async_update_settings(body)
                return self.json({"ok": True})
            return self._err("Nicht gefunden", 404)
        except CalibrationError as err:
            return self._err(str(err))
        except (RuntimeError, ValueError) as err:
            return self._err(str(err), 502)
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("Fehler bei %s", action)
            return self._err(f"Interner Fehler: {err}", 500)
