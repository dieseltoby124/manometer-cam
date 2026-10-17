"""Update des Manometer-Lesers über die Weboberfläche.

Ablauf: Zip prüfen -> Programmdateien in app.new entpacken -> Probe-Import -> ggf. neue
Python-Pakete installieren -> app nach app.bak verschieben, app.new nach app -> Neustart.
Der Datenordner (Einstellungen, Kalibrierung, Verlauf) wird nicht angefasst.
"""
from __future__ import annotations

import io
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath

log = logging.getLogger("manometer")

APP_DIR = Path(__file__).resolve().parent          # .../app
BASE_DIR = APP_DIR.parent                           # .../  (requirements.txt, venv, data)
MAX_SIZE = 30 * 1024 * 1024
SCRIPT = os.path.abspath(sys.argv[0])               # beim Start festhalten (Neustart nach dem Tausch)


class UpdateError(Exception):
    pass


def current_version() -> str:
    try:
        return (APP_DIR / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "unbekannt"


def _find_root(names: list[str]) -> str:
    """Pfad-Präfix im Zip, unter dem app/server.py liegt (mit oder ohne Oberordner)."""
    for n in names:
        p = PurePosixPath(n)
        if p.name == "server.py" and p.parent.name == "app":
            root = str(p.parent.parent)
            return "" if root == "." else root + "/"
    raise UpdateError("Das ist keine Manometer-Leser-Zip (app/server.py fehlt).")


def install(data: bytes) -> tuple[str, str]:
    """Update installieren. Gibt (alte Version, neue Version) zurück; Neustart danach separat."""
    if len(data) > MAX_SIZE:
        raise UpdateError("Datei ist zu groß.")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise UpdateError("Die Datei ist keine gültige Zip-Datei.")
    names = zf.namelist()
    root = _find_root(names)
    for need in ("app/gauge.py", "app/static/index.html"):
        if root + need not in names:
            raise UpdateError(f"In der Zip fehlt {need}.")

    staging = BASE_DIR / "app.new"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    new_req = None
    try:
        for info in zf.infolist():
            if info.is_dir() or not info.filename.startswith(root):
                continue
            rel = PurePosixPath(info.filename[len(root):])
            if rel.is_absolute() or ".." in rel.parts:
                raise UpdateError("Ungültiger Pfad in der Zip.")
            if rel.parts[0] == "app" and len(rel.parts) > 1:
                if "__pycache__" in rel.parts:
                    continue
                target = staging.joinpath(*rel.parts[1:])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(info))
            elif str(rel) == "requirements.txt":
                new_req = zf.read(info)

        new_version = (staging / "VERSION").read_text(encoding="utf-8").strip() if (staging / "VERSION").exists() else "unbekannt"

        # Probe-Import: fängt Tipp- und Importfehler ab, bevor etwas ausgetauscht wird
        probe = subprocess.run(
            [sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import gauge, server", str(staging)],
            capture_output=True, text=True, timeout=120, cwd=str(staging))
        if probe.returncode != 0:
            last = (probe.stderr.strip().splitlines() or ["?"])[-1]
            raise UpdateError(f"Die neue Version lässt sich nicht starten: {last}")

        # Neue Python-Pakete nur installieren, wenn sich requirements.txt geändert hat
        req_path = BASE_DIR / "requirements.txt"
        if new_req is not None and (not req_path.exists() or req_path.read_bytes() != new_req):
            tmp_req = staging / "_requirements.txt"
            tmp_req.write_bytes(new_req)
            log.info("Update: installiere Python-Pakete …")
            pip = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(tmp_req)],
                                 capture_output=True, text=True, timeout=900)
            if pip.returncode != 0:
                last = (pip.stderr.strip().splitlines() or ["?"])[-1]
                raise UpdateError(f"Python-Pakete konnten nicht installiert werden: {last}")
            tmp_req.unlink()
            req_path.write_bytes(new_req)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    # Austausch: alte Version als app.bak aufheben
    old_version = current_version()
    backup = BASE_DIR / "app.bak"
    shutil.rmtree(backup, ignore_errors=True)
    os.replace(APP_DIR, backup)
    try:
        os.replace(staging, APP_DIR)
    except Exception:
        os.replace(backup, APP_DIR)
        raise
    log.info("Update installiert: %s -> %s", old_version, new_version)
    return old_version, new_version


def restart_later(before_exit=None, delay: float = 1.0) -> None:
    """Prozess durch die neue Version ersetzen (gleiche Aufrufparameter, gleiche Prozess-ID)."""
    def run():
        time.sleep(delay)
        if before_exit:
            try:
                before_exit()
            except Exception:
                pass
        logging.shutdown()
        os.chdir(os.path.dirname(SCRIPT))
        os.execv(sys.executable, [sys.executable, SCRIPT] + sys.argv[1:])
    threading.Thread(target=run, daemon=True).start()
