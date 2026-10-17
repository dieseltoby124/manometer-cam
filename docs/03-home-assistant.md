# 3. Home Assistant

Es gibt zwei Wege. Nimm **einen** davon, sonst erscheint das Gerät doppelt:

| | Integration (empfohlen) | MQTT |
|---|---|---|
| Einrichtung | über HACS, dann *Integration hinzufügen* | MQTT-Broker nötig, Gerät erscheint automatisch |
| Inhalt | Druck, Güte, letzte Messung, Fehler, **Problem-Sensor Normalbereich**, **Kamerabild**, **Knopf „Jetzt messen“** | Druck, Güte, Fehler |

## Weg A: Integration „Manometer-Leser“

### Installieren
**Über HACS:** HACS → **⋮ → Benutzerdefinierte Repositories** → `https://github.com/dieseltoby124/manometer-cam`, Typ **Integration** → hinzufügen → „Manometer-Leser“ **herunterladen** → Home Assistant **neu starten**.

**Von Hand:** Den Ordner `custom_components/manometer_leser` nach `/config/custom_components/` kopieren (File-Editor- oder Samba-Add-on), neu starten.

### Einrichten
*Einstellungen → Geräte & Dienste → Integration hinzufügen → „Manometer-Leser“*
- **Adresse:** z. B. `192.168.77.20`. Port 8080 wird ergänzt.
- **Passwort:** nur, wenn im Manometer-Leser ein Zugriffsschutz gesetzt ist. Wird es später geändert, fragt Home Assistant nach dem neuen.

### Entitäten (Gerät „Heizung Manometer“)

| Entität | Beispiel-ID |
|---|---|
| Anlagendruck (bar) | `sensor.heizung_manometer_anlagendruck` |
| Druck außerhalb Normalbereich (Problem) | `binary_sensor.heizung_manometer_druck_ausserhalb_normalbereich` |
| Jetzt messen | `button.heizung_manometer_jetzt_messen` |
| Letztes Bild | `camera.heizung_manometer_letztes_bild` |
| Erkennungsgüte, Letzte Messung, Letzter Fehler, Messung gestört | Diagnose |

Die IDs können bei dir leicht abweichen, sie stehen beim Gerät. Der **Anlagendruck** wird „nicht verfügbar“, wenn länger als 3 Messintervalle kein gültiger Wert kam. Ein veralteter Wert zeigt also nie fälschlich „alles ok“.

> Sendet der Manometer-Leser zusätzlich per MQTT an Home Assistant, in seinen MQTT-Einstellungen das Häkchen **„Home Assistant“** entfernen und das alte MQTT-Gerät löschen.

## Weg B: MQTT

Im Manometer-Leser unter *Einstellungen → MQTT* eine Verbindung zum Broker anlegen und **„Home Assistant“** anhaken. Das Gerät „Heizung Manometer“ erscheint dann unter *MQTT*. Details: [05-mqtt.md](05-mqtt.md).

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

  - alias: "Manometer-Leser liefert keine Werte"
    trigger:
      - platform: state
        entity_id: sensor.heizung_manometer_anlagendruck
        to: "unavailable"
        for: "01:00:00"
    action:
      - service: notify.notify
        data:
          message: "Manometer-Leser liefert seit 1 h keine Werte – Weboberfläche prüfen."
```

Beim MQTT-Weg gibt es den Problem-Sensor nicht. Dort stattdessen auf `numeric_state` mit `below: 1.0` / `above: 2.5` beim Anlagendruck auslösen.

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

Dann in der **HomeKit-Bridge** nur diese vier Entitäten freigeben. In der Home-App einen eigenen Raum wie „Heizungsraum“ anlegen, sonst fließt der „Temperatur“-Wert in den Raum-Durchschnitt ein.
