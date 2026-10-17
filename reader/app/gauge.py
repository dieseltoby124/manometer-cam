"""Zeigererkennung und automatisches Nachführen für analoge Manometer."""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

ANGLE_STEP = 0.5          # Grad
N_RADII = 48              # Abtastpunkte pro Radiallinie


class CalibrationError(ValueError):
    pass


# --------------------------------------------------------------------------- Kalibrierung

@dataclass
class Gauge:
    center: tuple[float, float]
    scale_angles: np.ndarray      # Winkel der Skalenpunkte im Uhrzeigersinn relativ zum ersten
    scale_values: np.ndarray
    zero_angle: float             # Bildwinkel des ersten Skalenpunkts
    radius: float                 # Abstand Mitte -> Skalenstriche
    ring: tuple[float, float]
    needle: str                   # "dark", "light" oder "red"
    margin: float
    min_confidence: float

    @classmethod
    def from_config(cls, g: dict) -> "Gauge":
        if not g or not g.get("center") or len(g.get("scale") or []) < 2:
            raise CalibrationError("Noch nicht kalibriert: Achsmitte und mindestens zwei Skalenpunkte nötig")
        cx, cy = map(float, g["center"])
        pts = sorted(g["scale"], key=lambda p: float(p["value"]))
        img_angles, values, radii = [], [], []
        for p in pts:
            x, y = map(float, p["point"])
            r = math.hypot(x - cx, y - cy)
            if r < 10:
                raise CalibrationError("Ein Skalenpunkt liegt zu nah an der Achsmitte")
            img_angles.append(math.degrees(math.atan2(y - cy, x - cx)) % 360)   # Bild-y nach unten -> Uhrzeigersinn
            values.append(float(p["value"]))
            radii.append(r)
        if len(set(values)) != len(values):
            raise CalibrationError("Zwei Skalenpunkte haben denselben Wert")
        zero = img_angles[0]
        rel = np.array([(a - zero) % 360 for a in img_angles])
        rel[0] = 0.0
        if np.any(np.diff(rel) <= 0.5):
            raise CalibrationError("Die Skalenpunkte müssen mit steigendem Wert im Uhrzeigersinn liegen. "
                                   "Stimmen die eingegebenen Werte?")
        return cls(
            center=(cx, cy), scale_angles=rel, scale_values=np.array(values), zero_angle=zero,
            radius=float(np.mean(radii)),
            ring=tuple(g.get("ring", [0.25, 0.85])),
            needle=g.get("needle", "dark"),
            margin=float(g.get("margin_deg", 15)),
            min_confidence=float(g.get("min_confidence", 50.0)),
        )

    def angle_to_value(self, rel_angle: float) -> float:
        a, v = self.scale_angles, self.scale_values
        if rel_angle > 180 + a[-1] / 2:          # knapp vor dem Nullpunkt
            rel_angle -= 360
        if rel_angle <= a[0]:
            i = 0
        elif rel_angle >= a[-1]:
            i = len(a) - 2
        else:
            i = int(np.searchsorted(a, rel_angle)) - 1
        return float(v[i] + (rel_angle - a[i]) * (v[i + 1] - v[i]) / (a[i + 1] - a[i]))


# --------------------------------------------------------------------------- Zeigererkennung

@dataclass
class Reading:
    value: float
    angle: float            # Bildwinkel des Zeigers
    confidence: float       # 0–100 %
    offset: tuple[float, float] = (0.0, 0.0)   # vom Nachführen ermittelte Verschiebung
    align_score: float | None = None


