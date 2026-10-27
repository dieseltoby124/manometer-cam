"""Konstanten für die Manometer-Leser-Integration."""
DOMAIN = "manometer_leser"
VERSION = "2.2.0"

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
    "homekit": {"enabled": False, "scale10": True},   # ×10: Home-App rundet °C auf halbe Grad
    "gauge": {},
}

# HomeKit-Sensoren (Druck als „Temperatur“, Alarme als Wassermelder)
HOMEKIT_ALARM_DELAY_S = 15 * 60                    # Druck so lange außerhalb, bevor der Wassermelder auslöst
HOMEKIT_OUTAGE_S = 60 * 60                         # so lange kein gültiger Wert → „Messung ausgefallen“
HOMEKIT_KEYS = {                                   # unique_id-Schlüssel je Plattform (zum Aufräumen beim Abschalten)
    "sensor": ["homekit_druck"],
    "binary_sensor": ["homekit_zu_niedrig", "homekit_zu_hoch", "homekit_ausgefallen"],
}

# Vermerke (z. B. „Wasser nachgefüllt“)
NOTE_KINDS = {
    "nachgefuellt": "Wasser nachgefüllt",
    "abgelassen": "Druck abgelassen",
    "entlueftet": "Heizkörper entlüftet",
    "wartung": "Wartung",
    "sonstiges": "Sonstiges",
}
NOTE_EFFECT_WINDOW_S = 6 * 3600                    # Druck vorher/nachher: Messwerte bis 6 h vor bzw. nach dem Vermerk
EVENT_NOTE = "manometer_leser_vermerk"
