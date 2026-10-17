"""Regelmäßiges Abfragen des Manometer-Lesers."""
from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ManometerApi, ManometerAuthError, ManometerError
from .const import DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class ManometerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: ManometerApi) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL, config_entry=entry)
        self.api = api

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.api.status()
        except ManometerAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ManometerError as err:
            raise UpdateFailed(str(err)) from err


# ---- Hilfsfunktionen, die die Rohdaten auswerten (ohne Home-Assistant-Abhängigkeit testbar)

def pressure(data: dict) -> float | None:
    v = (data.get("last") or {}).get("value")
    return round(float(v), 3) if v is not None else None


def confidence(data: dict) -> float | None:
    v = (data.get("last") or {}).get("confidence")
    return round(float(v)) if v is not None else None


def value_time(data: dict) -> float | None:
    last = data.get("last") or {}
    return last.get("value_time") or (last.get("time") if not last.get("error") else None)


def value_fresh(data: dict, now: float | None = None) -> bool:
    """Gültiger Wert, der nicht älter als 3 Messintervalle (mind. 15 Min.) ist."""
    t = value_time(data)
    if t is None or pressure(data) is None:
        return False
    max_age = max(3 * float(data.get("interval_min") or 5) * 60, 900)
    return ((now or time.time()) - float(t)) <= max_age


def error_text(data: dict) -> str:
    return (data.get("last") or {}).get("error") or "ok"


def out_of_range(data: dict) -> bool | None:
    p, rng = pressure(data), data.get("range") or {}
    if p is None or "low" not in rng or "high" not in rng:
        return None
    return p < float(rng["low"]) or p > float(rng["high"])
