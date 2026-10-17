#!/usr/bin/env python3
"""Manometer-Leser: Messschleife + Weboberfläche + MQTT.

Start:  python3 server.py --data ./data
Danach im Browser http://<server>:8080 öffnen. Alles Weitere (Kamera, Kalibrierung, MQTT)
wird dort eingestellt und unter --data gespeichert.
"""
from __future__ import annotations

import argparse
import base64
import copy
import json
import logging
import math
import os
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np
import yaml

from gauge import Aligner, CalibrationError, Evaluator, Gauge, NeedleDetector, draw_overlay
import updater

log = logging.getLogger("manometer")
STATIC = Path(__file__).parent / "static"
HISTORY_DAYS = 30

DEFAULT_CONFIG = {
    "camera": {"url": "", "led_control": True, "led_brightness": 15, "rotate": 0},
    "interval_min": 5,
    "range": {"low": 1.0, "high": 2.5},          # Normalbereich (grüner Bereich) in bar
    "samples": 3,
    "mqtt": {"brokers": []},
    "web": {"port": 8080, "password": ""},
    "gauge": {},
}


def deep_merge(base: dict, upd: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (upd or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def rotate_img(img: np.ndarray, deg: int) -> np.ndarray:
    """Im Uhrzeigersinn drehen (0/90/180/270)."""
    code = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}.get(deg % 360)
    return img if code is None else cv2.rotate(img, code)


def rotate_point(p, deg: int, W: int, H: int) -> list:
    """Punkt (x, y) eines W×H-Bildes passend zu rotate_img umrechnen."""
    x, y = float(p[0]), float(p[1])
    deg %= 360
    if deg == 90:
        x, y = H - 1 - y, x
    elif deg == 180:
        x, y = W - 1 - x, H - 1 - y
    elif deg == 270:
        x, y = y, W - 1 - x
    return [round(x, 1), round(y, 1)]


def encode_jpg(img: np.ndarray, q: int = 85) -> bytes:
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])[1].tobytes()


# --------------------------------------------------------------------------- MQTT

BROKER_DEFAULTS = {"id": "", "name": "MQTT-Server", "enabled": True, "host": "", "port": 1883,
                   "username": "", "password": "", "topic_prefix": "heizung/manometer",
                   "discovery_prefix": "homeassistant",
                   "targets": {"ha": True, "iobroker": False, "other": False}}


def migrate_mqtt(mqtt_cfg: dict) -> dict:
    """Alte Einzel-Verbindung (bis 2026.10.16) in die Liste übernehmen."""
    if not isinstance(mqtt_cfg, dict) or "brokers" in mqtt_cfg:
        return mqtt_cfg or {"brokers": []}
    if not mqtt_cfg.get("host") and not mqtt_cfg.get("enabled"):
        return {"brokers": []}
    b = {**BROKER_DEFAULTS, **{k: mqtt_cfg[k] for k in BROKER_DEFAULTS if k in mqtt_cfg}}
    b.update(id="b1", name="MQTT-Server", targets={"ha": True, "iobroker": True, "other": False})
    return {"brokers": [b]}


