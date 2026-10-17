# 1. ESP32-CAM einrichten

Die Kamera läuft mit **ESPHome**. Sie liefert Einzelbilder an den Manometer-Leser und stellt eine kleine Statusseite bereit, über die der Manometer-Leser die LED schaltet.

## Hardware

- **ESP32-CAM (AI-Thinker)** mit OV2640-Kamera und 4 MB PSRAM
- **ESP32-CAM-MB**: das Aufsteckboard mit USB-Anschluss. Kamera-Platine aufstecken, die Kamera zeigt nach oben.
- **USB-Datenkabel** (reine Ladekabel funktionieren nicht) und für den Betrieb ein Netzteil mit **mind. 1 A**

Für den Dauerbetrieb kann die Kamera auf dem MB-Board bleiben.

## Firmware anlegen

1. ESPHome öffnen (Add-on „ESPHome Device Builder“ in Home Assistant oder [web.esphome.io](https://web.esphome.io)).
2. **Neues Gerät**, Name `manometer-cam`, Typ **ESP32**.
3. Bearbeiten, alles ersetzen durch den Inhalt von [`esphome/manometer-cam.yaml`](../esphome/manometer-cam.yaml), speichern.
4. Oben rechts **Secrets** öffnen und die Einträge aus [`esphome/secrets.example.yaml`](../esphome/secrets.example.yaml) mit deinen Werten ergänzen:
   - `wifi_ssid`, `wifi_password`: dein WLAN
   - `api_key`: 32 zufällige Bytes als Base64, z. B. mit `openssl rand -base64 32`
   - `ota_password`: ein beliebiges Passwort für spätere Updates per WLAN

> Fehlt ein Eintrag, meldet ESPHome `Secret '…' not defined`.

## Erstes Flashen (per USB)

1. In ESPHome **Install → Manual download → Factory format** wählen. Das erste Kompilieren dauert einige Minuten.
2. Die Kamera per USB an den PC anschließen.
3. Im Chrome oder Edge [web.esphome.io](https://web.esphome.io) öffnen → **Connect** → USB-Port wählen (meist „CH340“ oder „USB Serial“) → **Install** → die heruntergeladene `.bin` wählen.
4. Klappt die Verbindung nicht: **IO0** am MB-Board gedrückt halten, kurz **RST** an der Kamera-Platine drücken, IO0 loslassen, erneut verbinden.
5. Nach dem Flashen **RST** drücken.

Steckt die Kamera direkt am Rechner, auf dem ESPHome läuft, geht auch **Install → Plug into the computer running ESPHome Dashboard**. Alle weiteren Updates gehen per WLAN mit **Install → Wirelessly**.

## Prüfen

- Im ESPHome-Log erscheinen `WiFi Connected` mit **IP-Adresse** und am Ende `Boot seems successful`.
- `http://<Kamera-IP>:8081/` zeigt ein Standbild.
- `http://<Kamera-IP>/` zeigt die Statusseite.
- Im Router der Kamera eine **feste IP** geben.
- Home Assistant findet die Kamera meist selbst. Beim Hinzufügen fragt es nach dem Schlüssel, das ist der `api_key`.

## Montage

- Manometer **mittig** im Bild, Kamera möglichst **gerade** davor, ca. 10–20 cm Abstand.
- Unscharf aus der Nähe? Das Objektiv lässt sich vorsichtig drehen und damit scharf stellen.
- Die Kamera muss danach **fest** sitzen. Kleine Verschiebungen gleicht der Manometer-Leser aus, große nicht.
- Steht das Bild durch die Einbaulage schief oder auf dem Kopf, drehst du es im Manometer-Leser beim Kalibrieren (↺ / ↻).
- Die LED ist normalerweise aus und geht nur während einer Messung kurz an. Spiegelt das Glas, die Helligkeit im Manometer-Leser unter *Einstellungen → Kamera* reduzieren oder die Kamera minimal schräg stellen.

## Wichtige Details der Konfiguration

| Teil | Warum |
|---|---|
| `psram:` (`mode: quad`, `speed: 80MHz`) | Neuere ESPHome-Versionen brauchen das ausdrücklich, sonst: „This option requires component psram“ |
| `i2c_pins:` unter `esp32_camera` | ESPHome warnt „deprecated“. Die neue Variante mit `i2c:` + `i2c_id` lieferte bei ESPHome 2026.9 keine Bilder („Got invalid frame from camera“). Die Warnung ist harmlos |
| `web_server:` auf Port 80 | **Nicht entfernen**: Darüber schaltet der Manometer-Leser die LED |
| Licht-Name `Beleuchtung` | Der Manometer-Leser schaltet die LED unter diesem Namen |
| `restore_mode: ALWAYS_OFF` | LED nach einem Neustart aus |
| Warnungen zu GPIO0/GPIO5 | Hinweis auf „Strapping-Pins“, bei der ESP32-CAM normal und harmlos |

## Fehlersuche

| Problem | Lösung |
|---|---|
| `Got invalid frame from camera!` | 1. `i2c_pins:` wie in der Vorlage verwenden. 2. `frequency: 10MHz` statt 20 MHz testen. 3. Flachbandkabel der Kamera prüfen: Strom ab, Klappe am Stecker öffnen, Kabel gerade ganz einschieben, schließen |
| `PSRAM not found` / `Camera init failed` | Manche Nachbauten haben keinen PSRAM: Log-Ausgabe prüfen, ggf. `psram:` entfernen und `resolution: 640x480` setzen |
| Neustarts, Verbindungsabbrüche | Netzteil zu schwach → mind. 1 A, kurzes Kabel |
| Kamera nicht erreichbar | IP im Router prüfen, Kamera und Server im selben Netz? |
| Bild zu dunkel/hell bei Tageslicht | Kamera seitlich abschirmen oder in der Konfiguration `aec_mode: MANUAL` aktivieren |
