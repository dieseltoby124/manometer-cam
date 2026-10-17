"""Zeigererkennung und automatisches Nachführen für analoge Manometer.

Nur numpy + Pillow (beides bringt Home Assistant mit), kein OpenCV.
Bilder sind numpy-Arrays (Höhe, Breite, 3) im RGB-Format, uint8.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

ANGLE_STEP = 0.5          # Grad
N_RADII = 48              # Abtastpunkte pro Radiallinie


class CalibrationError(ValueError):
    pass


# --------------------------------------------------------------------------- Bild-Hilfsfunktionen

def decode_jpeg(data: bytes) -> np.ndarray:
    try:
        with Image.open(io.BytesIO(data)) as im:
            return np.asarray(im.convert("RGB")).copy()
    except Exception as err:  # noqa: BLE001
        raise ValueError("Die Kamera hat kein gültiges Bild geliefert") from err


def encode_jpeg(img: np.ndarray, quality: int = 85) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def rotate_img(img: np.ndarray, deg: int) -> np.ndarray:
    """Im Uhrzeigersinn drehen (0/90/180/270)."""
    k = {90: -1, 180: 2, 270: 1}.get(deg % 360)
    return img if k is None else np.ascontiguousarray(np.rot90(img, k))


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


def to_gray(img: np.ndarray) -> np.ndarray:
    f = img.astype(np.float32)
    return 0.299 * f[..., 0] + 0.587 * f[..., 1] + 0.114 * f[..., 2]


def _box_1d(a: np.ndarray, r: int, axis: int) -> np.ndarray:
    """Gleitender Mittelwert (Fensterbreite 2r+1) entlang einer Achse, Ränder gespiegelt."""
    if r <= 0:
        return a
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r + 1, r)
    p = np.pad(a, pad, mode="edge")
    c = np.cumsum(p, axis=axis, dtype=np.float64)
    n = a.shape[axis]
    hi = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
    lo = np.take(c, np.arange(0, n), axis=axis)
    return ((hi - lo) / (2 * r + 1)).astype(np.float32)


def blur(a: np.ndarray, sigma: float) -> np.ndarray:
    """Gauß-Weichzeichner, angenähert durch drei Box-Filter (schnell, nur numpy)."""
    if sigma <= 0:
        return a.astype(np.float32)
    w = math.sqrt(12 * sigma * sigma / 3 + 1)
    r = max(1, int(round((w - 1) / 2)))
    out = a.astype(np.float32)
    for _ in range(3):
        for axis in (0, 1):
            out = _box_1d(out, r, axis)
    return out


def remap_bilinear(src: np.ndarray, mx: np.ndarray, my: np.ndarray) -> np.ndarray:
    """Bild an (mx, my) bilinear abtasten; außerhalb = 0."""
    h, w = src.shape
    x0 = np.floor(mx).astype(np.int64)
    y0 = np.floor(my).astype(np.int64)
    fx, fy = (mx - x0).astype(np.float32), (my - y0).astype(np.float32)
    out = np.zeros(mx.shape, dtype=np.float32)

    def tap(yy, xx):
        ok = (xx >= 0) & (xx < w) & (yy >= 0) & (yy < h)
        v = np.zeros(mx.shape, dtype=np.float32)
        v[ok] = src[yy[ok], xx[ok]]
        return v

    out += tap(y0, x0) * (1 - fx) * (1 - fy)
    out += tap(y0, x0 + 1) * fx * (1 - fy)
    out += tap(y0 + 1, x0) * (1 - fx) * fy
    out += tap(y0 + 1, x0 + 1) * fx * fy
    return out


def match_template(region: np.ndarray, tpl: np.ndarray) -> np.ndarray:
    """Normierte Kreuzkorrelation (wie OpenCV TM_CCOEFF_NORMED), per FFT."""
    R = region.astype(np.float64)
    T = tpl.astype(np.float64)
    H, W = R.shape
    h, w = T.shape
    T0 = T - T.mean()
    num = np.fft.irfft2(np.fft.rfft2(R) * np.fft.rfft2(T0[::-1, ::-1], s=(H, W)), s=(H, W))
    num = num[h - 1:H, w - 1:W]

    def winsum(a):
        c = np.pad(a.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
        return c[h:, w:] - c[:-h, w:] - c[h:, :-w] + c[:-h, :-w]

    n = h * w
    s1, s2 = winsum(R), winsum(R * R)
    var = np.clip(s2 - s1 * s1 / n, 0, None)
    den = np.sqrt(var * float((T0 * T0).sum()))
    out = np.zeros_like(num)
    ok = den > 1e-6 * max(1.0, float(den.max()))
    out[ok] = num[ok] / den[ok]
    return out


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
            img_angles.append(math.degrees(math.atan2(y - cy, x - cx)) % 360)
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
        if rel_angle > 180 + a[-1] / 2:
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
    angle: float
    confidence: float
    offset: tuple[float, float] = (0.0, 0.0)
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
        if self.g.needle == "red":
            f = blur(img.astype(np.float32)[..., 0], 0.8), blur(img.astype(np.float32)[..., 1], 0.8), \
                blur(img.astype(np.float32)[..., 2], 0.8)
            r, gch, b = f
            return np.clip(r - np.maximum(gch, b), 0, 255).astype(np.float32)
        gray = blur(to_gray(img), 0.8)
        bg = blur(gray, max(2.0, self.g.radius * 0.15))      # Lichtverlauf herausrechnen
        if self.g.needle == "light":
            return np.clip(gray - bg, 0, None)
        return np.clip(bg - gray, 0, None)

    def detect(self, img: np.ndarray, offset: tuple[float, float] = (0.0, 0.0)) -> Reading:
        cx, cy = self.g.center[0] + offset[0], self.g.center[1] + offset[1]
        score = self._score_image(img)
        polar = remap_bilinear(score, self.dx + np.float32(cx), self.dy + np.float32(cy))
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

        thr = 0.5 * np.percentile(polar[self.allowed], 99.5)
        near = np.arange(i - 2, i + 3) % len(profile)
        coverage = float(np.max(np.mean(polar[near] > thr, axis=1))) if thr > 0 else 0.0
        d = np.abs(((np.arange(len(profile)) - i) * ANGLE_STEP + 180) % 360 - 180)
        second = np.max(np.where(d > 10, masked, -np.inf))
        uniq = float((profile[i] - base) / max(second - base, 1e-6)) if np.isfinite(second) else 99.0
        confidence = 100.0 * min(1.0, coverage / 0.9) * min(1.0, max(0.0, uniq - 1.0))

        rel = (angle - self.g.zero_angle) % 360
        return Reading(value=self.g.angle_to_value(rel), angle=float(angle),
                       confidence=float(confidence), offset=offset)


# --------------------------------------------------------------------------- Nachführen

class Aligner:
    """Findet das Manometer im neuen Bild wieder (Verschiebung gegenüber dem Kalibrierbild)."""

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
        return blur(to_gray(img) if img.ndim == 3 else img.astype(np.float32), 1.1)

    def locate(self, img: np.ndarray) -> tuple[float, float, float]:
        gray = self._prep(img)
        dx, dy, score = self._search(gray, self.margin)
        if score < 0.6:
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
        res = match_template(region, self.tpl)
        my, mx = np.unravel_index(int(np.argmax(res)), res.shape)
        score = float(res[my, mx])

        def sub(v_m, v_0, v_p):
            den = v_m - 2 * v_0 + v_p
            return 0.5 * (v_m - v_p) / den if abs(den) > 1e-9 else 0.0
        fx = sub(res[my, mx - 1], res[my, mx], res[my, mx + 1]) if 0 < mx < res.shape[1] - 1 else 0.0
        fy = sub(res[my - 1, mx], res[my, mx], res[my + 1, mx]) if 0 < my < res.shape[0] - 1 else 0.0
        return float(sx0 + mx + fx - self.x0), float(sy0 + my + fy - self.y0), score


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
                return Reading(value=float("nan"), angle=0.0, confidence=0.0, align_score=score)
            offset = (dx, dy)
        rd = self.detector.detect(img, offset)
        rd.align_score = score
        return rd


def draw_overlay(img: np.ndarray, gauge: Gauge, rd: Reading | None) -> np.ndarray:
    im = Image.fromarray(img)
    dr = ImageDraw.Draw(im)
    off = rd.offset if rd else (0.0, 0.0)
    cx, cy = gauge.center[0] + off[0], gauge.center[1] + off[1]
    for f in gauge.ring:
        r = gauge.radius * f
        dr.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(0, 200, 255), width=1)
    for a in gauge.scale_angles:
        t = math.radians(gauge.zero_angle + a)
        px, py = cx + gauge.radius * math.cos(t), cy + gauge.radius * math.sin(t)
        dr.ellipse([px - 4, py - 4, px + 4, py + 4], fill=(255, 200, 0))
    if rd and not math.isnan(rd.value):
        t = math.radians(rd.angle)
        dr.line([cx, cy, cx + gauge.radius * math.cos(t), cy + gauge.radius * math.sin(t)], fill=(0, 255, 0), width=2)
    return np.asarray(im)