class MqttConn:
    """Eine Verbindung zu einem MQTT-Broker. Was gesendet wird, hängt von den Zielen ab:
       ha       -> Discovery + JSON (state) + error + status
       iobroker -> Einzelwerte (druck, guete, zeitstempel) + error + status
       other    -> JSON + Einzelwerte + error + status (ohne Discovery)
    """

    def __init__(self, cfg: dict, expire: int):
        self.cfg = {**BROKER_DEFAULTS, **cfg, "targets": {**BROKER_DEFAULTS["targets"], **(cfg.get("targets") or {})}}
        self.expire = expire
        self.client = None
        self.connected = False
        self.error = ""
        t = self.cfg["targets"]
        self.want_json = bool(t.get("ha") or t.get("other"))
        self.want_plain = bool(t.get("iobroker") or t.get("other"))
        self.want_disc = bool(t.get("ha"))
        if not self.cfg.get("enabled"):
            return
        if not self.cfg.get("host"):
            self.error = "Kein Server eingetragen"
            return
        if not any(t.values()):
            self.error = "Kein Ziel ausgewählt"
            return
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            self.error = "Python-Paket paho-mqtt fehlt"
            return
        try:
            cid = f"manometer-reader-{self.cfg['id'] or 'x'}"
            try:
                c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid)
            except AttributeError:
                c = mqtt.Client(client_id=cid)
            if self.cfg.get("username"):
                c.username_pw_set(self.cfg["username"], self.cfg.get("password") or None)
            c.will_set(f"{self.prefix}/status", "offline", retain=True)
            c.on_connect = self._on_connect
            c.on_disconnect = self._on_disconnect
            c.reconnect_delay_set(5, 120)
            c.connect_async(self.cfg["host"], int(self.cfg.get("port") or 1883), keepalive=60)   # verbindet im Hintergrund, mit Wiederholung
            c.loop_start()
        except Exception as e:
            log.warning("MQTT %s: Start fehlgeschlagen: %s", self.cfg["name"], e)
            self.error = f"Verbindung fehlgeschlagen: {e}"
            return
        self.client = c

    @property
    def prefix(self) -> str:
        return (self.cfg.get("topic_prefix") or "heizung/manometer").strip("/")

    def _on_connect(self, client, userdata, flags, rc, *a):
        ok = (rc == 0) if isinstance(rc, int) else not rc.is_failure
        self.connected = ok
        if not ok:
            self.error = f"Server lehnt Verbindung ab ({rc}) – Benutzer/Passwort prüfen"
            return
        self.error = ""
        log.info("MQTT %s verbunden", self.cfg["name"])
        client.publish(f"{self.prefix}/status", "online", retain=True)
        self._discovery(client)

    def _on_disconnect(self, client, *a):
        self.connected = False

    def _discovery(self, c) -> None:
        disc, p = (self.cfg.get("discovery_prefix") or "homeassistant").strip("/"), self.prefix
        if not self.want_disc:
            # Bewusst nichts löschen: Eine zweite Verbindung zum selben Broker (z. B. nur ioBroker)
            # würde sonst das Gerät einer Home-Assistant-Verbindung wieder entfernen.
            return
        dev = {"identifiers": ["manometer_reader"], "name": "Heizung Manometer",
               "manufacturer": "DIY", "model": "ESP32-CAM + OpenCV"}
        common = {"availability_topic": f"{p}/status", "device": dev}
        sensors = {
            "druck": {"name": "Anlagendruck", "unique_id": "manometer_druck", "state_topic": f"{p}/state",
                      "device_class": "pressure", "unit_of_measurement": "bar", "state_class": "measurement",
                      "suggested_display_precision": 2, "value_template": "{{ value_json.pressure }}",
                      "expire_after": self.expire},
            "konfidenz": {"name": "Erkennungsgüte", "unique_id": "manometer_konfidenz", "state_topic": f"{p}/state",
                          "entity_category": "diagnostic", "unit_of_measurement": "%",
                          "state_class": "measurement", "value_template": "{{ value_json.confidence }}"},
            "fehler": {"name": "Letzter Fehler", "unique_id": "manometer_fehler", "state_topic": f"{p}/error",
                       "entity_category": "diagnostic"},
        }
        for key, sen in sensors.items():
            c.publish(f"{disc}/sensor/manometer/{key}/config", json.dumps({**common, **sen}), retain=True)

    def publish_state(self, value: float, confidence: float) -> None:
        if not (self.client and self.connected):
            return
        p, pub = self.prefix, self.client.publish
        if self.want_json:
            pub(f"{p}/state", json.dumps({"pressure": round(value, 3), "confidence": round(confidence)}), retain=True)
        if self.want_plain:
            pub(f"{p}/druck", f"{value:.2f}", retain=True)
            pub(f"{p}/guete", str(round(confidence)), retain=True)
            pub(f"{p}/zeitstempel", time.strftime("%Y-%m-%dT%H:%M:%S%z"), retain=True)
        pub(f"{p}/error", "ok", retain=True)

    def publish_error(self, msg: str) -> None:
        if self.client and self.connected:
            self.client.publish(f"{self.prefix}/error", msg[:250], retain=True)

    def status(self) -> dict:
        return {"id": self.cfg["id"], "name": self.cfg["name"], "enabled": bool(self.cfg.get("enabled")),
                "connected": self.connected, "error": self.error}

    def stop(self) -> None:
        if self.client:
            try:
                self.client.publish(f"{self.prefix}/status", "offline", retain=True)
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:
                pass
        self.client, self.connected = None, False


