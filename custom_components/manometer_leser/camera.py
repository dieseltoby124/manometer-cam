"""Kamera-Entität: letztes ausgewertetes Bild mit eingezeichnetem Zeiger."""
from __future__ import annotations

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import ManometerEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([ManometerCamera(entry.runtime_data)])


class ManometerCamera(ManometerEntity, Camera):
    _attr_name = "Letztes Bild"

    def __init__(self, coordinator) -> None:
        ManometerEntity.__init__(self, coordinator, "letztes_bild")
        Camera.__init__(self)
        self.content_type = "image/jpeg"

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        return await self.coordinator.api.last_image()
