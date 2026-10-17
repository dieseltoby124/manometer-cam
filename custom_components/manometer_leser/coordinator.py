"""Messintervall: der Coordinator löst die Messungen aus und verteilt den Status an die Entitäten."""
from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN
from .core import ManometerCore

_LOGGER = logging.getLogger(__name__)


class ManometerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, core: ManometerCore) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry,
                         update_interval=timedelta(seconds=core.interval_s))
        self.core = core
        core.listeners.append(self._on_core_change)

    async def _async_update_data(self) -> dict[str, Any]:
        """Eine Messung durchführen. Fehler landen im Status, nicht als Ausnahme."""
        return await self.core.async_measure()

    def _on_core_change(self) -> None:
        """Kalibrierung oder Einstellungen geändert: Intervall anpassen, Entitäten aktualisieren."""
        new = timedelta(seconds=self.core.interval_s)
        if new != self.update_interval:
            self.update_interval = new
        self.async_set_updated_data(self.core.status())


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
