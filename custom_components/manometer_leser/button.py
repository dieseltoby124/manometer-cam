"""Knopf „Jetzt messen“."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .api import ManometerError
from .entity import ManometerEntity

REFRESH_AFTER_S = 15     # Messung dauert mit LED-Vorlauf und 3 Bildern ca. 10 s


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([MeasureButton(entry.runtime_data)])


class MeasureButton(ManometerEntity, ButtonEntity):
    _attr_name = "Jetzt messen"
    _attr_icon = "mdi:camera-iris"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "jetzt_messen")

    async def async_press(self) -> None:
        try:
            await self.coordinator.api.measure()
        except ManometerError as err:
            raise HomeAssistantError(f"Manometer-Leser: {err}") from err

        async def _refresh(_now) -> None:
            await self.coordinator.async_request_refresh()

        async_call_later(self.hass, REFRESH_AFTER_S, _refresh)