class Mqtt:
    """Verwaltet alle eingetragenen Verbindungen."""

    def __init__(self):
        self.conns: list[MqttConn] = []

    def start(self, cfg: dict, interval_s: float) -> None:
        self.stop()
        expire = int(max(interval_s * 3, 300))
        self.conns = [MqttConn(b, expire) for b in (cfg or {}).get("brokers", [])]

    def publish_state(self, value: float, confidence: float) -> None:
        for c in self.conns:
            c.publish_state(value, confidence)

    def publish_error(self, msg: str) -> None:
        for c in self.conns:
            c.publish_error(msg)

    def status(self) -> list[dict]:
        return [c.status() for c in self.conns]

    def stop(self) -> None:
        for c in self.conns:
            c.stop()
        self.conns = []


# --------------------------------------------------------------------------- LED

class Led:
    """Schaltet die Blitz-LED der ESP32-CAM über die ESPHome-Weboberfläche (Port 80).

    Die LED ist an, solange gemessen wird oder jemand gerade die Vorschau ansieht,
    sonst aus. Bei abgeschalteter LED-Steuerung wird die LED nicht angefasst.
    """
    NAMES = ("Beleuchtung", "beleuchtung")     # Entity-Name (neues ESPHome) bzw. object_id (ältere Versionen)
    WARMUP_S = 2.5                             # Zeit für die automatische Belichtung nach dem Einschalten

    def __init__(self, app: "App"):
        self.app = app
        self.lock = threading.Lock()
        self.is_on: bool | None = None
        self.measuring = False
        self.preview_until = 0.0
        self.retry_at = 0.0
        self.error = ""
        threading.Thread(target=self._loop, daemon=True).start()

    @property
    def enabled(self) -> bool:
        cam = self.app.cfg["camera"]
        return bool(cam.get("led_control", True) and cam.get("url"))

    def _base(self) -> str:
        url = self.app.cfg["camera"]["url"].strip()
        u = urlparse(url if url.startswith("http") else "http://" + url)
        return f"http://{u.hostname}"

    def _send(self, on: bool) -> None:
        pct = min(100, max(1, int(self.app.cfg["camera"].get("led_brightness", 15))))
        action = f"turn_on?brightness={round(pct * 2.55)}" if on else "turn_off"
        last = None
        for name in self.NAMES:
            url = f"{self._base()}/light/{urllib.request.quote(name)}/{action}"
            try:
                req = urllib.request.Request(url, data=b"", method="POST")
                with urllib.request.urlopen(req, timeout=5):
                    pass
                self.is_on, self.error = on, ""
                return
            except urllib.error.HTTPError as e:
                last = e
                if e.code == 404:
                    continue
                break
            except Exception as e:
                last = e
                break
        self.error = f"LED nicht schaltbar ({last}). Ist in ESPHome 'web_server' aktiv?"
        self.retry_at = time.time() + 60          # nicht im Sekundentakt erneut versuchen
        log.warning(self.error)

    def _sync(self, force: bool = False) -> bool:
        """Gewünschten Zustand herstellen. True, wenn die LED gerade eingeschaltet wurde."""
        with self.lock:
            if not self.enabled or (not force and time.time() < self.retry_at):
                return False
            want = self.measuring or time.time() < self.preview_until
            if want == self.is_on:
                return False
            self._send(want)
            return want and self.is_on is True

    def _loop(self) -> None:
        while True:
            time.sleep(1)
            try:
                self._sync()
            except Exception as e:
                log.debug("LED: %s", e)

    def begin_measure(self) -> bool:
        self.measuring = True
        if self._sync(force=True):
            time.sleep(self.WARMUP_S)
            return True
        return False

    def end_measure(self) -> None:
        self.measuring = False
        self._sync(force=True)

    def preview(self, seconds: float = 30) -> bool:
        self.preview_until = max(self.preview_until, time.time() + seconds)
        if self._sync(force=True):
            time.sleep(self.WARMUP_S)
            return True
        return False


