"""Knöpfe „Jetzt messen“ und „Wasser nachgefüllt“."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import ManometerEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([MeasureButton(entry.runtime_data), RefillButton(entry.runtime_data)])


class MeasureButton(ManometerEntity, ButtonEntity):
    _attr_name = "Jetzt messen"
    _attr_icon = "mdi:camera-iris"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "jetzt_messen")

    async def async_press(self) -> None:
        # Messung dauert mit LED-Vorlauf ca. 10 s – im Hintergrund, der Knopf kehrt sofort zurück
        self.hass.async_create_task(self.coordinator.async_request_refresh())


class RefillButton(ManometerEntity, ButtonEntity):
    """Vermerk „Wasser nachgefüllt“ mit dem aktuellen Zeitpunkt, danach sofort nachmessen."""

    _attr_name = "Wasser nachgefüllt"
    _attr_icon = "mdi:water-plus"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "vermerk_nachgefuellt")

    async def async_press(self) -> None:
        await self.coordinator.core.async_add_note("nachgefuellt")
        if self.coordinator.core.evaluator is not None:
            self.hass.async_create_task(self.coordinator.async_request_refresh())
