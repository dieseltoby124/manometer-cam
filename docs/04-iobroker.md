# 4. ioBroker

ioBroker bekommt die Werte per **MQTT**. Der Manometer-Leser sendet dafür einfache Einzelwerte, die ioBroker direkt als Zahlen-Datenpunkte übernimmt.

## Manometer-Leser
*Einstellungen → MQTT → Verbindung hinzufügen:* Server, Benutzer und Passwort deines Brokers eintragen und **„ioBroker“** anhaken. Läuft Home Assistant über denselben Broker, zusätzlich „Home Assistant“ anhaken, falls du dort nicht die Integration nutzt.

## ioBroker: MQTT-Adapter

1. *Adapter* → **MQTT Broker/Client** installieren, Instanz `mqtt.0` öffnen.
2. Reiter **Verbindung**:
   - Typ **Client/Subscriber**, wenn du einen eigenen Broker hast (Mosquitto im Container oder als Home-Assistant-Add-on)
   - oder Typ **Server/Broker**: Dann ist ioBroker selbst der Broker, und im Manometer-Leser trägst du die IP von ioBroker ein
   - Server-IP, Port 1883, Benutzer und Passwort
3. Reiter **Einstellungen**:
   - **Abonnierte Patterns:** `heizung/manometer/#`
   - **Maske zum Bekanntgeben eigener States:** `0_userdata.0.mqtt_aus`. **Nicht leer lassen**, leer bedeutet „alle ioBroker-States an den Broker senden“.
4. Speichern, Instanz starten.

## Datenpunkte

| Datenpunkt | Inhalt |
|---|---|
| `mqtt.0.heizung.manometer.druck` | Druck in bar, z. B. `1.52` |
| `mqtt.0.heizung.manometer.guete` | Erkennungsgüte 0–100 |
| `mqtt.0.heizung.manometer.zeitstempel` | Zeit der letzten gültigen Messung |
| `mqtt.0.heizung.manometer.error` | `ok` oder Fehlermeldung |
| `mqtt.0.heizung.manometer.status` | `online` / `offline` |

Hast du im Manometer-Leser einen anderen Topic-Präfix eingestellt, ändern sich Pattern und Datenpunkt-Namen entsprechend.

## Alarm-Skript (Adapter „JavaScript“)

```javascript
const DRUCK = 'mqtt.0.heizung.manometer.druck';
const MIN = 1.0, MAX = 2.5;
let timer = null, gemeldet = false;

on({ id: DRUCK, change: 'any' }, (obj) => {
    const bar = parseFloat(obj.state.val);
    if (isNaN(bar)) return;
    if (bar < MIN || bar > MAX) {
        if (!timer && !gemeldet) timer = setTimeout(() => {   // erst nach 15 Min. außerhalb melden
            const akt = parseFloat(getState(DRUCK).val);
            sendTo('telegram.0', { text: `Heizungsdruck ${akt.toFixed(2)} bar – ` +
                (akt < MIN ? 'Wasser nachfüllen' : 'zu hoch, Ausdehnungsgefäß prüfen lassen') });
            gemeldet = true; timer = null;                     // nur einmal melden, bis wieder normal
        }, 15 * 60 * 1000);
    } else {
        if (timer) { clearTimeout(timer); timer = null; }
        gemeldet = false;
    }
});
```

`sendTo('telegram.0', …)` an deinen Benachrichtigungsdienst anpassen.
