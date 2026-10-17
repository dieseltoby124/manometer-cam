# Manometer-Leser für Home Assistant

Home-Assistant-Integration für den **Manometer-Leser**: Er liest das analoge Heizungsmanometer per ESP32-CAM ab. Die Integration holt die Werte direkt über die Weboberfläche des Manometer-Lesers, MQTT ist dafür nicht nötig.

## Was du bekommst

Gerät **„Heizung Manometer“** mit:

| Entität | Art | Beschreibung |
|---|---|---|
| Anlagendruck | Sensor (bar) | aktueller Druck; „nicht verfügbar“, wenn länger als 3 Messintervalle kein gültiger Wert kam |
| Druck außerhalb Normalbereich | Problem-Sensor | an, wenn der Druck außerhalb des im Manometer-Leser eingestellten grünen Bereichs liegt |
| Jetzt messen | Knopf | löst sofort eine Messung aus |
| Letztes Bild | Kamera | letztes Kamerabild mit eingezeichnetem Zeiger |
| Erkennungsgüte | Sensor (%), Diagnose | wie sicher der Zeiger erkannt wurde |
| Letzte Messung | Zeitstempel, Diagnose | Zeit der letzten gültigen Messung |
| Letzter Fehler | Text, Diagnose | „ok“ oder die Fehlermeldung |
| Messung gestört | Problem-Sensor, Diagnose | an bei Fehler oder veraltetem Wert |

Die Werte werden alle 30 Sekunden abgefragt, gemessen wird im Manometer-Leser (Standard: alle 5 Minuten).

## Installation über HACS

1. HACS öffnen → oben rechts **⋮ → Benutzerdefinierte Repositories**
2. Repository: `https://github.com/DEIN-NAME/ha-manometer-leser`, Typ: **Integration** → Hinzufügen
3. „Manometer-Leser“ suchen → **Herunterladen**
4. Home Assistant **neu starten**

## Installation von Hand

Den Ordner `custom_components/manometer_leser` nach `/config/custom_components/manometer_leser` kopieren (z. B. mit dem File-Editor- oder Samba-Add-on) und Home Assistant neu starten.

## Einrichten

*Einstellungen → Geräte & Dienste → Integration hinzufügen → „Manometer-Leser“*

- **Adresse:** IP des Manometer-Lesers, z. B. `192.168.77.20` (Port 8080 wird ergänzt) oder `http://192.168.77.20:8080`
- **Passwort:** nur, wenn im Manometer-Leser unter *Zugriffsschutz* eins gesetzt ist

Änderst du das Passwort später im Manometer-Leser, fragt Home Assistant automatisch nach dem neuen.

## Wichtig: MQTT doppelt?

Sendet der Manometer-Leser zusätzlich per MQTT an Home Assistant (Häkchen „Home Assistant“ bei einer MQTT-Verbindung), hast du das Gerät zweimal. Dann im Manometer-Leser unter *Einstellungen → MQTT* das Häkchen **„Home Assistant“** entfernen (ioBroker usw. bleiben) und das alte MQTT-Gerät in Home Assistant löschen.

## Beispiel-Automation

```yaml
automation:
  - alias: "Heizungsdruck außerhalb Normalbereich"
    trigger:
      - platform: state
        entity_id: binary_sensor.heizung_manometer_druck_ausserhalb_normalbereich
        to: "on"
        for: "00:15:00"
    action:
      - service: notify.notify
        data:
          title: "Heizung"
          message: >
            Anlagendruck {{ states('sensor.heizung_manometer_anlagendruck') }} bar –
            außerhalb des Normalbereichs.
```

Die Entitäts-IDs können bei dir leicht abweichen. Du findest sie beim Gerät „Heizung Manometer“.

Voraussetzung: Home Assistant 2025.1 oder neuer, Manometer-Leser ab Version 2026.10.17.4.
