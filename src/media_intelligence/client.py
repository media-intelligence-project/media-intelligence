"""Synchronous Python client for the Media Intelligence HTTP API.

Example:

    import os
    from media_intelligence.client import MediaIntelligenceClient

    api = MediaIntelligenceClient("http://localhost:8000", os.environ["LOCAL_API_TOKEN"])
    page = api.list_objects(type="HashArtifact", limit=20)
"""
from __future__ import annotations

import json
from http.client import HTTPConnection, HTTPSConnection
from urllib.parse import quote, urlencode, urlsplit


class MediaIntelligenceError(Exception):
    """An HTTP error from the service, including its structured detail when available."""

    def __init__(self, status: int, detail: object):
        self.status = status
        self.detail = detail
        super().__init__(f"HTTP {status}: {detail}")


class MediaIntelligenceClient:
    """Return decoded JSON. Transport errors propagate to the caller."""

    def __init__(self, base_url: str, token: str | None = None, *, timeout: float = 30):
        url = urlsplit(base_url)
        if url.scheme not in {"http", "https"} or not url.hostname:
            raise ValueError("base_url must be an absolute HTTP or HTTPS URL")
        if url.username is not None or url.query or url.fragment:
            raise ValueError("base_url must omit credentials, query and fragment")
        self._connection = HTTPSConnection if url.scheme == "https" else HTTPConnection
        self._host = url.hostname
        self._port = url.port
        self._prefix = url.path.rstrip("/")
        self._token = token
        self._timeout = timeout

    def _request(self, method, path, *, query=None, body=None, headers=None):
        target = self._prefix + path
        if query:
            target += "?" + urlencode({k: v for k, v in query.items() if v is not None})
        request_headers = {"Accept": "application/json"}
        if self._token:
            request_headers["Authorization"] = f"Bearer {self._token}"
        if headers:
            request_headers.update({k: v for k, v in headers.items() if v is not None})
        payload = None
        if body is not None:
            request_headers["Content-Type"] = "application/json"
            payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
        connection = self._connection(self._host, self._port, timeout=self._timeout)
        try:
            connection.request(method, target, body=payload, headers=request_headers)
            response = connection.getresponse()
            raw = response.read()
            if not 200 <= response.status < 300:
                try:
                    detail = json.loads(raw)
                except (ValueError, UnicodeError):
                    detail = raw.decode(errors="replace") or response.reason
                if isinstance(detail, dict):
                    detail = detail.get("detail", detail)
                raise MediaIntelligenceError(response.status, detail)
            return json.loads(raw)
        finally:
            connection.close()

    def live(self) -> dict:
        return self._request("GET", "/health/live")

    def ready(self) -> dict:
        return self._request("GET", "/health/ready")

    def profile(self) -> dict:
        return self._request("GET", "/profile")

    def package_schema(self) -> dict:
        return self._request("GET", "/schema/package")

    def create_object(self, type: str, data: dict, *, marking_id: str | None = None,
                      idempotency_key: str | None = None) -> dict:
        """Create any publication type using its type-specific fields."""
        return self._request("POST", "/objects", body={"type": type, "data": data, "marking_id": marking_id},
                             headers={"Idempotency-Key": idempotency_key})

    def create_artifact_batch(self, items: list[dict], *, marking_id: str | None = None,
                              idempotency_key: str | None = None) -> list[dict]:
        """Submit artifact creation requests together. Each item contains type and data."""
        return self._request("POST", "/artifacts/batch", body={"items": items, "marking_id": marking_id},
                             headers={"Idempotency-Key": idempotency_key})

    def list_objects(self, *, type: str | None = None, created_by: str | None = None,
                     algorithm: str | None = None, value: str | None = None,
                     offset: int = 0, limit: int = 100) -> dict:
        return self._request("GET", "/objects", query={"type": type, "created_by": created_by,
                             "algorithm": algorithm, "value": value, "offset": offset, "limit": limit})

    def get_object(self, object_id: str) -> dict:
        return self._request("GET", "/objects/" + quote(object_id, safe=""))

    def list_versions(self, object_id: str, *, offset: int = 0, limit: int = 100) -> dict:
        return self._request("GET", f"/objects/{quote(object_id, safe='')}/versions",
                             query={"offset": offset, "limit": limit})

    def create_version(self, object_id: str, expected_version: int, changes: dict) -> dict:
        return self._request("POST", f"/objects/{quote(object_id, safe='')}/versions",
                             body={"expected_version": expected_version, "changes": changes})

    def list_relations(self, *, source_id: str | None = None, target_id: str | None = None,
                       include_inactive: bool = False, offset: int = 0, limit: int = 100) -> dict:
        return self._request("GET", "/relations", query={"source_id": source_id, "target_id": target_id,
                             "include_inactive": str(include_inactive).lower(), "offset": offset, "limit": limit})

    def correlations(self, *, offset: int = 0, limit: int = 100) -> dict:
        return self._request("GET", "/correlations", query={"offset": offset, "limit": limit})

    def export_package(self, recipient: str, ids: list[str]) -> dict:
        return self._request("POST", "/exchange/export", body={"recipient": recipient, "ids": ids})

    def import_package(self, package: dict, *, verified_sender: str | None = None) -> dict:
        """Supply verified_sender for local file imports. Omit it for peer transfers."""
        return self._request("POST", "/exchange/import", body=package,
                             headers={"X-Verified-Sender": verified_sender})
