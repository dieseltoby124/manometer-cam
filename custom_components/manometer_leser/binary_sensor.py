"""Problem-Sensoren: Druck außerhalb des Normalbereichs, Messung gestört (+ optional Wassermelder für HomeKit)."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from . import coordinator as c
from .core import homekit_eval
from .entity import ManometerEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [OutOfRangeSensor(coordinator), MeasurementProblemSensor(coordinator)]
    if coordinator.core.homekit_enabled:
        entities += [HomeKitAlarm(coordinator, k) for k in HOMEKIT_ALARMS]
    async_add_entities(entities)


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


# Wassermelder für HomeKit: Nur für „Wasser erkannt“ schickt HomeKit kritische Mitteilungen,
# die auch bei „Nicht stören“ durchkommen.
HOMEKIT_ALARMS = {
    #  Schlüssel        (unique_id-Suffix, Name in der Home-App)
    "low":    ("homekit_zu_niedrig", "Heizungsdruck zu niedrig"),
    "high":   ("homekit_zu_hoch", "Heizungsdruck zu hoch"),
    "outage": ("homekit_ausgefallen", "Heizungsdruck Messung ausgefallen"),
}


class HomeKitAlarm(ManometerEntity, BinarySensorEntity):
    """Wassermelder für die HomeKit-Bridge (zu niedrig / zu hoch: nach 15 Min., ausgefallen: nach 1 h)."""

    _attr_has_entity_name = False
    _attr_device_class = BinarySensorDeviceClass.MOISTURE

    def __init__(self, coordinator, kind: str) -> None:
        key, name = HOMEKIT_ALARMS[kind]
        super().__init__(coordinator, key)
        self._kind = kind
        self._attr_name = name
        self._attr_icon = "mdi:gauge-empty" if kind == "low" else "mdi:gauge-full" if kind == "high" else "mdi:camera-off"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.coordinator.core.homekit_entities[self._kind] = self.entity_id
        # Die Verzögerung läuft auch zwischen zwei Messungen weiter → jede Minute neu bewerten
        self.async_on_remove(async_track_time_interval(
            self.hass, lambda _now: self.async_write_ha_state(), timedelta(minutes=1)))

    @property
    def is_on(self) -> bool:
        return homekit_eval(self.coordinator.data or {})[self._kind]

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        if self._kind == "outage":
            return {}
        rng = data.get("range") or {}
        since = (data.get("homekit") or {}).get(f"{self._kind}_since")
        return {
            "grenze_bar": rng.get(self._kind),
            "ausserhalb_seit": dt_util.utc_from_timestamp(since).isoformat() if since else None,
        }
