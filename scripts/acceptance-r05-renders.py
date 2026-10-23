#!/usr/bin/env python3
"""Staged real HTTP R05 QA; no service lifecycle, DB access or canonical mutation.

Root coordinates worker stop/start and safe restart between stages. All uploads
are new synthetic sources. Reports are exclusive: use a new --output on retry.
Run queued with worker stopped; render with worker started; verify after restart.
If render already persisted both completed exports and the newer revision, use
finish to resume cancellation/history without saving or rerendering those exports.
"""

import argparse
import hashlib
import http.client
import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
TERMINAL = {"completed", "failed", "canceled"}


class QAFailure(RuntimeError):
    pass


def checksum(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


class QA:
    def __init__(self, args, report):
        self.args, self.report = args, report
        parsed = urlsplit(args.api)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise QAFailure("only_local_http_is_allowed")
        if (
            parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
        ):
            raise QAFailure("invalid_local_api_url")
        self.host, self.port = parsed.hostname, parsed.port or 8001
        self.seed_path = args.work / "render-http-seed.json"
        self.seed = (
            json.loads(self.seed_path.read_text()) if self.seed_path.exists() else {}
        )

    def check(self, name, condition, **details):
        row = {"check": name, "passed": bool(condition), **details}
        self.report["checks"].append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        if not condition:
            raise QAFailure(name)

    def connection(self):
        return http.client.HTTPConnection(self.host, self.port, timeout=45)

    def request(self, method, path, payload=None, headers=None):
        connection = self.connection()
        try:
            data = json.dumps(payload).encode() if payload is not None else None
            request_headers = dict(headers or {})
            if data is not None:
                request_headers["Content-Type"] = "application/json"
            connection.request(method, path, data, request_headers)
            response = connection.getresponse()
            body = response.read(2 * 1024 * 1024 + 1)
            self.check("response_body_bounded", len(body) <= 2 * 1024 * 1024)
            return (
                response.status,
                {k.lower(): v for k, v in response.getheaders()},
                body,
            )
        finally:
            connection.close()

    def api(self, method, path, payload=None):
        status, headers, content = self.request(method, path, payload)
        self.check("request_id_present", bool(headers.get("x-request-id")))
        body = json.loads(content) if content else None
        if status >= 400:
            self.check(
                "sanitized_error_envelope",
                isinstance(body, dict)
                and set(body) == {"code", "message", "request_id"}
                and headers["x-request-id"] == body["request_id"],
            )
        elif isinstance(body, dict):
            forbidden = {
                "storage_key",
                "claim_token",
                "lease_token",
                "command",
                "database_url",
            }
            self.check("private_fields_absent", not forbidden.intersection(body))
        return status, body

    def base(self):
        return "/api/v1/projects/" + self.seed["project_id"]

    def persist(self):
        temporary = self.seed_path.with_name("seed-" + str(uuid4()) + ".tmp")
        with temporary.open("x") as output:
            temporary.chmod(0o600)
            json.dump(self.seed, output, ensure_ascii=False, indent=2)
            output.write("\n")
        temporary.replace(self.seed_path)

    def upload(self, path, suffix, mime):
        boundary = "EdivoQA" + uuid4().hex
        prefix = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{path.name}"\r\nContent-Type: {mime}\r\n\r\n'
        ).encode()
        ending = f"\r\n--{boundary}--\r\n".encode()
        connection = self.connection()
        try:
            connection.putrequest("POST", self.base() + suffix)
            connection.putheader(
                "Content-Type", f"multipart/form-data; boundary={boundary}"
            )
            connection.putheader(
                "Content-Length", str(len(prefix) + path.stat().st_size + len(ending))
            )
            connection.endheaders()
            connection.send(prefix)
            with path.open("rb") as source:
                while chunk := source.read(65536):
                    connection.send(chunk)
            connection.send(ending)
            response = connection.getresponse()
            body = json.loads(response.read(1024 * 1024))
            self.check(
                "synthetic_upload_" + suffix.removeprefix("/"), response.status == 201
            )
            self.check(
                "upload_sha256_real_bytes",
                body["checksum_sha256"] == checksum(path)
                and body["byte_size"] == path.stat().st_size,
            )
            return body
        finally:
            connection.close()

    def create(self, revision, profile="standard", key=None, expected=202):
        payload = {"edit_spec_revision_id": revision, "profile": profile}
        if key is not None:
            payload["idempotency_key"] = key
        status, job = self.api("POST", self.base() + "/renders", payload)
        self.check(
            "render_create_status", status == expected, expected=expected, actual=status
        )
        return job

    def wait(self, job, wanted="completed"):
        deadline = time.monotonic() + self.args.timeout
        observations, previous_by_attempt = [], {}
        while time.monotonic() < deadline:
            status, current = self.api("GET", job["status_url"])
            self.check("render_status_available", status == 200)
            row = {
                k: current[k]
                for k in (
                    "status",
                    "stage",
                    "attempts",
                    "progress_percent",
                    "progress_encoded_ms",
                    "cancel_requested_at",
                )
            }
            if not observations or row != observations[-1]:
                observations.append(row)
            value = current["progress_encoded_ms"]
            if value is not None:
                old = previous_by_attempt.get(current["attempts"], 0)
                self.check("progress_monotonic_per_attempt", value >= old)
                previous_by_attempt[current["attempts"]] = value
            if current["status"] != "completed":
                self.check(
                    "no_early_100_percent",
                    current["progress_percent"] is None
                    or 0 <= current["progress_percent"] <= 99,
                )
            if current["status"] in TERMINAL:
                self.report["observations"][job["id"]] = observations
                self.check(
                    "render_reached_" + wanted,
                    current["status"] == wanted,
                    render_id=current["id"],
                    error_code=current["error_code"],
                )
                return current
            time.sleep(0.2)
        raise QAFailure("render_poll_timeout")

    def file_checks(self, job, prefix):
        path = job["file_url"]
        size = job["byte_size"]
        status, headers, body = self.request("HEAD", path)
        self.check(
            "head_private_attachment",
            status == 200
            and not body
            and headers.get("content-type") == "video/mp4"
            and int(headers.get("content-length", 0)) == size
            and headers.get("accept-ranges") == "bytes"
            and headers.get("cache-control") == "private, no-store"
            and headers.get("x-content-type-options") == "nosniff"
            and re.fullmatch(
                r'attachment; filename="edivo-r[1-9][0-9]*-'
                r'(?:9x16|1x1|4x5|16x9)-(?:standard|high)\.mp4"',
                headers.get("content-disposition", ""),
            )
            is not None,
        )
        output = self.args.work / f"{prefix}-{job['id']}.mp4"
        connection = self.connection()
        digest, count = hashlib.sha256(), 0
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            self.check("full_download_200", response.status == 200)
            with output.open("xb") as destination:
                output.chmod(0o600)
                while chunk := response.read(65536):
                    destination.write(chunk)
                    digest.update(chunk)
                    count += len(chunk)
        finally:
            connection.close()
        self.check(
            "download_sha256_matches_persisted",
            count == size and digest.hexdigest() == job["checksum_sha256"],
            render_id=job["id"],
            checksum_sha256=digest.hexdigest(),
            byte_size=count,
        )
        intervals = [
            ("bytes=0-31", 0, 31),
            ("bytes=100-130", 100, 130),
            (f"bytes={size - 17}-", size - 17, size - 1),
            ("bytes=-17", size - 17, size - 1),
            ("bytes=0-0", 0, 0),
        ]
        with output.open("rb") as downloaded:
            for value, start, end in intervals:
                status, headers, body = self.request(
                    "GET", path, headers={"Range": value}
                )
                downloaded.seek(start)
                expected = downloaded.read(end - start + 1)
                self.check(
                    "range_exact_bytes",
                    status == 206
                    and body == expected
                    and headers.get("content-range") == f"bytes {start}-{end}/{size}"
                    and int(headers.get("content-length", 0)) == len(expected),
                    range=value,
                )
        status, headers, body = self.request(
            "HEAD", path, headers={"Range": "bytes=0-31"}
        )
        self.check(
            "range_head_without_body",
            status == 206
            and not body
            and headers.get("content-range") == f"bytes 0-31/{size}",
        )
        for value in (f"bytes={size}-", "bytes=0-1,3-4", "bytes=invalid"):
            status, headers, _body = self.request("GET", path, headers={"Range": value})
            self.check(
                "range_416",
                status == 416 and headers.get("content-range") == f"bytes */{size}",
                range=value,
            )
        return {
            "id": job["id"],
            "checksum_sha256": digest.hexdigest(),
            "byte_size": count,
        }

    def history(self):
        items, offset, total = [], 0, None
        while total is None or offset < total:
            status, page = self.api(
                "GET", self.base() + f"/renders?limit=2&offset={offset}"
            )
            self.check(
                "history_page_200",
                status == 200
                and page["limit"] == 2
                and page["offset"] == offset
                and len(page["items"]) <= 2,
            )
            total = page["total"]
            items += page["items"]
            offset += 2
        identities = [(item["created_at"], item["id"]) for item in items]
        self.check(
            "history_deterministic_no_duplicates",
            len(items) == total
            and len({i[1] for i in identities}) == total
            and identities == sorted(identities, reverse=True),
        )
        self.report["history"] = items
        return items

    def queued(self):
        self.check("new_seed_required", not self.seed)
        for name in ("horizontal.mp4", "short.mp3"):
            self.check(
                "synthetic_fixture_present", (self.args.fixtures / name).is_file()
            )
        status, project = self.api(
            "POST",
            "/api/v1/projects",
            {
                "name": "QA R05 export " + str(uuid4()),
                "purpose": "Prova sintética local R05",
            },
        )
        self.check("create_synthetic_project", status == 201)
        self.seed["project_id"] = project["id"]
        self.persist()
        media = self.upload(
            self.args.fixtures / "horizontal.mp4", "/media", "video/mp4"
        )
        audio = self.upload(self.args.fixtures / "short.mp3", "/audio", "audio/mpeg")
        self.seed.update(media=media, audio=audio)
        v1 = {
            "schema_version": 1,
            "source_media_id": media["id"],
            "trim": {"start_ms": 1000, "end_ms": 13000},
            "canvas": {
                "preset": "9:16",
                "fit": "cover",
                "focal_x": 0.25,
                "focal_y": 0.75,
            },
        }
        status, revision1 = self.api(
            "PUT", self.base() + "/edit-spec", {**v1, "expected_revision": 0}
        )
        self.check("save_v1_revision", status == 201)
        v2 = {
            **v1,
            "schema_version": 2,
            "text_overlays": [
                {
                    "id": str(uuid4()),
                    "text": "Ação: 'Edivo' \\ 100%\nPrévia ao vídeo final",
                    "start_ms": 1000,
                    "end_ms": 13000,
                    "position": "bottom",
                    "style": "blue_outline",
                    "size": "medium",
                }
            ],
            "audio": {
                "source_volume": 0.7,
                "background_audio_id": audio["id"],
                "background_volume": 0.15,
                "background_loop": True,
                "fade_in_ms": 1000,
                "fade_out_ms": 1500,
            },
        }
        status, revision2 = self.api(
            "PUT", self.base() + "/edit-spec", {**v2, "expected_revision": 1}
        )
        self.check(
            "save_v2_revision", status == 200 and revision2["schema_version"] == 2
        )
        self.seed.update(
            v1_revision=revision1,
            v2_revision=revision2,
            v2_plan=v2,
            latest_revision=revision2,
        )
        self.persist()
        key = str(uuid4())
        job = self.create(revision2["id"], key=key)
        self.check("worker_stopped_job_really_queued", job["status"] == "queued")
        status, limited = self.api(
            "POST",
            self.base() + "/renders",
            {
                "edit_spec_revision_id": revision2["id"],
                "profile": "high",
                "idempotency_key": str(uuid4()),
            },
        )
        self.check(
            "one_active_render_per_project",
            status == 409 and limited["code"] == "render_active_limit",
        )
        status, _ = self.api("GET", job["status_url"] + "/file")
        self.check("queued_artifact_unavailable", status == 409)
        status, canceled = self.api("POST", job["status_url"] + "/cancel")
        self.check(
            "queued_cancel_atomic", status == 200 and canceled["status"] == "canceled"
        )
        status, again = self.api("POST", job["status_url"] + "/cancel")
        self.check(
            "queued_cancel_idempotent", status == 200 and again["id"] == job["id"]
        )
        status, _ = self.api("GET", job["status_url"] + "/file")
        self.check("canceled_has_no_download", status == 409)
        self.seed["queued_canceled"] = canceled
        self.persist()

    def render(self):
        self.check("queued_stage_seed_present", bool(self.seed.get("queued_canceled")))
        revision = self.seed["v2_revision"]
        key = str(uuid4())
        job = self.create(revision["id"], key=key)
        self.seed.update(standard_job_id=job["id"], standard_key=key)
        self.persist()
        changed = json.loads(json.dumps(self.seed["v2_plan"]))
        changed["canvas"]["preset"] = "4:5"
        changed["text_overlays"][0]["text"] = (
            "Revisão posterior — export anterior preservado"
        )
        status, newer = self.api(
            "PUT", self.base() + "/edit-spec", {**changed, "expected_revision": 2}
        )
        self.check(
            "save_new_revision_while_render_active",
            status == 200
            and newer["revision_number"] == 3
            and job["status"] in {"queued", "running"},
        )
        self.seed.update(latest_revision=newer, latest_plan=changed)
        self.persist()
        completed = self.wait(job)
        self.check(
            "full_saved_v2_twelve_seconds",
            completed["duration_ms"] > 8000
            and abs(completed["duration_ms"] - 12000) <= 35
            and (completed["width"], completed["height"]) == (1080, 1920)
            and completed["fps_num"] == 30
            and completed["fps_den"] == 1
            and completed["revision_id"] == revision["id"]
            and completed["progress_percent"] == 100
            and completed["artifact_available"],
        )
        observed = self.report["observations"][job["id"]]
        self.check(
            "real_encoding_progress_observed",
            any(
                x["stage"] == "encoding"
                and x["progress_encoded_ms"] is not None
                and x["progress_encoded_ms"] > 0
                for x in observed
            ),
        )
        self.report["downloads"].append(self.file_checks(completed, "render"))
        for suffix in ("", "/file"):
            status, _ = self.api(
                "GET", f"/api/v1/projects/{uuid4()}/renders/{completed['id']}" + suffix
            )
            self.check("cross_project_render_access_blocked", status == 404)
        for invalid in (
            {"edit_spec_revision_id": revision["id"], "profile": "arbitrary"},
            {
                "edit_spec_revision_id": revision["id"],
                "profile": "standard",
                "extra": True,
            },
        ):
            status, _ = self.api("POST", self.base() + "/renders", invalid)
            self.check("invalid_render_request_rejected", status == 422)
        replay = self.create(revision["id"], key=key, expected=200)
        self.check("same_key_reuses_completed", replay["id"] == job["id"])
        status, error = self.api(
            "POST",
            self.base() + "/renders",
            {
                "edit_spec_revision_id": revision["id"],
                "profile": "high",
                "idempotency_key": key,
            },
        )
        self.check(
            "idempotency_different_payload_conflict",
            status == 409 and error["code"] == "idempotency_conflict",
        )
        reused = self.create(revision["id"], expected=200)
        self.check(
            "equivalent_without_key_reuses_valid_artifact", reused["id"] == job["id"]
        )
        status, _ = self.api("POST", job["status_url"] + "/cancel")
        self.check("completed_cannot_be_canceled", status == 409)
        self.seed["completed_standard"] = completed
        self.persist()
        v1 = self.wait(self.create(self.seed["v1_revision"]["id"], key=str(uuid4())))
        self.check(
            "v1_full_export_supported",
            v1["revision_number"] == 1
            and v1["revision_id"] == self.seed["v1_revision"]["id"]
            and abs(v1["duration_ms"] - 12000) <= 35,
        )
        self.report["downloads"].append(self.file_checks(v1, "render"))
        self.seed["completed_v1"] = v1
        self.persist()
        self.finish_render_tail(newer)

    def finish(self):
        """Resume the cancellation/history gate from already completed exports."""
        self.check(
            "finish_seed_has_completed_exports_and_saved_revision",
            all(
                isinstance(self.seed.get(field), dict) and self.seed[field].get("id")
                for field in (
                    "queued_canceled",
                    "completed_standard",
                    "completed_v1",
                    "latest_revision",
                )
            ),
        )
        resumed = []
        for field in ("completed_standard", "completed_v1"):
            expected = self.seed[field]
            status, existing = self.api("GET", expected["status_url"])
            self.check(
                "resume_preserves_completed_export",
                status == 200
                and existing == expected
                and existing["status"] == "completed"
                and existing["artifact_available"],
                render_id=expected["id"],
                checksum_sha256=expected["checksum_sha256"],
            )
            resumed.append(existing)
        status, current = self.api("GET", self.base() + "/edit-spec")
        self.check(
            "resume_uses_existing_saved_revision_without_put",
            status == 200 and current == self.seed["latest_revision"],
            revision_id=self.seed["latest_revision"]["id"],
            revision_number=self.seed["latest_revision"]["revision_number"],
        )
        self.report["resumed_completed_exports"] = resumed
        self.finish_render_tail(current)

    def cancel_candidate(self, revision):
        """Keep every previous outcome; resume active work instead of duplicating it."""
        self.report["previous_cancel_candidates"] = list(
            self.seed.get("previous_cancel_candidates", [])
        )
        previous_id = self.seed.get("cancel_running_job_id")
        previous = None
        if previous_id:
            status, previous = self.api("GET", self.base() + "/renders/" + previous_id)
            self.check(
                "previous_cancel_candidate_recovered",
                status == 200 and previous["id"] == previous_id,
            )
            self.report["previous_cancel_candidate_on_resume"] = previous
            if previous["status"] in {"queued", "running", "canceled"}:
                self.check(
                    "resumed_cancel_candidate_matches_saved_revision",
                    previous["revision_id"] == revision["id"]
                    and previous["profile"] == "high",
                )
                return previous
            self.check(
                "previous_terminal_cancel_candidate_retained",
                previous["status"] in {"failed", "completed"},
                render_id=previous["id"],
                status=previous["status"],
                error_code=previous["error_code"],
            )
            candidates = self.seed.setdefault("previous_cancel_candidates", [])
            if not any(item["id"] == previous["id"] for item in candidates):
                candidates.append(previous)
            # Persist the prior terminal identity before requesting any new job.
            # Its failed report and immutable export/history are never rewritten.
            self.persist()
        self.report["previous_cancel_candidates"] = list(
            self.seed.get("previous_cancel_candidates", [])
        )
        high = self.create(revision["id"], profile="high", key=str(uuid4()))
        self.seed["cancel_running_job_id"] = high["id"]
        self.persist()
        return high

    def expected_history(self):
        records = [
            self.seed[field]
            for field in (
                "queued_canceled",
                "completed_standard",
                "completed_v1",
                "running_canceled",
            )
        ]
        records += self.seed.get("previous_cancel_candidates", [])
        return {record["id"]: record for record in records}

    def finish_render_tail(self, revision):
        high = self.cancel_candidate(revision)
        if high["status"] == "canceled":
            # A previous finish can have committed cancellation before the host
            # received its final response. Reuse it, without another render.
            canceled = high
            self.check("resume_existing_cancellation", True, render_id=high["id"])
        elif high["cancel_requested_at"] is not None:
            self.check(
                "resume_existing_cancel_intention",
                high["status"] == "running",
                render_id=high["id"],
                cancel_requested_at=high["cancel_requested_at"],
            )
            canceled = self.wait(high, "canceled")
        else:
            canceled = self.cancel_during_encoding(high)
        self.check(
            "canceled_never_publishes_artifact",
            canceled["file_url"] is None
            and not canceled["artifact_available"]
            and canceled["byte_size"] is None,
        )
        status, _ = self.api("GET", high["status_url"] + "/file")
        self.check("canceled_running_file_unavailable", status == 409)
        self.seed["running_canceled"] = canceled
        self.persist()
        history = self.history()
        expected = self.expected_history()
        self.check(
            "history_preserves_all_expected_jobs",
            len(history) == len(expected)
            and {item["id"] for item in history} == set(expected)
            and all(
                item["status"] == expected[item["id"]]["status"] for item in history
            ),
            expected_count=len(expected),
            expected_ids=sorted(expected),
        )
        previous = self.seed.get("previous_cancel_candidates", [])
        self.report["previous_cancel_candidates"] = list(previous)
        self.check(
            "prior_cancel_candidate_outcomes_remain_in_history",
            all(
                any(
                    item["id"] == candidate["id"]
                    and item["status"] == candidate["status"]
                    and item["error_code"] == candidate["error_code"]
                    for item in history
                )
                for candidate in previous
            ),
            prior_candidates=[
                {
                    key: candidate[key]
                    for key in ("id", "status", "stage", "error_code", "revision_id")
                }
                for candidate in previous
            ],
        )
        canceled_ids = [item["id"] for item in history if item["status"] == "canceled"]
        status, filtered = self.api(
            "GET", self.base() + "/renders?status=canceled&limit=2&offset=0"
        )
        self.check(
            "history_status_filter",
            status == 200
            and filtered["total"] == len(canceled_ids)
            and [item["id"] for item in filtered["items"]] == canceled_ids[:2]
            and all(item["status"] == "canceled" for item in filtered["items"]),
            expected_total=len(canceled_ids),
        )
        self.seed["history_ids"] = [item["id"] for item in history]
        self.persist()

    def cancel_during_encoding(self, high):
        deadline = time.monotonic() + self.args.timeout
        observations = []
        while time.monotonic() < deadline:
            status, running = self.api("GET", high["status_url"])
            self.check("cancel_candidate_available", status == 200)
            row = {
                key: running[key]
                for key in (
                    "id",
                    "status",
                    "stage",
                    "attempts",
                    "progress_encoded_ms",
                    "error_code",
                    "cancel_requested_at",
                )
            }
            if not observations or observations[-1] != row:
                observations.append(row)
                self.report["cancel_candidate_observations"] = observations
            if running["status"] in TERMINAL:
                if (
                    running["status"] == "canceled"
                    and running["cancel_requested_at"] is not None
                ):
                    self.check(
                        "resume_cancel_confirmed_by_worker",
                        True,
                        render_id=running["id"],
                    )
                    return running
                self.report["unexpected_cancel_candidate_terminal"] = running
                raise QAFailure(
                    "high_render_finished_before_running_cancel_was_observed"
                )
            if (
                running["status"] == "running"
                and running["stage"] == "encoding"
                and (running["progress_encoded_ms"] or 0) > 0
            ):
                break
            time.sleep(0.05)
        else:
            raise QAFailure("running_encoding_not_observed")
        status, intention = self.api("POST", high["status_url"] + "/cancel")
        self.check(
            "running_cancel_intention_202",
            status == 202 and intention["cancel_requested_at"] is not None,
        )
        return self.wait(high, "canceled")

    def verify(self):
        self.check("render_stage_seed_present", bool(self.seed.get("history_ids")))
        status, revision = self.api("GET", self.base() + "/edit-spec")
        self.check(
            "revision_persisted_after_restart",
            status == 200 and revision == self.seed["latest_revision"],
        )
        history = self.history()
        self.check(
            "history_persisted_after_restart",
            [r["id"] for r in history] == self.seed["history_ids"],
        )
        for expected in self.seed.get("previous_cancel_candidates", []):
            actual = next(
                (item for item in history if item["id"] == expected["id"]), None
            )
            self.check(
                "prior_cancel_candidate_state_persisted",
                actual is not None
                and actual["status"] == expected["status"]
                and actual["error_code"] == expected["error_code"]
                and actual["revision_id"] == expected["revision_id"],
                render_id=expected["id"],
                status=expected["status"],
                error_code=expected["error_code"],
            )
        for field in ("completed_standard", "completed_v1"):
            expected = self.seed[field]
            status, job = self.api("GET", expected["status_url"])
            self.check(
                "completed_metadata_persisted", status == 200 and job == expected
            )
            self.report["downloads"].append(self.file_checks(job, "verify"))
        for field in ("queued_canceled", "running_canceled"):
            expected = self.seed[field]
            status, job = self.api("GET", expected["status_url"])
            self.check(
                "canceled_state_persisted",
                status == 200
                and job["status"] == "canceled"
                and not job["artifact_available"],
            )
        for suffix, asset in (
            ("media", self.seed["media"]),
            ("audio", self.seed["audio"]),
        ):
            status, metadata = self.api(
                "GET", self.base() + "/" + suffix + "/" + asset["id"]
            )
            self.check(
                "original_metadata_sha_preserved",
                status == 200
                and metadata["checksum_sha256"] == asset["checksum_sha256"]
                and metadata["byte_size"] == asset["byte_size"],
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage",
        choices=("queued", "render", "finish", "verify"),
        required=True,
        help=(
            "queued prepara o projeto; render exporta v2/v1; finish retoma apenas "
            "cancelamento/histórico com completed_standard/completed_v1 já no seed; "
            "verify consulta a persistência após reinício"
        ),
    )
    parser.add_argument("--api", default="http://127.0.0.1:8001")
    parser.add_argument(
        "--fixtures", type=Path, default=Path("/tmp/edivo-r05/fixtures")
    )
    parser.add_argument("--work", type=Path, default=Path("/tmp/edivo-r05/http"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 3600:
        parser.error("timeout must be between1 and3600 seconds")
    args.work.mkdir(mode=0o700, parents=True, exist_ok=True)
    output = args.output or ROOT / f"docs/r05/evidence/render-http-{args.stage}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    report = {
        "status": "failed",
        "stage": args.stage,
        "created_at": datetime.now(UTC).isoformat(),
        "checks": [],
        "observations": {},
        "downloads": [],
        "browser_used": False,
        "service_lifecycle_performed": False,
        "direct_database_access": False,
        "human_auditory_inspection_performed": False,
    }
    started = time.monotonic()
    try:
        qa = QA(args, report)
        getattr(qa, args.stage)()
        report["project_id"] = qa.seed["project_id"]
        report["status"] = "passed"
    except (
        QAFailure,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        http.client.HTTPException,
    ) as error:
        report["error_type"] = type(error).__name__
        if isinstance(error, QAFailure):
            report["failure"] = str(error)
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "failure": report.get("failure"),
                }
            ),
            flush=True,
        )
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        with os.fdopen(descriptor, "w") as target:
            json.dump(report, target, ensure_ascii=False, indent=2)
            target.write("\n")
    print(
        json.dumps(
            {
                "status": report["status"],
                "stage": args.stage,
                "checks": len(report["checks"]),
                "seconds": report["elapsed_seconds"],
            }
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
