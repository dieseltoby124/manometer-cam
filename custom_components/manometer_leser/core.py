"""Messlogik des Manometer-Lesers innerhalb von Home Assistant.

Holt Bilder von der Kamera-Entität (z. B. ESPHome), schaltet die LED über eine Licht-Entität,
erkennt den Zeiger und speichert Einstellungen, Kalibrierung und Verlauf unter
/config/manometer_leser/<entry_id>/.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import os
import time
from pathlib import Path
from typing import Any

import numpy as np

from homeassistant.core import HomeAssistant

from . import gauge as G
from .const import (
    DEFAULT_SETTINGS, DOMAIN, HISTORY_DAYS, HOMEKIT_ALARM_DELAY_S, HOMEKIT_OUTAGE_S,
    LED_WARMUP_S, PREVIEW_HOLD_S, VERSION,
)

_LOGGER = logging.getLogger(__name__)


def deep_merge(base: dict, upd: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (upd or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def homekit_eval(status: dict, now: float | None = None) -> dict:
    """Zustände der HomeKit-Wassermelder aus dem Status berechnen (ohne Home-Assistant-Abhängigkeit)."""
    now = now or time.time()
    hk = status.get("homekit") or {}
    last = status.get("last") or {}
    out = {}
    for kind in ("low", "high"):
        since = hk.get(f"{kind}_since")
        out[kind] = bool(since is not None and now - since >= HOMEKIT_ALARM_DELAY_S)
    t = last.get("value_time")
    max_age = max(HOMEKIT_OUTAGE_S, 3 * float(status.get("interval_min") or 5) * 60)
    out["outage"] = bool(status.get("calibrated")) and (t is None or now - float(t) > max_age)
    return out


class ManometerCore:
    def __init__(self, hass: HomeAssistant, entry_id: str, camera_entity: str, light_entity: str | None) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.camera_entity = camera_entity
        self.light_entity = light_entity or None
        self.dir = Path(hass.config.path(DOMAIN, entry_id))
        self.p_settings = self.dir / "settings.json"
        self.p_calib, self.p_ref = self.dir / "kalibrierbild.jpg", self.dir / "referenz.jpg"
        self.p_last, self.p_hist = self.dir / "letzte_messung.jpg", self.dir / "verlauf.jsonl"

        self.settings: dict = copy.deepcopy(DEFAULT_SETTINGS)
        self.history: list[tuple[float, float, float]] = []
        self.calib_img: np.ndarray | None = None
        self.last_jpg: bytes | None = None
        self.last: dict = {}
        self.evaluator: G.Evaluator | None = None
        self.measuring = False
        self.cam_lock = asyncio.Lock()
        self.measure_lock = asyncio.Lock()
        self.led_on: bool | None = None
        self.led_error = ""
        self.preview_until = 0.0
        self.listeners: list = []          # werden nach Kalibrierung/Einstellungen aufgerufen
        self.homekit_entities: dict[str, str] = {}   # unique_id-Schlüssel → entity_id (fürs Panel)
        self.reload_cb = None              # Integration neu laden (z. B. wenn HomeKit-Sensoren an/aus)

    # ------------------------------------------------------------------ Laden / Speichern
    async def async_load(self) -> None:
        await self.hass.async_add_executor_job(self._load)

    def _load(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        if self.p_settings.exists():
            try:
                self.settings = deep_merge(DEFAULT_SETTINGS, json.loads(self.p_settings.read_text("utf-8")))
            except (OSError, ValueError):
                _LOGGER.warning("settings.json unlesbar – Standardwerte werden verwendet")
        if self.p_calib.exists():
            try:
                self.calib_img = G.decode_jpeg(self.p_calib.read_bytes())
            except ValueError:
                self.calib_img = None
        if self.p_last.exists():
            self.last_jpg = self.p_last.read_bytes()
        cutoff = time.time() - HISTORY_DAYS * 86400
        if self.p_hist.exists():
            for line in self.p_hist.read_text("utf-8").splitlines():
                try:
                    t, v, c = json.loads(line)
                    if t >= cutoff:
                        self.history.append((t, v, c))
                except (ValueError, TypeError):
                    continue
            self.p_hist.write_text("".join(json.dumps(list(r)) + "\n" for r in self.history), "utf-8")
        if self.history:
            t, v, c = self.history[-1]
            self.last = {"time": t, "value_time": t, "value": v, "confidence": c, "error": ""}
        self._build_evaluator()

    def _save_settings(self) -> None:
        tmp = self.p_settings.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), "utf-8")
        os.replace(tmp, self.p_settings)

    def _build_evaluator(self) -> None:
        try:
            ref = G.decode_jpeg(self.p_ref.read_bytes()) if self.p_ref.exists() else None
            self.evaluator = G.Evaluator(self.settings["gauge"], ref)
        except (G.CalibrationError, ValueError):
            self.evaluator = None

    def _notify(self) -> None:
        for cb in list(self.listeners):
            cb()

    # ------------------------------------------------------------------ Status
    @property
    def interval_s(self) -> float:
        return max(1.0, float(self.settings.get("interval_min", 5))) * 60

    def status(self) -> dict[str, Any]:
        d = {
            "calibrated": self.evaluator is not None,
            "tracking": bool(self.evaluator and self.evaluator.aligner),
            "last": dict(self.last), "measuring": self.measuring,
            "interval_min": self.settings["interval_min"], "range": self.settings["range"],
            "version": VERSION, "id": self.entry_id,
            "has_last_image": self.last_jpg is not None,
            "camera_entity": self.camera_entity, "light_entity": self.light_entity,
            "led": {"enabled": self.led_enabled, "on": self.led_on, "error": self.led_error},
            "mqtt": {"available": "mqtt" in self.hass.config.components,
                     "enabled": bool(self.settings["mqtt"].get("enabled"))},
            "homekit": {"enabled": self.homekit_enabled,
                        "scale10": bool(self.settings.get("homekit", {}).get("scale10", True)),
                        "low_since": self._streak_since("low"), "high_since": self._streak_since("high"),
                        "delay_min": HOMEKIT_ALARM_DELAY_S // 60},
        }
        d["homekit"]["state"] = homekit_eval(d)      # für die Anzeige im Panel
        return d

    @property
    def homekit_enabled(self) -> bool:
        return bool(self.settings.get("homekit", {}).get("enabled"))

    def _streak_since(self, kind: str) -> float | None:
        """Seit wann liegen die Messwerte ohne Unterbrechung unter (low) bzw. über (high) dem Normalbereich?"""
        rng = self.settings["range"]
        bad = (lambda v: v < rng["low"]) if kind == "low" else (lambda v: v > rng["high"])
        since = None
        for t, v, _ in reversed(self.history):
            if not bad(v):
                break
            since = t
        return since

    def history_since(self, hours: float, max_points: int = 600) -> list:
        cutoff = time.time() - hours * 3600
        pts = [(t, v) for t, v, _ in self.history if t >= cutoff]
        if len(pts) > max_points:
            step = hours * 3600 / max_points
            buckets: dict[int, list] = {}
            for t, v in pts:
                buckets.setdefault(int((t - cutoff) // step), []).append((t, v))
            pts = [(float(np.mean([p[0] for p in b])), float(np.mean([p[1] for p in b]))) for b in buckets.values()]
        return [[round(t), round(v, 3)] for t, v in pts]

    # ------------------------------------------------------------------ Kamera & LED
    @property
    def led_enabled(self) -> bool:
        return bool(self.light_entity and self.settings["camera"].get("led_control", True))

    async def _led(self, on: bool) -> bool:
        """LED schalten. True, wenn sie dadurch gerade eingeschaltet wurde."""
        if not self.led_enabled or self.led_on == on:
            return False
        pct = min(100, max(1, int(self.settings["camera"].get("led_brightness", 15))))
        try:
            if on:
                await self.hass.services.async_call("light", "turn_on",
                                                    {"entity_id": self.light_entity, "brightness_pct": pct}, blocking=True)
            else:
                await self.hass.services.async_call("light", "turn_off", {"entity_id": self.light_entity}, blocking=True)
            self.led_on, self.led_error = on, ""
            return on
        except Exception as err:  # noqa: BLE001
            self.led_error = f"LED nicht schaltbar: {err}"
            _LOGGER.warning(self.led_error)
            return False

    async def async_led_tick(self, _now=None) -> None:
        """Regelmäßig aufgerufen: LED nach Ende der Vorschau wieder aus."""
        if not self.measuring and time.time() >= self.preview_until and self.led_on:
            await self._led(False)

    async def _fetch(self) -> np.ndarray:
        from homeassistant.components.camera import async_get_image  # noqa: PLC0415
        try:
            image = await async_get_image(self.hass, self.camera_entity, timeout=15)
        except Exception as err:  # noqa: BLE001
            raise RuntimeError(f"Kamera nicht erreichbar ({err})") from err
        rot = int(self.settings["camera"].get("rotate", 0))
        return await self.hass.async_add_executor_job(lambda: G.rotate_img(G.decode_jpeg(image.content), rot))

    # ------------------------------------------------------------------ Messung
    async def async_measure(self) -> dict:
        async with self.measure_lock:
            await self._measure()
        return self.status()

    async def _measure(self) -> None:
        ev = self.evaluator
        if ev is None:
            self.last = {**{k: v for k, v in self.last.items() if k in ("value", "confidence", "value_time")},
                         "time": time.time(), "error": "Noch nicht kalibriert"}
            return
        self.measuring = True
        readings, img_last, rd_last = [], None, None
        try:
            async with self.cam_lock:
                if await self._led(True):
                    await asyncio.sleep(LED_WARMUP_S)
                    await self._fetch()                       # erstes Bild nach dem Einschalten verwerfen
                n = max(1, int(self.settings.get("samples", 3)))
                for k in range(n):
                    img = await self._fetch()
                    rd = await self.hass.async_add_executor_job(ev.evaluate, img)
                    img_last, rd_last = img, rd
                    if not math.isnan(rd.value) and rd.confidence >= ev.gauge.min_confidence:
                        readings.append(rd)
                    if k < n - 1:
                        await asyncio.sleep(1.5)
            jpg = await self.hass.async_add_executor_job(
                lambda: G.encode_jpeg(G.draw_overlay(img_last, ev.gauge, rd_last)))
            self.last_jpg = jpg
            await self.hass.async_add_executor_job(self.p_last.write_bytes, jpg)
            if not readings:
                if rd_last.align_score is not None and rd_last.align_score < G.Aligner.MIN_SCORE:
                    raise RuntimeError("Manometer im Bild nicht wiedergefunden – Kamera verdreht oder verdeckt? "
                                       "Bitte neu kalibrieren.")
                raise RuntimeError(f"Zeiger nicht sicher erkannt (Güte {rd_last.confidence:.0f} %). "
                                   "Spiegelung oder Kamera verschoben?")
            value = float(np.median([r.value for r in readings]))
            conf = float(np.median([r.confidence for r in readings]))
            off = readings[-1].offset
            now = time.time()
            self.last = {"time": now, "value_time": now, "value": value, "confidence": conf,
                         "offset": [round(off[0], 1), round(off[1], 1)], "error": ""}
            await self._add_history(value, conf)
            await self._mqtt_state(value, conf)
            _LOGGER.debug("Messung: %.2f bar (Güte %.0f %%)", value, conf)
        except Exception as err:  # noqa: BLE001
            msg = str(err)
            _LOGGER.warning("Messung fehlgeschlagen: %s", msg)
            self.last = {**{k: v for k, v in self.last.items() if k in ("value", "confidence", "value_time")},
                         "time": time.time(), "error": msg, "stale": True}
            await self._mqtt_error(msg)
        finally:
            self.measuring = False
            await self.async_led_tick()

    async def _add_history(self, v: float, c: float) -> None:
        rec = (round(time.time(), 1), round(v, 3), round(c))
        self.history.append(rec)
        cutoff = time.time() - HISTORY_DAYS * 86400
        while self.history and self.history[0][0] < cutoff:
            self.history.pop(0)

        def write():
            with self.p_hist.open("a", encoding="utf-8") as f:
                f.write(json.dumps(list(rec)) + "\n")
        await self.hass.async_add_executor_job(write)

    # ------------------------------------------------------------------ MQTT (über die MQTT-Integration von HA)
    async def _publish(self, items: list[tuple[str, str]]) -> None:
        if not self.settings["mqtt"].get("enabled") or "mqtt" not in self.hass.config.components:
            return
        from homeassistant.components import mqtt  # noqa: PLC0415
        p = (self.settings["mqtt"].get("topic_prefix") or "heizung/manometer").strip("/")
        for sub, payload in items:
            try:
                await mqtt.async_publish(self.hass, f"{p}/{sub}", payload, 0, True)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("MQTT-Senden fehlgeschlagen: %s", err)
                return

    async def _mqtt_state(self, value: float, conf: float) -> None:
        await self._publish([
            ("druck", f"{value:.2f}"), ("guete", str(round(conf))),
            ("zeitstempel", time.strftime("%Y-%m-%dT%H:%M:%S%z")),
            ("state", json.dumps({"pressure": round(value, 3), "confidence": round(conf)})),
            ("error", "ok"), ("status", "online"),
        ])

    async def _mqtt_error(self, msg: str) -> None:
        await self._publish([("error", msg[:250]), ("status", "online")])

    # ------------------------------------------------------------------ Kalibrierung
    def calibration(self) -> dict:
        size = list(self.calib_img.shape[1::-1]) if self.calib_img is not None else None
        return {"gauge": self.settings["gauge"], "image_size": size,
                "rotate": int(self.settings["camera"].get("rotate", 0))}

    async def async_snapshot(self) -> list:
        self.preview_until = max(self.preview_until, time.time() + PREVIEW_HOLD_S)
        async with self.cam_lock:
            if await self._led(True):
                await asyncio.sleep(LED_WARMUP_S)
                await self._fetch()
            img = await self._fetch()
        self.calib_img = img
        await self.hass.async_add_executor_job(lambda: self.p_calib.write_bytes(G.encode_jpeg(img, 92)))
        return list(img.shape[1::-1])

    def calib_test(self, gauge_cfg: dict) -> dict:
        """Blockierend (im Executor aufrufen)."""
        if self.calib_img is None:
            raise G.CalibrationError("Erst ein Foto aufnehmen")
        import base64  # noqa: PLC0415
        cfg = {**self.settings["gauge"], **gauge_cfg}
        g = G.Gauge.from_config(cfg)
        rd = G.NeedleDetector(g).detect(self.calib_img)
        img = G.draw_overlay(self.calib_img, g, rd)
        suggest = None
        if rd.confidence < 80:
            others = {m: G.NeedleDetector(G.Gauge.from_config({**cfg, "needle": m})).detect(self.calib_img).confidence
                      for m in ("dark", "light", "red") if m != g.needle}
            best = max(others, key=others.get)
            if others[best] >= 50 and others[best] > rd.confidence + 25:
                suggest = {"needle": best, "confidence": others[best]}
        return {"value": rd.value, "confidence": rd.confidence, "suggest": suggest,
                "image": "data:image/jpeg;base64," + base64.b64encode(G.encode_jpeg(img)).decode()}

    async def async_calib_save(self, gauge_cfg: dict) -> None:
        if self.calib_img is None:
            raise G.CalibrationError("Erst ein Foto aufnehmen")
        new = {**self.settings["gauge"], **gauge_cfg}
        G.Gauge.from_config(new)
        img = self.calib_img

        def work():
            self.p_ref.write_bytes(G.encode_jpeg(img, 92))
            self.settings["gauge"] = new
            self._save_settings()
            self._build_evaluator()
        await self.hass.async_add_executor_job(work)
        self._notify()

    async def async_set_rotation(self, new_rot: int, points: dict | None) -> dict:
        new_rot = int(new_rot) % 360
        if new_rot not in (0, 90, 180, 270):
            raise G.CalibrationError("Drehung nur in 90°-Schritten möglich")

        def work():
            delta = (new_rot - int(self.settings["camera"].get("rotate", 0))) % 360
            size = None
            if self.calib_img is not None:
                size = self.calib_img.shape[1::-1]
            elif self.p_ref.exists():
                size = G.decode_jpeg(self.p_ref.read_bytes()).shape[1::-1]
            out_points = points
            if delta and size:
                W, H = size
                tp = lambda p: G.rotate_point(p, delta, W, H)  # noqa: E731
                g = self.settings["gauge"]
                if g.get("center"):
                    g["center"] = tp(g["center"])
                    g["scale"] = [{**sp, "point": tp(sp["point"])} for sp in g.get("scale", [])]
                if points and points.get("center"):
                    out_points = {**points, "center": tp(points["center"]),
                                  "scale": [{**sp, "point": tp(sp["point"])} for sp in points.get("scale", [])]}
                for path in (self.p_calib, self.p_ref, self.p_last):
                    if path.exists():
                        path.write_bytes(G.encode_jpeg(G.rotate_img(G.decode_jpeg(path.read_bytes()), delta), 92))
                if self.calib_img is not None:
                    self.calib_img = G.rotate_img(self.calib_img, delta)
                if self.p_last.exists():
                    self.last_jpg = self.p_last.read_bytes()
            self.settings["camera"]["rotate"] = new_rot
            self._save_settings()
            self._build_evaluator()
            return {"rotate": new_rot, "points": out_points,
                    "image_size": list(self.calib_img.shape[1::-1]) if self.calib_img is not None else None}
        result = await self.hass.async_add_executor_job(work)
        self._notify()
        return result

    # ------------------------------------------------------------------ Einstellungen
    def public_settings(self) -> dict:
        s = copy.deepcopy(self.settings)
        s.pop("gauge", None)
        s["camera_entity"], s["light_entity"] = self.camera_entity, self.light_entity
        s["mqtt"]["available"] = "mqtt" in self.hass.config.components
        s.setdefault("homekit", {})["entities"] = dict(self.homekit_entities)
        return s

    async def async_update_settings(self, body: dict) -> None:
        new = copy.deepcopy(self.settings)
        cam = body.get("camera") or {}
        if "led_control" in cam:
            new["camera"]["led_control"] = bool(cam["led_control"])
        if "led_brightness" in cam:
            new["camera"]["led_brightness"] = min(100, max(1, int(cam["led_brightness"] or 15)))
        if "interval_min" in body:
            new["interval_min"] = max(1, int(body["interval_min"] or 5))
        if isinstance(body.get("range"), dict):
            try:
                lo, hi = float(body["range"]["low"]), float(body["range"]["high"])
            except (KeyError, TypeError, ValueError) as err:
                raise G.CalibrationError("Normalbereich: bitte zwei Zahlen eintragen") from err
            if not 0 <= lo < hi <= 10:
                raise G.CalibrationError("Normalbereich: Untergrenze muss kleiner als Obergrenze sein (0–10 bar)")
            new["range"] = {"low": round(lo, 2), "high": round(hi, 2)}
        if isinstance(body.get("mqtt"), dict):
            new["mqtt"]["enabled"] = bool(body["mqtt"].get("enabled"))
            new["mqtt"]["topic_prefix"] = (str(body["mqtt"].get("topic_prefix") or "heizung/manometer")).strip().strip("/")
        if isinstance(body.get("homekit"), dict):
            new.setdefault("homekit", {})["enabled"] = bool(body["homekit"].get("enabled"))
            if "scale10" in body["homekit"]:
                new["homekit"]["scale10"] = bool(body["homekit"]["scale10"])
        homekit_changed = bool(new.get("homekit", {}).get("enabled")) != self.homekit_enabled
        self.settings = new
        await self.hass.async_add_executor_job(self._save_settings)
        if not self.led_enabled and self.led_on:
            self.led_on = None
        self._notify()
        if homekit_changed and self.reload_cb:
            self.reload_cb()                # Entitäten anlegen bzw. entfernen