class NeedleDetector:
    def __init__(self, gauge: Gauge):
        self.g = gauge
        self.angles = np.arange(0, 360, ANGLE_STEP)
        r0, r1 = (f * gauge.radius for f in gauge.ring)
        radii = np.linspace(r0, r1, N_RADII)
        th = np.radians(self.angles)[:, None]
        self.dx = (radii[None, :] * np.cos(th)).astype(np.float32)
        self.dy = (radii[None, :] * np.sin(th)).astype(np.float32)
        rel = (self.angles - gauge.zero_angle) % 360
        self.allowed = (rel <= gauge.scale_angles[-1] + gauge.margin) | (rel >= 360 - gauge.margin)

    def _score_image(self, img: np.ndarray) -> np.ndarray:
        img = cv2.GaussianBlur(img, (3, 3), 0)
        if self.g.needle == "red":
            b, gch, r = cv2.split(img.astype(np.int16))
            return np.clip(r - np.maximum(gch, b), 0, 255).astype(np.float32)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
        bg = cv2.GaussianBlur(gray, (0, 0), max(2.0, self.g.radius * 0.15))   # Lichtverlauf herausrechnen
        if self.g.needle == "light":                     # heller Zeiger auf dunklem Zifferblatt
            return np.clip(gray - bg, 0, None)
        return np.clip(bg - gray, 0, None)

    def detect(self, img: np.ndarray, offset: tuple[float, float] = (0.0, 0.0)) -> Reading:
        cx, cy = self.g.center[0] + offset[0], self.g.center[1] + offset[1]
        score = self._score_image(img)
        polar = cv2.remap(score, self.dx + np.float32(cx), self.dy + np.float32(cy), cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        # 25-%-Perzentil entlang des Radius: hoch nur, wenn fast die ganze Linie Zeiger ist
        profile = np.percentile(polar, 25, axis=1)
        k = np.array([1, 2, 3, 2, 1], dtype=np.float32)
        profile = np.convolve(np.r_[profile[-2:], profile, profile[:2]], k / k.sum(), mode="valid")

        masked = np.where(self.allowed, profile, -np.inf)
        i = int(np.argmax(masked))
        base = np.median(profile[self.allowed])

        win = np.arange(i - 4, i + 5) % len(profile)
        w = np.clip(profile[win] - base, 0, None)
        offs = np.arange(-4, 5) * ANGLE_STEP
        angle = (self.angles[i] + (np.sum(w * offs) / w.sum() if w.sum() > 0 else 0.0)) % 360

        # Güte 1: Abdeckung der Radiallinie am gefundenen Winkel
        thr = 0.5 * np.percentile(polar[self.allowed], 99.5)
        near = np.arange(i - 2, i + 3) % len(profile)
        coverage = float(np.max(np.mean(polar[near] > thr, axis=1))) if thr > 0 else 0.0
        # Güte 2: Abstand zum zweitbesten Kandidaten (>10° entfernt)
        d = np.abs(((np.arange(len(profile)) - i) * ANGLE_STEP + 180) % 360 - 180)
        second = np.max(np.where(d > 10, masked, -np.inf))
        uniq = float((profile[i] - base) / max(second - base, 1e-6)) if np.isfinite(second) else 99.0
        confidence = 100.0 * min(1.0, coverage / 0.9) * min(1.0, max(0.0, uniq - 1.0))

        rel = (angle - self.g.zero_angle) % 360
        return Reading(value=self.g.angle_to_value(rel), angle=float(angle),
                       confidence=float(confidence), offset=offset)


# --------------------------------------------------------------------------- Nachführen

class Aligner:
    """Findet das Manometer im neuen Bild wieder (Verschiebung gegenüber dem Kalibrierbild).

    Vorlage ist das Zifferblatt samt Rand aus dem Kalibrierbild. Der Zeiger ist darin zwar
    enthalten, macht aber nur einen kleinen Teil der Fläche aus.
    """

    MIN_SCORE = 0.5

    def __init__(self, ref_img: np.ndarray, gauge: Gauge, search_frac: float = 0.6):
        h, w = ref_img.shape[:2]
        cx, cy = gauge.center
        half = gauge.radius * 1.2
        self.x0, self.y0 = int(max(0, cx - half)), int(max(0, cy - half))
        x1, y1 = int(min(w, cx + half)), int(min(h, cy + half))
        self.tpl = self._prep(ref_img)[self.y0:y1, self.x0:x1]
        self.margin = int(max(30, gauge.radius * search_frac))

    @staticmethod
    def _prep(img: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        return cv2.GaussianBlur(gray, (5, 5), 0)

    def locate(self, img: np.ndarray) -> tuple[float, float, float]:
        gray = self._prep(img)
        dx, dy, score = self._search(gray, self.margin)
        if score < 0.6:                                  # weit verrutscht? -> ganzes Bild absuchen
            dx2, dy2, score2 = self._search(gray, max(gray.shape))
            if score2 >= 0.7 and score2 > score:
                return dx2, dy2, score2
        return dx, dy, score

    def _search(self, gray: np.ndarray, margin: int) -> tuple[float, float, float]:
        h, w = gray.shape
        th, tw = self.tpl.shape
        sx0, sy0 = max(0, self.x0 - margin), max(0, self.y0 - margin)
        sx1, sy1 = min(w, self.x0 + tw + margin), min(h, self.y0 + th + margin)
        region = gray[sy0:sy1, sx0:sx1]
        if region.shape[0] < th or region.shape[1] < tw:
            return 0.0, 0.0, 0.0
        res = cv2.matchTemplate(region, self.tpl, cv2.TM_CCOEFF_NORMED)
        _, score, _, (mx, my) = cv2.minMaxLoc(res)

        def sub(v_m, v_0, v_p):          # Parabel-Feinjustierung
            den = v_m - 2 * v_0 + v_p
            return 0.5 * (v_m - v_p) / den if abs(den) > 1e-9 else 0.0
        fx = sub(res[my, mx - 1], res[my, mx], res[my, mx + 1]) if 0 < mx < res.shape[1] - 1 else 0.0
        fy = sub(res[my - 1, mx], res[my, mx], res[my + 1, mx]) if 0 < my < res.shape[0] - 1 else 0.0
        dx = sx0 + mx + fx - self.x0
        dy = sy0 + my + fy - self.y0
        return float(dx), float(dy), float(score)


# --------------------------------------------------------------------------- Auswertung

class Evaluator:
    def __init__(self, calib: dict, ref_img: np.ndarray | None):
        self.gauge = Gauge.from_config(calib)
        self.detector = NeedleDetector(self.gauge)
        self.aligner = Aligner(ref_img, self.gauge) if ref_img is not None and calib.get("track", True) else None

    def evaluate(self, img: np.ndarray) -> Reading:
        offset, score = (0.0, 0.0), None
        if self.aligner:
            dx, dy, score = self.aligner.locate(img)
            if score < Aligner.MIN_SCORE:
                rd = Reading(value=float("nan"), angle=0.0, confidence=0.0, align_score=score)
                return rd
            offset = (dx, dy)
        rd = self.detector.detect(img, offset)
        rd.align_score = score
        return rd


def draw_overlay(img: np.ndarray, gauge: Gauge, rd: Reading | None) -> np.ndarray:
    out = img.copy()
    off = rd.offset if rd else (0.0, 0.0)
    cx, cy = gauge.center[0] + off[0], gauge.center[1] + off[1]
    c = (int(round(cx)), int(round(cy)))
    for f in gauge.ring:
        cv2.circle(out, c, int(gauge.radius * f), (255, 200, 0), 1, cv2.LINE_AA)
    for a, v in zip(gauge.scale_angles, gauge.scale_values):
        t = math.radians(gauge.zero_angle + a)
        p = (int(cx + gauge.radius * math.cos(t)), int(cy + gauge.radius * math.sin(t)))
        cv2.circle(out, p, 4, (0, 200, 255), -1, cv2.LINE_AA)
    if rd and not math.isnan(rd.value):
        t = math.radians(rd.angle)
        tip = (int(cx + gauge.radius * math.cos(t)), int(cy + gauge.radius * math.sin(t)))
        cv2.line(out, c, tip, (0, 255, 0), 2, cv2.LINE_AA)
    return out
