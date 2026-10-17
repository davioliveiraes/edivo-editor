#!/usr/bin/env python3
"""Exercise R02 failure limits against real PostgreSQL/FFprobe in an isolated API process.

Runs a disposable Compose process, never stops services or removes existing data. Its
small synthetic imports remain preserved in a logically deleted QA project. No mocks
replace PostgreSQL, multipart handling, storage, FFmpeg, FFprobe or subprocess timeout.
"""

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "EDIVO_UPLOAD_LIMITS_RESULT="

CONTAINER_CHECK = r"""
import hashlib
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import Settings
from app.main import create_app
from app.models.media_asset import MediaAsset

checks = []
settings = Settings()
root = settings.storage_root
application = create_app(settings)


def verify(name, condition):
    if not condition:
        raise RuntimeError("check_failed:" + name)
    checks.append({"check": name, "status": "passed"})


def assets_count(project_id):
    with application.state.database.sessions() as session:
        return session.scalar(select(func.count()).select_from(MediaAsset).where(
            MediaAsset.project_id == UUID(project_id)))


def upload(client, project_id, file, filename="qa.mp4"):
    return client.post(f"/api/v1/projects/{project_id}/media",
                       files={"file": (filename, file, "video/mp4")})


def multipart(data):
    return (b'--edivo-qa\r\nContent-Disposition: form-data; name="file"; filename="qa.mp4"'
            b'\r\nContent-Type: video/mp4\r\n\r\n' + data + b'\r\n--edivo-qa--\r\n')


def synthetic(path, seconds, audio=False):
    argv = [settings.ffmpeg_bin, "-v", "error", "-nostdin", "-f", "lavfi", "-i",
            "color=c=blue:s=160x90:r=24"]
    if audio:
        argv += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono"]
    argv += ["-t", str(seconds), "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p"]
    argv += ["-c:a", "aac"] if audio else ["-an"]
    argv += [str(path)]
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=20, check=False)
    if result.returncode:
        raise RuntimeError("synthetic_fixture_failed")


with TemporaryDirectory(prefix="qa-upload-limits-", dir=root / "temp") as workspace:
    workspace = Path(workspace)
    short = workspace / "short.mp4"
    sound = workspace / "sound.mp4"
    long = workspace / "long.mp4"
    synthetic(short, 0.5)
    synthetic(sound, 0.5, audio=True)
    synthetic(long, 2)
    verify("real_ffmpeg_fixtures_below_file_limit",
           max(short.stat().st_size, sound.stat().st_size, long.stat().st_size)
           < settings.upload_max_bytes)
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.post("/api/v1/projects", json={"name": "QA R02 limites sintéticos"})
        verify("postgres_project_created", response.status_code == 201)
        project_id = response.json()["id"]
        try:
            silent_response = upload(client, project_id, short.read_bytes())
            verify("real_mp4_without_audio_accepted", silent_response.status_code == 201
                   and silent_response.json()["has_audio"] is False)
            audio_response = upload(client, project_id, sound.read_bytes())
            verify("real_mp4_with_audio_accepted", audio_response.status_code == 201
                   and audio_response.json()["has_audio"] is True
                   and audio_response.json()["audio_codec"] == "aac")
            asset = silent_response.json()
            with application.state.database.sessions() as session:
                persisted = session.get(MediaAsset, UUID(asset["id"]))
                original = root / persisted.storage_key
            original_hash = hashlib.sha256(original.read_bytes()).hexdigest()
            verify("sha256_and_actual_bytes_match", original_hash == asset["checksum_sha256"]
                   and original.read_bytes() == short.read_bytes()
                   and original.stat().st_size == asset["byte_size"])

            response = upload(client, project_id, long.read_bytes())
            verify("real_ffprobe_duration_limit_422", response.status_code == 422
                   and response.json()["code"] == "media_duration_exceeded")

            body = multipart(b"x" * (settings.upload_max_bytes + 1))
            response = client.post(f"/api/v1/projects/{project_id}/media",
                                   content=iter([body[:128], body[128:]]),
                                   headers={"Content-Type":
                                            "multipart/form-data; boundary=edivo-qa"})
            verify("file_limit_without_content_length_413", response.status_code == 413)

            ingress_body = b"x" * (settings.upload_max_bytes
                                    + settings.upload_multipart_overhead_bytes + 1)
            for label, content in (
                ("with_content_length", ingress_body),
                ("without_content_length", iter([ingress_body[:100], ingress_body[100:]])),
            ):
                response = client.post(f"/api/v1/projects/{project_id}/media", content=content,
                                       headers={"Content-Type":
                                                "multipart/form-data; boundary=edivo-qa"})
                verify("ingress_limit_" + label + "_413", response.status_code == 413)

            initial_bin = settings.ffprobe_bin
            settings.ffprobe_bin = "/not-a-real-qa-ffprobe"
            response = upload(client, project_id, short.read_bytes())
            verify("missing_ffprobe_503", response.status_code == 503
                   and response.json()["code"] == "probe_unavailable")
            settings.ffprobe_bin = initial_bin

            initial_timeout = settings.ffprobe_timeout_seconds
            # Real executable; the deliberately tiny deadline exercises kill/reap, not performance.
            settings.ffprobe_timeout_seconds = 0.000001
            response = upload(client, project_id, short.read_bytes())
            verify("real_ffprobe_artificial_deadline_503", response.status_code == 503
                   and response.json()["code"] == "probe_timeout")
            settings.ffprobe_timeout_seconds = initial_timeout

            settings.storage_root = short  # A regular file is not an accessible storage directory.
            response = upload(client, project_id, short.read_bytes())
            verify("storage_unavailable_503", response.status_code == 503
                   and response.json()["code"] == "storage_unavailable")
            settings.storage_root = root

            for filename in ("bad.mp4evil", "bad.mov"):
                response = upload(client, project_id, short.read_bytes(), filename=filename)
                verify("extension_rejected_" + filename.rsplit(".", 1)[-1],
                       response.status_code == 415)

            response = upload(client, project_id, short.read_bytes()[:-20])
            verify("corrupt_real_mp4_422", response.status_code == 422)
            verify("rejections_created_no_database_assets", assets_count(project_id) == 2)
            verify("staging_clean_after_all_failures", not list((root / "temp").glob("upload-*")))
            verify("original_immutable_after_failures",
                   hashlib.sha256(original.read_bytes()).hexdigest() == original_hash
                   and original.stat().st_mode & 0o777 == 0o400)
        finally:
            settings.storage_root = root
            settings.ffprobe_bin = (initial_bin if "initial_bin" in locals()
                                   else settings.ffprobe_bin)
            settings.ffprobe_timeout_seconds = (initial_timeout if "initial_timeout" in locals()
                                                else settings.ffprobe_timeout_seconds)
            deleted = client.delete(f"/api/v1/projects/{project_id}")
            verify("qa_project_soft_deleted_originals_preserved", deleted.status_code == 204)

print("EDIVO_UPLOAD_LIMITS_RESULT=" + json.dumps({
    "status": "passed", "checks": checks,
    "configuration": {"upload_max_bytes": settings.upload_max_bytes,
                      "multipart_overhead_bytes": settings.upload_multipart_overhead_bytes,
                      "duration_seconds": settings.media_max_duration_seconds},
    "data_policy": "QA projects are soft-deleted; imported originals are preserved.",
    "timeout_note": "Real FFprobe, deliberately artificial 1 microsecond deadline.",
}, ensure_ascii=False))
"""


def main() -> int:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "-e",
            "EDIVO_UPLOAD_MAX_BYTES=65536",
            "-e",
            "EDIVO_UPLOAD_MULTIPART_OVERHEAD_BYTES=1024",
            "-e",
            "EDIVO_MEDIA_MAX_DURATION_SECONDS=1",
            "api",
            "python",
            "-",
        ],
        cwd=ROOT,
        input=CONTAINER_CHECK,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    outputs = [
        line[len(PREFIX) :] for line in result.stdout.splitlines() if line.startswith(PREFIX)
    ]
    if result.returncode != 0 or not outputs:
        # Container tracebacks can contain private host paths; emit only a stable failure marker.
        detail = re.search(r"RuntimeError: (check_failed:[a-zA-Z0-9_]+)", result.stderr)
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": "upload_limits_acceptance_failed",
                    "check": detail.group(1) if detail else "runtime_failed",
                }
            )
        )
        return 1
    print(json.dumps(json.loads(outputs[-1]), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
