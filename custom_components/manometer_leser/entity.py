"""Gemeinsame Basis für alle Entitäten des Manometer-Lesers."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ManometerCoordinator


class ManometerEntity(CoordinatorEntity[ManometerCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: ManometerCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        base = entry.unique_id or entry.entry_id
        self._attr_unique_id = f"{base}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, base)},
            name="Heizung Manometer",
            manufacturer="DIY",
            model="ESP32-CAM + Manometer-Leser",
            sw_version=(coordinator.data or {}).get("version"),
            configuration_url=coordinator.api.url,
        )
