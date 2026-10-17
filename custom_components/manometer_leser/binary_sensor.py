"""Problem-Sensoren: Druck außerhalb des Normalbereichs, Messung gestört."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import coordinator as c
from .entity import ManometerEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    async_add_entities([OutOfRangeSensor(coordinator), MeasurementProblemSensor(coordinator)])


class OutOfRangeSensor(ManometerEntity, BinarySensorEntity):
    """An, wenn der Druck außerhalb des im Manometer-Leser eingestellten Normalbereichs liegt."""

    _attr_name = "Druck außerhalb Normalbereich"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:gauge-full"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "ausserhalb_normalbereich")

    @property
    def available(self) -> bool:
        return super().available and c.value_fresh(self.coordinator.data or {})

    @property
    def is_on(self) -> bool | None:
        return c.out_of_range(self.coordinator.data or {})

    @property
    def extra_state_attributes(self) -> dict:
        rng = (self.coordinator.data or {}).get("range") or {}
        return {"untergrenze_bar": rng.get("low"), "obergrenze_bar": rng.get("high")}


class MeasurementProblemSensor(ManometerEntity, BinarySensorEntity):
    """An, wenn die letzte Messung fehlgeschlagen ist oder schon zu lange zurückliegt."""

    _attr_name = "Messung gestört"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "messung_gestoert")

    @property
    def is_on(self) -> bool:
        data = self.coordinator.data or {}
        return c.error_text(data) != "ok" or not c.value_fresh(data)
