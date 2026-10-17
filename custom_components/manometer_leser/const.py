"""Konstanten für die Manometer-Leser-Integration."""
DOMAIN = "manometer_leser"
VERSION = "2.0.0"

CONF_CAMERA = "camera_entity"
CONF_LIGHT = "light_entity"

PANEL_URL = "manometer-leser"                      # /manometer-leser in der Seitenleiste
STATIC_URL = "/manometer_leser_frontend"
API_BASE = "/api/manometer_leser"

HISTORY_DAYS = 30
LED_WARMUP_S = 2.5                                 # Zeit für die automatische Belichtung nach dem Einschalten
PREVIEW_HOLD_S = 30                                # LED bleibt nach dem letzten Vorschau-Bild so lange an

DEFAULT_SETTINGS = {
    "interval_min": 5,
    "samples": 3,
    "camera": {"led_control": True, "led_brightness": 15, "rotate": 0},
    "range": {"low": 1.0, "high": 2.5},
    "mqtt": {"enabled": False, "topic_prefix": "heizung/manometer"},
    "gauge": {},
}
