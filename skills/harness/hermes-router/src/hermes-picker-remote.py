"""One bounded SSH request. Credentials are read and used only on the VPS."""
import base64
import json
import os
from pathlib import Path
import shlex
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request


def run(data):
    if data.get("action") == "file":
        requested = Path(data["path"])
        resolved = requested.resolve(strict=True)
        roots = [Path(root).resolve(strict=True) for root in data.get("roots", []) if Path(root).exists()]
        if not roots or not any(resolved.is_relative_to(root) for root in roots):
            raise ValueError("Artifact is outside the configured output directories")
        if any(part.startswith(".") for part in resolved.parts):
            raise ValueError("Hidden files are not artifacts")
        limit = min(int(data.get("maxBytes", 20971520)), 20971520)
        fd = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise ValueError("Artifact is not a regular file within the size limit")
            content = file.read(limit + 1)
            if len(content) > limit:
                raise ValueError("Artifact exceeds size limit")
        return {"status": 200, "data": {"base64": base64.b64encode(content).decode(), "bytes": len(content)}}

    base = data.get("baseUrl", "http://127.0.0.1:8766")
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost"):
        raise ValueError("Only the remote loopback Hermes API is allowed")
    route = data["path"]
    if not (route in ("/health", "/health/detailed", "/v1/capabilities") or route.startswith("/api/sessions")):
        raise ValueError("Unsupported session API route")
    if ".." in route or "#" in route or "\\" in route:
        raise ValueError("Invalid session API route")
    key = os.environ.get("API_SERVER_KEY", "")
    if not key:
        for line in Path(data.get("envPath", "~/.hermes/.env")).expanduser().read_text().splitlines():
            candidate = line.strip().removeprefix("export ")
            name, sep, value = candidate.partition("=")
            if sep and name.strip() == "API_SERVER_KEY":
                parts = shlex.split(value, comments=True)
                key = parts[0] if parts else ""
                break
    if len(key) < 16:
        raise ValueError("Remote Hermes API credential is unavailable")
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    body = json.dumps(data["body"]).encode() if "body" in data else None
    request = urllib.request.Request(base.rstrip("/") + route, data=body, headers=headers, method=data.get("method", "GET"))
    try:
        with urllib.request.urlopen(request, timeout=min(data.get("timeoutSeconds", 240), 600)) as reply:
            raw = reply.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise ValueError("Hermes response exceeds limit")
            return {"status": reply.status, "data": json.loads(raw)}
    except urllib.error.HTTPError as error:
        # Do not expose upstream bodies or credential-bearing diagnostics.
        return {"status": error.code, "error": "Hermes API returned HTTP " + str(error.code)}


if __name__ == "__main__":
    try:
        payload = sys.stdin.buffer.read(2 * 1024 * 1024 + 1)
        if len(payload) > 2 * 1024 * 1024:
            raise ValueError("Request exceeds limit")
        print(json.dumps(run(json.loads(payload))))
    except Exception as error:
        print(json.dumps({"status": 500, "error": "Remote Hermes operation failed: " + type(error).__name__}))
