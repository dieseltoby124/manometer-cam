"""Sensoren: Anlagendruck, Erkennungsgüte, letzte Messung, letzter Fehler."""
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
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfPressure
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import coordinator as c
from .entity import ManometerEntity


def _timestamp(data: dict):
    t = c.value_time(data)
    return dt_util.utc_from_timestamp(float(t)) if t else None


@dataclass(frozen=True, kw_only=True)
class ManometerSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict], Any]
    available_fn: Callable[[dict], bool] = lambda d: True


SENSORS: tuple[ManometerSensorDescription, ...] = (
    ManometerSensorDescription(
        key="druck", name="Anlagendruck", icon="mdi:gauge",
        device_class=SensorDeviceClass.PRESSURE, native_unit_of_measurement=UnitOfPressure.BAR,
        state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=2,
        value_fn=c.pressure, available_fn=c.value_fresh,
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
    async_add_entities(ManometerSensor(coordinator, d) for d in SENSORS)


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