# --------------------------------------------------------------------------- Anwendung

class App:
    def __init__(self, data: Path):
        self.data = data
        data.mkdir(parents=True, exist_ok=True)
        self.p_config, self.p_ref = data / "config.yaml", data / "referenz.jpg"
        self.p_calib, self.p_last, self.p_hist = data / "kalibrierbild.jpg", data / "letzte_messung.jpg", data / "verlauf.jsonl"

        cfg = yaml.safe_load(self.p_config.read_text(encoding="utf-8")) if self.p_config.exists() else {}
        cfg = cfg or {}
        cfg["mqtt"] = migrate_mqtt(cfg.get("mqtt"))
        self.cfg = deep_merge(DEFAULT_CONFIG, cfg)
        if not self.cfg.get("instance_id"):           # feste ID, z. B. für die Home-Assistant-Integration
            import uuid
            self.cfg["instance_id"] = uuid.uuid4().hex[:12]
        self.save_config()

        self.lock = threading.RLock()
        self.cam_lock = threading.Lock()
        self.trigger = threading.Event()
        self.last: dict = {}
        self.last_jpg: bytes | None = self.p_last.read_bytes() if self.p_last.exists() else None
        self.calib_img: np.ndarray | None = cv2.imread(str(self.p_calib)) if self.p_calib.exists() else None
        self.measuring = False
        self.history = self._load_history()
        self.evaluator: Evaluator | None = None
        self._build_evaluator()
        self.mqtt = Mqtt()
        self.mqtt.start(self.cfg["mqtt"], self.interval_s)
        self.led = Led(self)

    # ---- Konfiguration
    @property
    def interval_s(self) -> float:
        return max(1.0, float(self.cfg.get("interval_min", 5))) * 60

    def save_config(self) -> None:
        tmp = self.p_config.with_suffix(".tmp")
        tmp.write_text(yaml.safe_dump(self.cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
        os.replace(tmp, self.p_config)

    def _build_evaluator(self) -> None:
        try:
            ref = cv2.imread(str(self.p_ref)) if self.p_ref.exists() else None
            self.evaluator = Evaluator(self.cfg["gauge"], ref)
        except CalibrationError:
            self.evaluator = None

    # ---- Verlauf
    def _load_history(self) -> list[tuple[float, float, float]]:
        out, cutoff = [], time.time() - HISTORY_DAYS * 86400
        if self.p_hist.exists():
            for line in self.p_hist.read_text(encoding="utf-8").splitlines():
                try:
                    t, v, c = json.loads(line)
                    if t >= cutoff:
                        out.append((t, v, c))
                except Exception:
                    continue
            self.p_hist.write_text("".join(json.dumps(list(r)) + "\n" for r in out), encoding="utf-8")
        return out

    def _add_history(self, v: float, c: float) -> None:
        rec = (round(time.time(), 1), round(v, 3), round(c))
        with self.lock:
            self.history.append(rec)
            cutoff = time.time() - HISTORY_DAYS * 86400
            while self.history and self.history[0][0] < cutoff:
                self.history.pop(0)
        with self.p_hist.open("a", encoding="utf-8") as f:
            f.write(json.dumps(list(rec)) + "\n")

    def history_since(self, hours: float, max_points: int = 600) -> list:
        cutoff = time.time() - hours * 3600
        with self.lock:
            pts = [(t, v) for t, v, _ in self.history if t >= cutoff]
        if len(pts) > max_points:                     # auf Zeitfenster mitteln
            step = hours * 3600 / max_points
            buckets: dict[int, list] = {}
            for t, v in pts:
                buckets.setdefault(int((t - cutoff) // step), []).append((t, v))
            pts = [(float(np.mean([p[0] for p in b])), float(np.mean([p[1] for p in b]))) for b in buckets.values()]
        return [[round(t), round(v, 3)] for t, v in pts]

    # ---- Kamera
    def fetch(self, url: str | None = None) -> np.ndarray:
        url = (url or self.cfg["camera"]["url"] or "").strip()
        if not url:
            raise RuntimeError("Keine Kamera-Adresse eingetragen (Einstellungen)")
        if not url.startswith("http"):
            url = "http://" + url
        with self.cam_lock:
            req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=12) as r:
                data = np.frombuffer(r.read(), dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError("Die Kamera hat kein gültiges Bild geliefert")
        return rotate_img(img, int(self.cfg["camera"].get("rotate", 0)))

    # ---- Bild drehen (Kalibrierpunkte und Referenzbilder werden mitgedreht)
    def set_rotation(self, new_rot: int, points: dict | None) -> dict:
        new_rot = int(new_rot) % 360
        if new_rot not in (0, 90, 180, 270):
            raise CalibrationError("Drehung nur in 90°-Schritten möglich")
        with self.lock:
            delta = (new_rot - int(self.cfg["camera"].get("rotate", 0))) % 360
            size = None
            if self.calib_img is not None:
                size = self.calib_img.shape[1::-1]
            elif self.p_ref.exists():
                ref = cv2.imread(str(self.p_ref))
                size = ref.shape[1::-1] if ref is not None else None
            out_points = points
            if delta and size:
                W, H = size
                tp = lambda p: rotate_point(p, delta, W, H)
                g = self.cfg["gauge"]
                if g.get("center"):
                    g["center"] = tp(g["center"])
                    g["scale"] = [{**sp, "point": tp(sp["point"])} for sp in g.get("scale", [])]
                if points and points.get("center"):
                    out_points = {**points, "center": tp(points["center"]),
                                  "scale": [{**sp, "point": tp(sp["point"])} for sp in points.get("scale", [])]}
                for path in (self.p_calib, self.p_ref, self.p_last):
                    if path.exists():
                        im = cv2.imread(str(path))
                        if im is not None:
                            cv2.imwrite(str(path), rotate_img(im, delta))
                if self.calib_img is not None:
                    self.calib_img = rotate_img(self.calib_img, delta)
                if self.p_last.exists():
                    self.last_jpg = self.p_last.read_bytes()
            self.cfg["camera"]["rotate"] = new_rot
            self.save_config()
            self._build_evaluator()
        return {"rotate": new_rot, "points": out_points,
                "image_size": list(self.calib_img.shape[1::-1]) if self.calib_img is not None else None}

    # ---- Messung
    def measure_once(self) -> None:
        with self.lock:
            ev = self.evaluator
            min_conf = ev.gauge.min_confidence if ev else 50
        if ev is None:
            with self.lock:
                self.last = {"time": time.time(), "error": "Noch nicht kalibriert"}
            return
        self.measuring = True
        readings, img_last, rd_last = [], None, None
        try:
            n = max(1, int(self.cfg.get("samples", 3)))
            if self.led.begin_measure():
                self.fetch()                          # erstes Bild nach dem Einschalten verwerfen (evtl. noch dunkel)
            for k in range(n):
                img = self.fetch()
                rd = ev.evaluate(img)
                img_last, rd_last = img, rd
                if not math.isnan(rd.value) and rd.confidence >= min_conf:
                    readings.append(rd)
                if k < n - 1:
                    time.sleep(1.5)
            overlay = draw_overlay(img_last, ev.gauge, rd_last)
            jpg = encode_jpg(overlay)
            self.p_last.write_bytes(jpg)
            if not readings:
                if rd_last.align_score is not None and rd_last.align_score < Aligner.MIN_SCORE:
                    raise RuntimeError("Manometer im Bild nicht wiedergefunden – Kamera verdreht oder verdeckt? "
                                       "Bitte neu kalibrieren.")
                raise RuntimeError(f"Zeiger nicht sicher erkannt (Güte {rd_last.confidence:.0f} %). "
                                   "Spiegelung oder Kamera verschoben?")
            value = float(np.median([r.value for r in readings]))
            conf = float(np.median([r.confidence for r in readings]))
            off = readings[-1].offset
            with self.lock:
                self.last_jpg = jpg
                now = time.time()
                self.last = {"time": now, "value_time": now, "value": value, "confidence": conf,
                             "offset": [round(off[0], 1), round(off[1], 1)], "error": ""}
            self._add_history(value, conf)
            self.mqtt.publish_state(value, conf)
            log.info("%.2f bar (Güte %.0f %%)", value, conf)
        except Exception as e:
            msg = str(e)
            if isinstance(e, OSError) and not isinstance(e, RuntimeError):
                msg = f"Kamera nicht erreichbar ({e})"
            log.warning("Messung fehlgeschlagen: %s", msg)
            with self.lock:
                self.last = {**{k: v for k, v in self.last.items() if k in ("value", "confidence", "value_time")},
                             "time": time.time(), "error": msg, "stale": True}
                if img_last is not None and ev:
                    self.last_jpg = encode_jpg(draw_overlay(img_last, ev.gauge, rd_last))
            self.mqtt.publish_error(msg)
        finally:
            self.measuring = False
            self.led.end_measure()

    def loop(self) -> None:
        time.sleep(2)
        while True:
            self.measure_once()
            self.trigger.wait(self.interval_s)
            self.trigger.clear()

    # ---- Kalibrierung
    def calib_test(self, gauge_cfg: dict) -> dict:
        if self.calib_img is None:
            raise CalibrationError("Erst ein Foto aufnehmen")
        cfg = {**self.cfg["gauge"], **gauge_cfg}
        g = Gauge.from_config(cfg)
        rd = NeedleDetector(g).detect(self.calib_img)
        img = draw_overlay(self.calib_img, g, rd)
        # Passt eine andere Zeigerfarbe deutlich besser? -> Hinweis für die Oberfläche
        suggest = None
        if rd.confidence < 80:
            others = {}
            for mode in ("dark", "light", "red"):
                if mode != g.needle:
                    others[mode] = NeedleDetector(Gauge.from_config({**cfg, "needle": mode})).detect(self.calib_img).confidence
            best = max(others, key=others.get)
            if others[best] >= 50 and others[best] > rd.confidence + 25:
                suggest = {"needle": best, "confidence": others[best]}
        return {"value": rd.value, "confidence": rd.confidence, "suggest": suggest,
                "image": "data:image/jpeg;base64," + base64.b64encode(encode_jpg(img)).decode()}

    def calib_save(self, gauge_cfg: dict) -> None:
        if self.calib_img is None:
            raise CalibrationError("Erst ein Foto aufnehmen")
        new = {**self.cfg["gauge"], **gauge_cfg}
        Gauge.from_config(new)                          # validieren
        cv2.imwrite(str(self.p_ref), self.calib_img)
        with self.lock:
            self.cfg["gauge"] = new
            self.save_config()
            self._build_evaluator()
        self.trigger.set()


# --------------------------------------------------------------------------- Web

def make_handler(app: App):
    class H(BaseHTTPRequestHandler):
        server_version = "Manometer/1.0"

        def log_message(self, fmt, *a):
            log.debug("web: " + fmt, *a)

        # ---- Hilfen
        def _auth(self) -> bool:
            pw = app.cfg["web"].get("password")
            if not pw:
                return True
            hdr = self.headers.get("Authorization", "")
            if hdr.startswith("Basic "):
                try:
                    if base64.b64decode(hdr[6:]).decode().split(":", 1)[1] == pw:
                        return True
                except Exception:
                    pass
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Manometer"')
            self.end_headers()
            return False

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _err(self, msg: str, code: int = 400) -> None:
            self._json({"error": msg}, code)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        # ---- Update (Zip als Rohdaten im Body)
        def _update(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                return self._err("Keine Datei empfangen")
            if n > updater.MAX_SIZE:
                return self._err("Datei ist zu groß")
            data = self.rfile.read(n)
            try:
                old, new = updater.install(data)
            except updater.UpdateError as e:
                return self._err(str(e))
            except Exception as e:
                log.exception("Update fehlgeschlagen")
                return self._err(f"Update fehlgeschlagen: {e}", 500)
            self._json({"ok": True, "from": old, "to": new})

            def shutdown():
                app.led.measuring, app.led.preview_until = False, 0
                app.led._sync(force=True)
                app.mqtt.stop()
            updater.restart_later(shutdown)

        # ---- GET
        def do_GET(self):
            if not self._auth():
                return
            u = urlparse(self.path)
            if u.path in ("/", "/index.html"):
                return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/status":
                with app.lock:
                    last = dict(app.last)
                return self._json({
                    "calibrated": app.evaluator is not None,
                    "tracking": bool(app.evaluator and app.evaluator.aligner),
                    "last": last, "measuring": app.measuring, "interval_min": app.cfg["interval_min"],
                    "mqtt": app.mqtt.status(),
                    "has_last_image": app.last_jpg is not None,
                    "led": {"enabled": app.led.enabled, "on": app.led.is_on, "error": app.led.error},
                    "version": updater.current_version(),
                    "range": app.cfg["range"],
                    "id": app.cfg["instance_id"],
                })
            if u.path == "/api/history":
                hours = float(parse_qs(u.query).get("hours", ["24"])[0])
                return self._json(app.history_since(min(hours, HISTORY_DAYS * 24)))
            if u.path == "/api/calibration":
                g = app.cfg["gauge"]
                size = list(app.calib_img.shape[1::-1]) if app.calib_img is not None else None
                return self._json({"gauge": g, "image_size": size, "rotate": int(app.cfg["camera"].get("rotate", 0))})
            if u.path == "/api/settings":
                c = copy.deepcopy(app.cfg)
                c.pop("gauge", None)
                for b in c["mqtt"]["brokers"]:
                    b["password_set"] = bool(b.get("password"))
                    b["password"] = ""
                c["web"]["password_set"] = bool(c["web"].get("password"))
                c["web"]["password"] = ""
                return self._json(c)
            if u.path == "/img/last.jpg":
                return self._send(200, app.last_jpg, "image/jpeg") if app.last_jpg else self._err("Noch kein Bild", 404)
            if u.path == "/img/calib.jpg":
                if app.calib_img is None:
                    return self._err("Noch kein Foto", 404)
                return self._send(200, encode_jpg(app.calib_img, 92), "image/jpeg")
            self._err("Nicht gefunden", 404)

        # ---- POST
        def do_POST(self):
            if not self._auth():
                return
            path = urlparse(self.path).path
            if path == "/api/update":
                return self._update()
            try:
                body = self._body()
                if path == "/api/rotate":
                    return self._json(app.set_rotation(body.get("rotate", 0), body.get("points")))
                if path == "/api/snapshot":
                    if not body.get("url") and app.led.preview(30):
                        app.fetch()                     # erstes Bild nach dem Einschalten verwerfen
                    img = app.fetch(body.get("url"))
                    app.calib_img = img
                    cv2.imwrite(str(app.p_calib), img)
                    return self._json({"ok": True, "image_size": list(img.shape[1::-1])})
                if path == "/api/calibration/test":
                    return self._json(app.calib_test(body))
                if path == "/api/calibration/save":
                    app.calib_save(body)
                    return self._json({"ok": True})
                if path == "/api/measure":
                    app.trigger.set()
                    return self._json({"ok": True})
                if path == "/api/settings":
                    with app.lock:
                        new = copy.deepcopy(app.cfg)
                        for sect in ("camera", "web"):
                            if sect in body:
                                new[sect].update({k: v for k, v in body[sect].items() if k != "password_set"})
                        if "web" in body and not body["web"].get("password"):   # leeres Passwort = unverändert
                            new["web"]["password"] = app.cfg["web"].get("password", "")
                        if "interval_min" in body:
                            new["interval_min"] = max(1, int(body["interval_min"]))
                        if isinstance(body.get("range"), dict):
                            try:
                                lo, hi = float(body["range"]["low"]), float(body["range"]["high"])
                            except (KeyError, TypeError, ValueError):
                                raise CalibrationError("Normalbereich: bitte zwei Zahlen eintragen")
                            if not (0 <= lo < hi <= 10):
                                raise CalibrationError("Normalbereich: Untergrenze muss kleiner als Obergrenze sein (0–10 bar)")
                            new["range"] = {"low": round(lo, 2), "high": round(hi, 2)}
                        if isinstance(body.get("mqtt"), dict) and isinstance(body["mqtt"].get("brokers"), list):
                            old = {b.get("id"): b for b in app.cfg["mqtt"].get("brokers", [])}
                            reserved = set(old) | {str(b.get("id")) for b in body["mqtt"]["brokers"] if b.get("id")}
                            used, brokers = set(), []
                            for b in body["mqtt"]["brokers"]:
                                bid = str(b.get("id") or "")
                                if not bid or bid in used:          # neue Verbindung -> freie ID vergeben
                                    n = 1
                                    while f"b{n}" in used or f"b{n}" in reserved:
                                        n += 1
                                    bid = f"b{n}"
                                used.add(bid)
                                reserved.add(bid)
                                nb = {**BROKER_DEFAULTS, **{k: v for k, v in b.items() if k in BROKER_DEFAULTS}, "id": bid}
                                nb["targets"] = {k: bool((b.get("targets") or {}).get(k)) for k in BROKER_DEFAULTS["targets"]}
                                nb["port"] = int(nb.get("port") or 1883)
                                nb["host"] = str(nb.get("host") or "").strip()
                                if not b.get("password"):
                                    nb["password"] = old.get(b.get("id"), {}).get("password", "")
                                brokers.append(nb)
                            new["mqtt"] = {"brokers": brokers}
                        app.cfg = new
                        app.save_config()
                    app.mqtt.start(app.cfg["mqtt"], app.interval_s)
                    return self._json({"ok": True})
                self._err("Nicht gefunden", 404)
            except CalibrationError as e:
                self._err(str(e))
            except (OSError, RuntimeError) as e:
                prefix = "Kamera nicht erreichbar: " if path == "/api/snapshot" and not isinstance(e, RuntimeError) else ""
                self._err(f"{prefix}{e}", 502)
            except Exception as e:
                log.exception("Fehler bei %s", path)
                self._err(f"Interner Fehler: {e}", 500)

    return H


def main() -> None:
    ap = argparse.ArgumentParser(description="Heizungsmanometer per Kamera auslesen")
    ap.add_argument("--data", default=os.environ.get("MANOMETER_DATA", "./data"),
                    help="Ordner für Einstellungen, Referenzbild und Verlauf")
    ap.add_argument("--port", type=int, help="Port der Weboberfläche (Standard 8080)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    app = App(Path(a.data))
    port = a.port or int(app.cfg["web"].get("port", 8080))
    threading.Thread(target=app.loop, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", port), make_handler(app))
    log.info("Weboberfläche: http://<dieser-rechner>:%d", port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.mqtt.stop()


if __name__ == "__main__":
    main()
