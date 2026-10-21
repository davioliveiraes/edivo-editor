#!/usr/bin/env python3
"""Exercise R04 MP3 security through the running loopback HTTP API.

Uses synthetic fixtures, creates its own project and soft-deletes only that
project. Existing originals are hashed before/after; every imported original
is preserved. No restart, database writes outside HTTP, or raw payload logging.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import stat
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
MAX_AUDIO_BYTES = 30 * 1024 * 1024
BOUNDARY = "edivo-r04-mp3-security"


class CheckFailed(RuntimeError):
    pass


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def storage_snapshot(storage: Path) -> dict:
    originals = {}
    for namespace, filename in (("originals", "source.mp4"), ("originals-audio", "source.mp3")):
        for path in sorted((storage / namespace).glob(f"*/*/{filename}")):
            if any(part.is_symlink() for part in (path, *path.parents)):
                raise CheckFailed("unsafe_original")
            metadata = path.stat()
            originals[f"{namespace}:{path.parent.name}"] = {
                "sha256": checksum(path),
                "byte_size": metadata.st_size,
                "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
            }
    return {"originals": originals, "temp_entries": len(list((storage / "temp").iterdir()))}


def frame_info(header: bytes) -> tuple[int, Fraction]:
    bits = int.from_bytes(header)
    version, layer = (bits >> 19) & 3, (bits >> 17) & 3
    bitrate_index, rate_index = (bits >> 12) & 15, (bits >> 10) & 3
    if len(header) != 4 or bits >> 21 != 0x7FF or version == 1 or layer != 1:
        raise CheckFailed("unexpected_fixture_frame")
    if bitrate_index in (0, 15) or rate_index == 3:
        raise CheckFailed("unexpected_fixture_frame")
    rate = (44100, 48000, 32000)[rate_index]
    if version != 3:
        rate //= 2 if version == 2 else 4
    bitrates = (
        (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
        if version == 3
        else (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
    )
    length = (144000 if version == 3 else 72000) * bitrates[bitrate_index] // rate
    return length + ((bits >> 9) & 1), Fraction(1152 if version == 3 else 576, rate)


def id3_offset(data: bytes | bytearray) -> int:
    if data[:3] != b"ID3":
        return 0
    size = 0
    for byte in data[6:10]:
        if byte & 128:
            raise CheckFailed("unexpected_fixture_tag")
        size = (size << 7) | byte
    return 10 + size


def structural_duration(data: bytes | bytearray) -> Fraction:
    offset, duration = id3_offset(data), Fraction(0)
    while offset < len(data):
        if len(data) - offset == 128 and data[offset : offset + 3] == b"TAG":
            break
        length, frame_duration = frame_info(data[offset : offset + 4])
        offset += length
        duration += frame_duration
        if offset > len(data):
            raise CheckFailed("unexpected_fixture_truncation")
    return duration


def forged_payloads(original: bytes) -> dict[str, bytes]:
    start = id3_offset(original)
    length, _ = frame_info(original[start : start + 4])
    info = original.find(b"Info", start, start + length)
    if info < 0:
        raise CheckFailed("fixture_info_header_missing")
    long_info = bytearray(original[: start + length] + original[start + length :] * 55)
    long_info[info + 12 : info + 16] = (len(long_info) - start).to_bytes(4)
    long_xing = bytearray(long_info)
    long_xing[info : info + 4] = b"Xing"
    short_info = bytearray(original)
    short_info[info + 8 : info + 12] = (40).to_bytes(4)
    return {
        "forged_long_info": bytes(long_info),
        "forged_long_xing": bytes(long_xing),
        "forged_short_info": bytes(short_info),
    }


def file_header(name: str = "security.mp3", field: str = "file") -> bytes:
    return (
        f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{field}"; '
        f'filename="{name}"\r\nContent-Type: audio/mpeg\r\n\r\n'
    ).encode()


def multipart(data: bytes, *, name: str = "security.mp3", extra: str | None = None) -> bytes:
    body = file_header(name) + data + b"\r\n"
    if extra == "duplicate":
        body += file_header("second.mp3") + data + b"\r\n"
    elif extra == "field":
        body += (
            f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="extra"\r\n\r\nunexpected\r\n'
        ).encode()
    return body + f"--{BOUNDARY}--\r\n".encode()


class HTTPClient:
    def __init__(self, port: int, records: list[dict]) -> None:
        self.port, self.records = port, records

    def request(
        self,
        method: str,
        path: str,
        *,
        body=None,
        content_type: str | None = None,
        chunked: bool = False,
        label: str,
    ) -> tuple[int | None, dict | None]:
        headers = {"Connection": "close"}
        if content_type:
            headers["Content-Type"] = content_type
        if chunked:
            headers["Transfer-Encoding"] = "chunked"
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=45)
        record = {
            "case": label,
            "command": f"HTTP {method} {path}",
            "transfer_encoding": "chunked" if chunked else "identity",
            "content_length_sent": not chunked and body is not None,
        }
        self.records.append(record)
        try:
            try:
                connection.request(method, path, body=body, headers=headers, encode_chunked=chunked)
            except OSError as error:
                # An early close is not a rejection proof. Read a real HTTP response
                # if the peer left it available; otherwise report an unproven case.
                record["send_error"] = type(error).__name__
            response = connection.getresponse()
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise CheckFailed("unexpected_response_size")
            result = json.loads(raw) if raw else None
            record["status_code"] = response.status
            if isinstance(result, dict):
                record["response_code"] = result.get("code")
                if response.status >= 400:
                    safe = set(result) == {"code", "message", "request_id"}
                    safe = safe and result["request_id"] == response.getheader("X-Request-ID")
                    safe = safe and not any(
                        marker in raw.decode(errors="replace")
                        for marker in ("Traceback", "postgresql", "/home/", "/data/", "password=")
                    )
                    record["safe_error_envelope"] = safe
                    if not safe:
                        raise CheckFailed("unsafe_error_response")
            return response.status, result
        except (OSError, http.client.HTTPException, ValueError) as error:
            record["receive_error"] = type(error).__name__
            record["status_code"] = None
            return None, None
        finally:
            connection.close()


def configured_port() -> int:
    candidate = os.environ.get("EDIVO_API_PORT")
    local_environment = ROOT / ".env"
    if candidate is None and local_environment.is_file():
        for line in local_environment.read_text().splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() == "EDIVO_API_PORT":
                candidate = value.strip().strip("\"'")
    return int(candidate or "8000")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-port", type=int, default=None)
    parser.add_argument("--fixtures", type=Path, default=Path("/tmp/edivo-r04/fixtures"))
    parser.add_argument(
        "--output", type=Path, default=ROOT / "docs/r04/evidence/mp3-http-security.json"
    )
    parser.add_argument("--skip-large-chunked", action="store_true")
    args = parser.parse_args()
    records, checks = [], []
    evidence = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "status": "running",
        "scope": "Real HTTP requests to the current API; no TestClient, mocks or service restarts.",
        "command": "python3 scripts/acceptance-r04-mp3-security.py",
        "requests": records,
        "checks": checks,
        "data_policy": "Own synthetic project is soft-deleted; every original remains preserved.",
    }
    project_id, positive_asset, before = None, None, None
    client = HTTPClient(args.api_port or configured_port(), records)

    def verify(name: str, condition: bool) -> None:
        checks.append({"check": name, "status": "passed" if condition else "failed"})
        if not condition:
            raise CheckFailed(name)

    def upload_case(name: str, body: bytes, expected: int, code: str | None = None):
        status, result = client.request(
            "POST",
            f"/api/v1/projects/{project_id}/audio",
            body=body,
            content_type=f"multipart/form-data; boundary={BOUNDARY}",
            label=name,
        )
        verify(name, status == expected and (code is None or result.get("code") == code))
        return result

    try:
        original = (args.fixtures / "music-long.mp3").read_bytes()
        cover = (args.fixtures / "invalid-cover.mp3").read_bytes()
        mp4 = (args.fixtures / "horizontal.mp4").read_bytes()
        fixtures = {
            name: checksum(args.fixtures / name)
            for name in ("music-long.mp3", "invalid-cover.mp3", "horizontal.mp4")
        }
        evidence["fixture_sha256_before"] = fixtures
        before = storage_snapshot(ROOT / "storage")
        evidence["storage_before"] = before
        status, created = client.request(
            "POST",
            "/api/v1/projects",
            body=json.dumps({"name": f"QA R04 MP3 security {uuid4()}"}),
            content_type="application/json",
            label="create_own_synthetic_project",
        )
        verify("synthetic_project_created", status == 201)
        project_id = created["id"]
        evidence["project_id"] = project_id
        positive_asset = upload_case("legitimate_mp3", multipart(original), 201)
        evidence["legitimate_asset"] = {
            key: positive_asset[key]
            for key in ("id", "checksum_sha256", "byte_size", "duration_ms")
        }
        verify(
            "http_sha_matches_source",
            positive_asset["checksum_sha256"] == hashlib.sha256(original).hexdigest(),
        )
        forged = forged_payloads(original)
        evidence["forged_inputs"] = {
            label: {
                "byte_size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "structural_duration_seconds": float(structural_duration(data)),
                "structural_duration_exact": str(structural_duration(data)),
            }
            for label, data in forged.items()
        }
        verify(
            "forged_long_input_exceeds_600_seconds",
            structural_duration(forged["forged_long_info"]) > 600,
        )
        for label, data in forged.items():
            code = "invalid_audio" if label == "forged_short_info" else "audio_duration_exceeded"
            upload_case(label, multipart(data), 422, code)
        upload_case("mp4_renamed_mp3", multipart(mp4), 415, "unsupported_audio")
        upload_case("mp3_with_attached_cover", multipart(cover), 415, "unsupported_audio")
        upload_case(
            "multipart_extra_field", multipart(original, extra="field"), 400, "invalid_multipart"
        )
        upload_case(
            "multipart_duplicate_file",
            multipart(original, extra="duplicate"),
            400,
            "invalid_multipart",
        )
        if not args.skip_large_chunked:
            generated = {"file_bytes": 0}

            def oversized_body():
                yield file_header()
                while generated["file_bytes"] <= MAX_AUDIO_BYTES:
                    chunk = original[
                        : min(len(original), MAX_AUDIO_BYTES + 1 - generated["file_bytes"])
                    ]
                    generated["file_bytes"] += len(chunk)
                    yield chunk
                yield f"\r\n--{BOUNDARY}--\r\n".encode()

            status, result = client.request(
                "POST",
                f"/api/v1/projects/{project_id}/audio",
                body=oversized_body(),
                content_type=f"multipart/form-data; boundary={BOUNDARY}",
                chunked=True,
                label="over_30_mib_without_content_length",
            )
            evidence["large_chunked"] = {
                "file_bytes_generated": generated["file_bytes"],
                "file_limit_bytes": MAX_AUDIO_BYTES,
                "observed_http_status": status,
                "proof": "observed_http_response"
                if status is not None
                else "transport_closed_unproven",
            }
            if status is None:
                checks.append({"check": "large_chunked_413", "status": "unproven"})
            else:
                verify(
                    "large_chunked_413", status == 413 and result.get("code") == "upload_too_large"
                )
        status, page = client.request(
            "GET", f"/api/v1/projects/{project_id}/audio", label="inventory_after_rejections"
        )
        verify("rejections_created_no_audio_rows", status == 200 and page["total"] == 1)
        evidence["status"] = (
            "partial" if any(c["status"] == "unproven" for c in checks) else "passed"
        )
    except (CheckFailed, OSError, ValueError, KeyError) as error:
        evidence["status"] = "failed"
        evidence["failure_code"] = (
            str(error) if isinstance(error, CheckFailed) else type(error).__name__
        )
    finally:
        if project_id is not None:
            status, _ = client.request(
                "DELETE", f"/api/v1/projects/{project_id}", label="soft_delete_only_own_project"
            )
            checks.append(
                {
                    "check": "own_project_soft_deleted",
                    "status": "passed" if status == 204 else "failed",
                }
            )
            if status != 204:
                evidence["status"] = "failed"
            status, _ = client.request(
                "GET",
                f"/api/v1/projects/{project_id}/audio",
                label="deleted_project_access_blocked",
            )
            checks.append(
                {
                    "check": "deleted_project_audio_404",
                    "status": "passed" if status == 404 else "failed",
                }
            )
            if status != 404:
                evidence["status"] = "failed"
        if before is not None:
            try:
                after = storage_snapshot(ROOT / "storage")
                evidence["storage_after"] = after
                unchanged = all(
                    after["originals"].get(key) == value
                    for key, value in before["originals"].items()
                )
                checks.append(
                    {
                        "check": "preexisting_originals_sha_size_mode_unchanged",
                        "status": "passed" if unchanged else "failed",
                    }
                )
                temps_clean = after["temp_entries"] == before["temp_entries"] == 0
                checks.append(
                    {
                        "check": "temporary_entries_zero_before_after",
                        "status": "passed" if temps_clean else "failed",
                    }
                )
                if positive_asset:
                    own = after["originals"].get("originals-audio:" + positive_asset["id"])
                    preserved = bool(
                        own
                        and own["mode"] == "0400"
                        and own["sha256"] == positive_asset["checksum_sha256"]
                    )
                    checks.append(
                        {
                            "check": "own_original_preserved_0400_after_soft_delete",
                            "status": "passed" if preserved else "failed",
                        }
                    )
                    unchanged = unchanged and preserved
                if not unchanged or not temps_clean:
                    evidence["status"] = "failed"
                fixture_after = {
                    name: checksum(args.fixtures / name)
                    for name in evidence["fixture_sha256_before"]
                }
                evidence["fixture_sha256_after"] = fixture_after
                if fixture_after != evidence["fixture_sha256_before"]:
                    evidence["status"] = "failed"
            except OSError:
                evidence["status"] = "failed"
                evidence["storage_audit_unavailable"] = True
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {"status": evidence["status"], "checks": len(checks), "http_requests": len(records)},
            ensure_ascii=False,
        )
    )
    return 0 if evidence["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
