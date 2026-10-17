#!/usr/bin/env bash
# Installiert den Manometer-Leser ohne Docker (Debian/Ubuntu, z. B. Proxmox-LXC).
# Aufruf als root:   bash install.sh
# Erneut ausführen = Update. Einstellungen, Kalibrierung und Verlauf in /opt/manometer/data bleiben erhalten.
set -euo pipefail

APP=/opt/manometer
PORT=8080
SRC="$(cd "$(dirname "$0")" && pwd)"

[ "$(id -u)" -eq 0 ] || { echo "Bitte als root ausführen (z. B. mit sudo)."; exit 1; }
[ -f "$SRC/app/server.py" ] || { echo "app/server.py nicht gefunden – install.sh im entpackten Ordner aufrufen."; exit 1; }

echo "==> Pakete installieren"
apt-get update -q
apt-get install -y -q python3 python3-venv python3-pip

echo "==> Benutzer und Ordner anlegen"
id manometer &>/dev/null || useradd --system --home-dir "$APP" --shell /usr/sbin/nologin manometer
mkdir -p "$APP/data"
rm -rf "$APP/app"
cp -r "$SRC/app" "$APP/app"
cp "$SRC/requirements.txt" "$APP/"

echo "==> Python-Umgebung einrichten (dauert beim ersten Mal ein paar Minuten)"
[ -x "$APP/venv/bin/python" ] || python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install -q --upgrade pip
"$APP/venv/bin/pip" install -q -r "$APP/requirements.txt"
chown -R manometer:manometer "$APP"

echo "==> Dienst einrichten"
cat > /etc/systemd/system/manometer.service <<EOF
[Unit]
Description=Manometer-Leser (Heizungsdruck per Kamera)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=manometer
WorkingDirectory=$APP/app
Environment=PYTHONUNBUFFERED=1
ExecStart=$APP/venv/bin/python $APP/app/server.py --data $APP/data --port $PORT
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable manometer >/dev/null
systemctl restart manometer
sleep 3

if systemctl is-active --quiet manometer; then
  IP="$(hostname -I | awk '{print $1}')"
  echo
  echo "Fertig! Weboberfläche:  http://${IP}:${PORT}"
  echo "Log ansehen:            journalctl -u manometer -f"
else
  echo
  echo "Der Dienst ist nicht gestartet. Fehlermeldung:"
  journalctl -u manometer -n 30 --no-pager
  exit 1
fi
