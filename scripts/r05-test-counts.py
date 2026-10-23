#!/usr/bin/env python3
"""Count distinct passed pytest cases from actual R05 JUnit reports, read-only.

Run pytest separately with --junitxml, copy its reports into R05 evidence, then
pass the default report and explicit PostgreSQL opt-in report(s) here. No tests,
Docker commands, database connections or application mutations occur here.
Repeated case identities are counted once; failures and skips stay visible.
Backend counts use only default and PostgreSQL reports. Supplemental host QA
tests are reported separately and included only in the combined Python count.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/r05/evidence"
MAX_REPORT_BYTES = 16 * 1024 * 1024


def public_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return path.name


def read_report(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size > MAX_REPORT_BYTES:
        raise ValueError("junit_report_unavailable_or_too_large")
    raw = path.read_bytes()
    document = ET.fromstring(raw)
    cases, duplicates = {}, []
    for element in document.iter():
        if element.tag.rsplit("}", 1)[-1] != "testcase":
            continue
        classname, name = element.get("classname"), element.get("name")
        if not classname or not name:
            raise ValueError("junit_case_identity_missing")
        identity = (classname, name)
        tags = {child.tag.rsplit("}", 1)[-1] for child in element}
        outcome = (
            "error"
            if "error" in tags
            else "failed"
            if "failure" in tags
            else "skipped"
            if "skipped" in tags
            else "passed"
        )
        if identity in cases:
            duplicates.append(identity)
            # A repeated passing XML node must never hide a negative result.
            prior = cases[identity]
            outcome = next(
                item
                for item in ("error", "failed", "skipped", "passed")
                if item in {prior, outcome}
            )
        cases[identity] = outcome
    if not cases:
        raise ValueError("junit_report_has_no_test_cases")
    return {
        "path": public_path(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "cases": cases,
        "duplicate_xml_identities": duplicates,
    }


def identifier(identity: tuple[str, str]) -> str:
    return "::".join(identity)


def outcomes(reports: list[dict]) -> dict[tuple[str, str], set[str]]:
    result: dict[tuple[str, str], set[str]] = {}
    for report in reports:
        for identity, outcome in report["cases"].items():
            result.setdefault(identity, set()).add(outcome)
    return result


def summarize(reports: list[dict]) -> dict:
    cases = outcomes(reports)
    failures = {
        identity for identity, values in cases.items() if values & {"failed", "error"}
    }
    passed = {
        identity for identity, values in cases.items() if "passed" in values
    } - failures
    skipped_only = {
        identity for identity, values in cases.items() if values == {"skipped"}
    }
    return {
        "reports": [
            {
                "path": report["path"],
                "sha256": report["sha256"],
                "counts": dict(Counter(report["cases"].values())),
                "duplicate_xml_identities": [
                    identifier(identity)
                    for identity in report["duplicate_xml_identities"]
                ],
            }
            for report in reports
        ],
        "distinct_collected": len(cases),
        "distinct_passed": len(passed),
        "distinct_failed_or_errored": len(failures),
        "distinct_skipped_only": len(skipped_only),
        "passed_case_ids": sorted(map(identifier, passed)),
        "failed_or_errored_case_ids": sorted(map(identifier, failures)),
        "skipped_only_case_ids": sorted(map(identifier, skipped_only)),
    }


def build_report(default: dict, postgres: list[dict], supplemental: list[dict]) -> dict:
    backend_reports = [default, *postgres]
    selected = [*backend_reports, *supplemental]
    backend_summary = summarize(backend_reports)
    backend_summary["scope"] = (
        "Backend application tests: default and PostgreSQL reports only"
    )
    combined = summarize(selected)
    combined["scope"] = (
        "All selected Python tests: backend application and host QA harnesses"
    )
    default_summary = summarize([default])
    postgres_summary = summarize(postgres)
    supplemental_summary = summarize(supplemental)
    supplemental_summary["scope"] = (
        "Supplemental host QA harness tests, separate from backend"
    )
    pg_cases = set(outcomes(postgres))
    default_cases = set(default["cases"])
    pg_absent = pg_cases - default_cases
    browser = {
        identity: values
        for identity, values in outcomes(selected).items()
        if "test_browser_r05_harness" in identity[0].split(".")
    }
    browser_passed = {
        identity
        for identity, values in browser.items()
        if "passed" in values and not values & {"failed", "error"}
    }
    duplicates = any(report["duplicate_xml_identities"] for report in selected)
    valid = (
        combined["distinct_failed_or_errored"] == 0
        and postgres_summary["distinct_skipped_only"] == 0
        and not duplicates
        and not pg_absent
        and bool(browser_passed)
    )
    return {
        "release": "R05",
        "schema": "r05-pytest-counts-v2",
        "recorded_at": datetime.now(UTC).isoformat(),
        "status": "passed" if valid else "failed",
        "read_only_inputs": True,
        "identity": "JUnit (classname, name), including parameterized names",
        "default": default_summary,
        "postgres_opt_in": postgres_summary,
        "supplemental": supplemental_summary,
        "backend": backend_summary,
        "combined": combined,
        "backend_distinct_passed": backend_summary["distinct_passed"],
        "python_distinct_passed": combined["distinct_passed"],
        "qa_harness_distinct_passed": supplemental_summary["distinct_passed"],
        "scope_checks": {
            "postgres_cases_absent_from_default_collection": sorted(
                map(identifier, pg_absent)
            ),
            "duplicate_nodes_inside_any_report": duplicates,
            "default_and_postgres_passing_overlap": sorted(
                set(default_summary["passed_case_ids"])
                & set(postgres_summary["passed_case_ids"])
            ),
            "browser_harness_distinct_passed": len(browser_passed),
            "browser_harness_case_ids": sorted(map(identifier, browser_passed)),
            "supplemental_case_overlap_with_backend": sorted(
                map(
                    identifier,
                    set(outcomes(supplemental)) & set(outcomes(backend_reports)),
                )
            ),
        },
        "notes": [
            "Repeated reports/cases are merged by identity and never added again.",
            "Skipped opt-in cases are not passes; actual PostgreSQL reports supply their results.",
            "Backend totals include only default and PostgreSQL application reports.",
            "Supplemental host QA cases are separate and included only in combined Python totals.",
            "Any selected negative result stays visible and prevents passing this summary.",
            "HTTP, FFmpeg and real-browser counts stay separate and are not added here.",
            "Earlier failed attempts remain separate evidence and are not rewritten by this tool.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--default-junit", type=Path, required=True)
    parser.add_argument("--postgres-junit", type=Path, action="append", required=True)
    parser.add_argument("--supplemental-junit", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(EVIDENCE.resolve()) or output.exists():
        parser.error("--output must be a new file under docs/r05/evidence")
    try:
        report = build_report(
            read_report(args.default_junit),
            [read_report(path) for path in args.postgres_junit],
            [read_report(path) for path in args.supplemental_junit],
        )
    except (OSError, ValueError, ET.ParseError) as error:
        parser.error(
            str(error) if isinstance(error, ValueError) else "junit_report_unreadable"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                "status": report["status"],
                "default_passed": report["default"]["distinct_passed"],
                "postgres_passed": report["postgres_opt_in"]["distinct_passed"],
                "backend_distinct_passed": report["backend_distinct_passed"],
                "python_distinct_passed": report["python_distinct_passed"],
                "qa_harness_distinct_passed": report["qa_harness_distinct_passed"],
                "browser_harness_distinct_passed": report["scope_checks"][
                    "browser_harness_distinct_passed"
                ],
                "evidence": public_path(output),
            }
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
