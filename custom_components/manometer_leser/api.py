"""Zugriff auf die Weboberfläche/API des Manometer-Lesers."""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from .const import DEFAULT_PORT


class ManometerError(Exception):
    """Manometer-Leser nicht erreichbar oder unerwartete Antwort."""


class ManometerAuthError(ManometerError):
    """Passwort falsch oder fehlt."""


def normalize_url(url: str) -> str:
    """'192.168.77.20' -> 'http://192.168.77.20:8080'."""
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    host_part = url.split("://", 1)[1]
    if ":" not in host_part.split("/", 1)[0]:
        scheme, rest = url.split("://", 1)
        host, _, path = rest.partition("/")
        url = f"{scheme}://{host}:{DEFAULT_PORT}" + (f"/{path}" if path else "")
    return url


class ManometerApi:
    def __init__(self, session: aiohttp.ClientSession, url: str, password: str | None = None) -> None:
        self._session = session
        self.url = normalize_url(url)
        self._auth = aiohttp.BasicAuth("homeassistant", password) if password else None

    async def _request(self, method: str, path: str, **kw) -> aiohttp.ClientResponse:
        try:
            resp = await self._session.request(method, self.url + path, auth=self._auth,
                                               timeout=aiohttp.ClientTimeout(total=15), **kw)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise ManometerError(f"Nicht erreichbar: {err}") from err
        if resp.status == 401:
            resp.release()
            raise ManometerAuthError("Passwort falsch")
        if resp.status >= 400:
            text = await resp.text()
            raise ManometerError(f"HTTP {resp.status}: {text[:200]}")
        return resp

    async def status(self) -> dict[str, Any]:
        resp = await self._request("GET", "/api/status")
        try:
            data = await resp.json(content_type=None)
        except (ValueError, aiohttp.ContentTypeError) as err:
            raise ManometerError("Keine gültige Antwort – ist das wirklich der Manometer-Leser?") from err
        if not isinstance(data, dict) or "last" not in data:
            raise ManometerError("Keine gültige Antwort – ist das wirklich der Manometer-Leser?")
        return data

    async def last_image(self) -> bytes | None:
        try:
            resp = await self._request("GET", "/img/last.jpg")
        except ManometerError:
            return None
        return await resp.read()

    async def measure(self) -> None:
        resp = await self._request("POST", "/api/measure", json={})
        resp.release()
