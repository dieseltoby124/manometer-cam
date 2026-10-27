"""Sensoren: Anlagendruck, Erkennungsgüte, letzte Messung, letzter Fehler (+ optional Druck für HomeKit)."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfPressure, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import coordinator as c
from .entity import ManometerEntity


def _timestamp(data: dict):
    t = c.value_time(data)
    return dt_util.utc_from_timestamp(float(t)) if t else None


def _note_time(data: dict, key: str):
    n = (data.get("notes") or {}).get(key)
    return dt_util.utc_from_timestamp(float(n["time"])) if n else None


def _note_attrs(data: dict, key: str, with_time: bool = False) -> dict:
    n = (data.get("notes") or {}).get(key)
    if not n:
        return {}
    out = {"notiz": n.get("text") or None, "druck_vorher_bar": n.get("before"), "druck_nachher_bar": n.get("after")}
    if with_time:
        out["zeitpunkt"] = dt_util.utc_from_timestamp(float(n["time"])).isoformat()
    return out


@dataclass(frozen=True, kw_only=True)
class ManometerSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict], Any]
    available_fn: Callable[[dict], bool] = lambda d: True
    attrs_fn: Callable[[dict], dict] | None = None


SENSORS: tuple[ManometerSensorDescription, ...] = (
    ManometerSensorDescription(
        key="druck", name="Anlagendruck", icon="mdi:gauge",
        device_class=SensorDeviceClass.PRESSURE, native_unit_of_measurement=UnitOfPressure.BAR,
        state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=2,
        value_fn=c.pressure, available_fn=c.value_fresh,
    ),
    ManometerSensorDescription(
        key="zuletzt_nachgefuellt", name="Zuletzt Wasser nachgefüllt", icon="mdi:water-plus",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: _note_time(d, "last_refill"), attrs_fn=lambda d: _note_attrs(d, "last_refill"),
    ),
    ManometerSensorDescription(
        key="letzter_vermerk", name="Letzter Vermerk", icon="mdi:note-text-outline",
        value_fn=lambda d: ((d.get("notes") or {}).get("last") or {}).get("label"),
        attrs_fn=lambda d: _note_attrs(d, "last", with_time=True),
    ),
    ManometerSensorDescription(
        key="guete", name="Erkennungsgüte", icon="mdi:eye-check-outline",
        native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC, value_fn=c.confidence,
    ),
    ManometerSensorDescription(
        key="letzte_messung", name="Letzte Messung", device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC, value_fn=_timestamp,
    ),
    ManometerSensorDescription(
        key="fehler", name="Letzter Fehler", icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC, value_fn=c.error_text,
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [ManometerSensor(coordinator, d) for d in SENSORS]
    if coordinator.core.homekit_enabled:
        entities.append(HomeKitPressureSensor(coordinator))
    async_add_entities(entities)


class ManometerSensor(ManometerEntity, SensorEntity):
    entity_description: ManometerSensorDescription

    def __init__(self, coordinator, description: ManometerSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        return super().available and self.entity_description.available_fn(self.coordinator.data or {})

    @property
    def native_value(self):
        return self.entity_description.value_fn(self.coordinator.data or {})

    @property
    def extra_state_attributes(self) -> dict | None:
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator.data or {}) if fn else None


class HomeKitPressureSensor(ManometerEntity, SensorEntity):
    """Der Anlagendruck als „Temperatur“: HomeKit kennt keinen Drucksensor und keine Einheit bar.

    Standard: Wert × 10 (2,3 bar → 23 °), weil die Home-App Temperaturen auf halbe Grad rundet
    und aus 2,3 sonst 2,5 macht. Abschaltbar im Panel. Nur für die HomeKit-Bridge gedacht.
    """

    _attr_has_entity_name = False          # kurzer Name, so wie er in der Home-App erscheinen soll
    _attr_name = "Heizungsdruck"
    _attr_icon = "mdi:gauge"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "homekit_druck")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.coordinator.core.homekit_entities["druck"] = self.entity_id

    @property
    def available(self) -> bool:
        return super().available and c.value_fresh(self.coordinator.data or {})

    @property
    def _scale10(self) -> bool:
        return bool(((self.coordinator.data or {}).get("homekit") or {}).get("scale10", True))

    @property
    def suggested_display_precision(self) -> int:
        return 0 if self._scale10 else 1

    @property
    def native_value(self) -> float | None:
        p = c.pressure(self.coordinator.data or {})
        if p is None:
            return None
        return round(p * 10) if self._scale10 else round(p, 1)

    @property
    def extra_state_attributes(self) -> dict:
        p = c.pressure(self.coordinator.data or {})
        return {"druck_bar": round(p, 2) if p is not None else None,
                "hinweis": "23 ° = 2,3 bar (Wert × 10)" if self._scale10 else "Anlagendruck in bar, als Temperatur"}
