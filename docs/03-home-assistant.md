# 3. Home Assistant

Die Integration **„Manometer-Leser“** erledigt alles in Home Assistant: Sie holt die Bilder von der ESP32-CAM, schaltet die LED, erkennt den Zeiger, stellt die Sensoren bereit und bringt die Oberfläche zum Kalibrieren als Eintrag **„Manometer“** in der Seitenleiste mit.

## Voraussetzungen

- Home Assistant **2025.1** oder neuer, mit [HACS](https://hacs.xyz)
- Die ESP32-CAM ist in Home Assistant eingebunden (ESPHome-Integration) → [01-esp32-cam.md](01-esp32-cam.md). Du solltest beim Gerät „Manometer Cam“ eine **Kamera** und ein **Licht „Beleuchtung“** sehen.

## Installieren

**Über HACS:**

[![In HACS öffnen](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=dieseltoby124&repository=manometer-cam&category=integration)

oder HACS → **⋮ → Benutzerdefinierte Repositories** → `https://github.com/dieseltoby124/manometer-cam`, Typ **Integration** → „Manometer-Leser“ **herunterladen** → Home Assistant **neu starten**.

**Von Hand:** Den Ordner `custom_components/manometer_leser` nach `/config/custom_components/` kopieren und neu starten.

## Einrichten

1. *Einstellungen → Geräte & Dienste → Integration hinzufügen → „Manometer-Leser“*
2. **Kamera** auswählen (z. B. `camera.manometer_cam_manometer`) und **LED** (z. B. `light.manometer_cam_beleuchtung`). Beides ist meist schon vorausgewählt.
3. In der Seitenleiste **„Manometer“** öffnen → **Kalibrieren**:
   1. **Foto aufnehmen.** Mit **Live-Vorschau** die Kamera ausrichten, bei Bedarf mit **↺ / ↻** drehen.
   2. **Mitte der Zeigerachse** antippen. Gedrückt halten zeigt eine Lupe.
   3. Mindestens zwei **Skalenstriche** antippen, z. B. 0 und 4 bar, und den Wert wählen. Ein dritter macht es genauer.
   4. **Zeigerfarbe** wählen: Schwarz, **Weiß/hell** (für dunkle Zifferblätter) oder Rot. Dann **Testen**: Die grüne Linie muss auf dem Zeiger liegen. Passt eine andere Farbe besser, schlägt die Seite sie vor. Dann **Speichern**.
   5. Schritt 5 zeigt **„Fertig – alles eingerichtet“**.

<img src="img/kalibrieren-schritte.png" width="300" alt="Kalibrierschritte">

Kamera oder LED später ändern: *Geräte & Dienste → Manometer-Leser → Konfigurieren*.

### Einstellungen in der Seitenleiste
- **Messen alle … Minuten** (Standard 5), **LED-Helligkeit**, „LED nur während Messung und Vorschau“
- **Normalbereich**: grüner Bereich im Verlauf und Grenze für den Problem-Sensor
- **MQTT** (optional), siehe unten

Gespeichert wird alles unter `/config/manometer_leser/` und ist damit in den Home-Assistant-Backups enthalten.

## Entitäten (Gerät „Heizung Manometer“)

| Entität | Beispiel-ID |
|---|---|
| Anlagendruck (bar) | `sensor.heizung_manometer_anlagendruck` |
| Druck außerhalb Normalbereich (Problem) | `binary_sensor.heizung_manometer_druck_ausserhalb_normalbereich` |
| Jetzt messen | `button.heizung_manometer_jetzt_messen` |
| Letztes Bild (mit erkanntem Zeiger) | `camera.heizung_manometer_letztes_bild` |
| Erkennungsgüte, Letzte Messung, Letzter Fehler, Messung gestört | Diagnose |

Die IDs können bei dir leicht abweichen, sie stehen beim Gerät. Der **Anlagendruck** wird „nicht verfügbar“, wenn länger als 3 Messintervalle kein gültiger Wert kam.

## Benachrichtigungen

```yaml
automation:
  - alias: "Heizungsdruck außerhalb Normalbereich"
    trigger:
      - platform: state
        entity_id: binary_sensor.heizung_manometer_druck_ausserhalb_normalbereich
        to: "on"
        for: "00:15:00"
    action:
      - service: notify.notify            # durch deinen Benachrichtigungsdienst ersetzen
        data:
          title: "Heizung"
          message: "Anlagendruck {{ states('sensor.heizung_manometer_anlagendruck') }} bar – außerhalb des Normalbereichs."

  - alias: "Manometer liefert keine Werte"
    trigger:
      - platform: state
        entity_id: sensor.heizung_manometer_anlagendruck
        to: "unavailable"
        for: "01:00:00"
    action:
      - service: notify.notify
        data:
          message: "Manometer liefert seit 1 h keine Werte – Kamera und Seitenleiste „Manometer“ prüfen."
```

### Schleichenden Druckverlust erkennen

*Helfer → Statistik*, Merkmal „Änderung“, Zeitraum 7 Tage, oder per YAML:

```yaml
sensor:
  - platform: statistics
    name: "Anlagendruck Änderung 7 Tage"
    entity_id: sensor.heizung_manometer_anlagendruck
    state_characteristic: change
    max_age:
      days: 7
    sampling_size: 5000
```

Fällt der Wert z. B. unter −0,3 bar, verliert die Anlage Wasser. Warmes Wasser hat mehr Druck als kaltes (meist 0,2–0,5 bar Unterschied), deshalb Werte zur gleichen Tageszeit vergleichen.

## MQTT für ioBroker & Co. (optional)

Voraussetzung: Die **MQTT-Integration** ist in Home Assistant eingerichtet, z. B. mit dem Mosquitto-Add-on oder einem eigenen Broker. Dann in der Seitenleiste *Manometer → Einstellungen → MQTT* **„Werte zusätzlich per MQTT senden“** anhaken. Topics: [05-mqtt.md](05-mqtt.md), ioBroker: [04-iobroker.md](04-iobroker.md).

## HomeKit (über die HomeKit-Bridge)

HomeKit kennt keinen Drucksensor. Der Druck wird deshalb als „Temperatur“ angezeigt, und die Alarme laufen als **Wassermelder**, weil HomeKit nur dafür **kritische Mitteilungen** schickt, die auch bei „Nicht stören“ durchkommen.

```yaml
template:
  - sensor:
      - name: "Heizungsdruck"
        unique_id: heizungsdruck_homekit
        device_class: temperature
        unit_of_measurement: "°C"
        state: "{{ states('sensor.heizung_manometer_anlagendruck') | float(0) | round(1) }}"
        availability: "{{ has_value('sensor.heizung_manometer_anlagendruck') }}"

  - binary_sensor:
      - name: "Heizungsdruck zu niedrig"
        unique_id: heizungsdruck_niedrig
        device_class: moisture
        delay_on: "00:15:00"
        state: "{{ states('sensor.heizung_manometer_anlagendruck') | float(2) < 1.0 }}"
        availability: "{{ has_value('sensor.heizung_manometer_anlagendruck') }}"

      - name: "Heizungsdruck zu hoch"
        unique_id: heizungsdruck_hoch
        device_class: moisture
        delay_on: "00:15:00"
        state: "{{ states('sensor.heizung_manometer_anlagendruck') | float(2) > 2.5 }}"
        availability: "{{ has_value('sensor.heizung_manometer_anlagendruck') }}"

      - name: "Heizungsdruck Messung ausgefallen"
        unique_id: heizungsdruck_ausfall
        device_class: moisture
        delay_on: "01:00:00"
        state: "{{ not has_value('sensor.heizung_manometer_anlagendruck') }}"
```

In der **HomeKit-Bridge** nur diese vier Entitäten freigeben. In der Home-App einen eigenen Raum wie „Heizungsraum“ anlegen, sonst fließt die „Temperatur“ in den Raum-Durchschnitt ein.

## Fehlersuche

| Problem | Lösung |
|---|---|
| „Kamera nicht erreichbar“ | Ist die ESP32-CAM in Home Assistant online? Kamerabild beim Gerät „Manometer Cam“ prüfen |
| „Zeiger nicht sicher erkannt“ | Zeigerfarbe richtig? Spiegelung → LED-Helligkeit senken. Ggf. neu kalibrieren |
| „Manometer im Bild nicht wiedergefunden“ | Kamera stark verrutscht/verdeckt → ausrichten, neu kalibrieren |
| „LED nicht schaltbar“ | Licht-Entität unter *Konfigurieren* prüfen |
| Panel „Manometer“ fehlt | Home Assistant nach der Installation neu starten; die Seitenleiste ist nur für Administratoren sichtbar |
| Umstieg von Version 1 (externer Server) | Alte Integration entfernen, neu hinzufügen, einmal neu kalibrieren |
