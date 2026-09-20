"""Read-only access to Notion Agent Skills exports."""
from __future__ import annotations

import json
import subprocess
import urllib.request
from dataclasses import dataclass


class NotionImportError(ValueError):
    pass


class _HttpsRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise NotionImportError("non_https_redirect")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


@dataclass(frozen=True)
class Export:
    kind: str
    id: str
    version_id: str
    archive: bytes


class NotionExportClient:
    def __init__(self, *, runner=None, opener=None, max_archive_bytes=25_000_000):
        self.runner = runner or self._run_ntn
        self.opener = opener or urllib.request.build_opener(_HttpsRedirects())
        self.max_archive_bytes = max_archive_bytes

    @staticmethod
    def _run_ntn(path):
        process = subprocess.run(["ntn", "api", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if process.returncode:
            raise NotionImportError("notion_api_failure")
        try:
            return json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            raise NotionImportError("invalid_notion_response") from exc

    def inspect(self, kind, identity):
        if kind not in {"skill", "plugin"} or not identity:
            raise NotionImportError("invalid_export_identity")
        plural = "skills" if kind == "skill" else "plugins"
        value = self.runner(f"v1/ai/{plural}/{identity}")
        if not isinstance(value, dict) or value.get("id") != identity:
            raise NotionImportError("notion_identity_mismatch")
        version = value.get("version_id")
        url = value.get("url")
        if not isinstance(version, str) or len(version) != 64 or not isinstance(url, str) or not url.startswith("https://"):
            raise NotionImportError("invalid_notion_export")
        return {"kind": kind, "id": identity, "version_id": version, "url": url}

    def fetch(self, kind, identity):
        metadata = self.inspect(kind, identity)
        # This request deliberately carries no Notion authorization headers.
        try:
            response = self.opener.open(urllib.request.Request(metadata["url"]), timeout=30)
            if not response.geturl().lower().startswith("https://"):
                raise NotionImportError("non_https_download")
            archive = response.read(self.max_archive_bytes + 1)
        except NotionImportError:
            raise
        except Exception as exc:
            raise NotionImportError("archive_download_failure") from exc
        if len(archive) > self.max_archive_bytes:
            raise NotionImportError("archive_too_large")
        return Export(kind, identity, metadata["version_id"], archive)
