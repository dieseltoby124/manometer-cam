"""Konstanten für die Manometer-Leser-Integration."""
from datetime import timedelta

DOMAIN = "manometer_leser"
DEFAULT_PORT = 8080
SCAN_INTERVAL = timedelta(seconds=30)

CONF_URL = "url"
CONF_PASSWORD = "password"
