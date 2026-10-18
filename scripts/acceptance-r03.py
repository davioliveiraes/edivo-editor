#!/usr/bin/env python3
"""R03 real HTTP acceptance using synthetic media and the running Edivo stack.

Creates only QA projects, soft-deletes them and preserves imported originals.
Requires Compose and existing local .env; never removes files or volumes.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import importlib.util
import json
import sys
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
module_spec = importlib.util.spec_from_file_location("r02", ROOT / "scripts/acceptance-r02.py")
assert module_spec and module_spec.loader
r02 = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(r02)
foundation = r02.foundation


def stream_request(path, method="GET", headers=None):
    connection = http.client.HTTPConnection(
        "127.0.0.1", foundation.configured_port("EDIVO_API_PORT", 8000), timeout=30
    )
    try:
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def main():
    checks, projects = [], []

    def verify(name, condition, detail=None):
        foundation.require(bool(condition), name)
        checks.append({"check": name, "status": "passed", "detail": detail})
        print(json.dumps(checks[-1], ensure_ascii=False), flush=True)

    def project(name):
        status, body = r02.json_request("POST", "/api/v1/projects", {"name": name})
        foundation.require(status == 201, "qa_project_create_failed")
        projects.append(body["id"])
        return body["id"]

    try:
        foundation.wait_ready()
        fixtures = json.loads(foundation.execute_python("api", r02.FIXTURES))
        media = base64.b64decode(fixtures["audio"])
        current = project(f"QA R03 stream {uuid4().hex[:8]}")
        other = project("QA R03 stream isolation")
        status, asset = r02.upload(current, media)
        verify("r02_real_upload_preserved", status == 201)
        url = f"/api/v1/projects/{current}/media/{asset['id']}/stream"
        baseline = r02.storage_snapshot(current)
        status, headers, body = stream_request(url)
        lower = {key.lower(): value for key, value in headers.items()}
        verify("stream_get_200_exact_bytes", status == 200 and body == media)
        verify(
            "stream_security_and_length_headers",
            lower.get("content-type") == "video/mp4"
            and lower.get("accept-ranges") == "bytes"
            and lower.get("cache-control") == "private, no-store"
            and lower.get("x-content-type-options") == "nosniff"
            and int(lower.get("content-length", 0)) == len(media)
            and lower.get("etag") == f'"sha256:{hashlib.sha256(media).hexdigest()}"',
        )
        status, headers, body = stream_request(url, "HEAD")
        lower_head = {key.lower(): value for key, value in headers.items()}
        verify(
            "stream_head_200_no_body",
            status == 200 and body == b"" and lower_head.get("content-length") == str(len(media)),
        )
        ranges = [
            ("bytes=0-9", 0, 10),
            ("bytes=10-30", 10, 31),
            (f"bytes={len(media) - 10}-{len(media) - 1}", len(media) - 10, len(media)),
            ("bytes=10-", 10, len(media)),
            ("bytes=-10", len(media) - 10, len(media)),
            ("bytes=5-5", 5, 6),
            (f"bytes=-{len(media) + 10}", 0, len(media)),
            (f"bytes=0-{len(media) + 10}", 0, len(media)),
        ]
        for value, start, end in ranges:
            status, headers, body = stream_request(url, headers={"Range": value})
            lowered = {key.lower(): item for key, item in headers.items()}
            verify(
                f"range_{value}_206_correct_bytes",
                status == 206
                and body == media[start:end]
                and lowered.get("content-range") == f"bytes {start}-{end - 1}/{len(media)}"
                and lowered.get("content-length") == str(end - start),
            )
        status, headers, body = stream_request(url, "HEAD", {"Range": "bytes=5-5"})
        lowered = {key.lower(): value for key, value in headers.items()}
        verify(
            "head_range_206_no_body",
            status == 206
            and not body
            and lowered.get("content-range") == f"bytes 5-5/{len(media)}"
            and lowered.get("content-length") == "1",
        )
        for value in (
            "bytes=-0",
            "bytes=10-5",
            "bytes=0-1,3-4",
            "invalid",
            f"bytes={len(media)}-",
            "bytes=abc-def",
            "bytes=-",
            "items=0-1",
        ):
            status, headers, body = stream_request(url, headers={"Range": value})
            lowered = {key.lower(): item for key, item in headers.items()}
            error = json.loads(body)
            verify(
                f"range_invalid_{value}_416",
                status == 416
                and lowered.get("content-range") == f"bytes */{len(media)}"
                and set(error) == {"code", "message", "request_id"},
            )
        for validator, expected in (
            (lower["etag"], 206),
            ('"other"', 200),
            (f"W/{lower['etag']}", 200),
        ):
            status, _, body = stream_request(
                url, headers={"Range": "bytes=0-9", "If-Range": validator}
            )
            verify(
                f"if_range_{expected}_{validator[:4]}",
                status == expected and body == (media[:10] if expected == 206 else media),
            )
        wrong = f"/api/v1/projects/{other}/media/{asset['id']}/stream"
        verify("stream_foreign_project_404", stream_request(wrong)[0] == 404)
        # Abort one valid response; the descriptor must close even when send is cancelled.
        connection = http.client.HTTPConnection(
            "127.0.0.1", foundation.configured_port("EDIVO_API_PORT", 8000), timeout=30
        )
        connection.request("GET", url)
        response = connection.getresponse()
        response.read(32)
        response.close()
        connection.close()
        time.sleep(0.2)
        verify(
            "original_unchanged_after_streaming_and_disconnect",
            r02.storage_snapshot(current) == baseline,
        )
        verify(
            "soft_delete_project_204",
            r02.request("DELETE", f"/api/v1/projects/{current}")[0] == 204,
        )
        verify(
            "stream_deleted_project_get_head_404",
            stream_request(url)[0] == 404 and stream_request(url, "HEAD")[0] == 404,
        )
        verify("deleted_project_original_preserved", r02.storage_snapshot(current) == baseline)
        print(json.dumps({"status": "passed", "checks": len(checks)}), flush=True)
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "completed_checks": len(checks),
                    "message": str(error)
                    if isinstance(error, foundation.AcceptanceError)
                    else type(error).__name__,
                }
            ),
            flush=True,
        )
        return 1
    finally:
        for project_id in projects:
            try:
                r02.request("DELETE", f"/api/v1/projects/{project_id}")
            except (OSError, http.client.HTTPException):
                print("Synthetic QA project retained for review.", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
