"""Repository QA helper checks, executed on the host outside the backend image."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/browser-r05.py"
spec = importlib.util.spec_from_file_location("browser_r05_qa", SCRIPT)
assert spec and spec.loader
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


def arguments(**updates):
    payload = {
        "frontend": "a" * 64,
        "work": Path("/tmp/edivo-r05/browser"),
        "fixtures": Path("/tmp/edivo-r05/fixtures"),
        "dependencies": Path("/tmp/edivo-browser/node_modules"),
        "run_id": "r05-export-123456",
        "stage": "export",
        "base": "http://127.0.0.1:5173",
    }
    return harness.docker_arguments(**(payload | updates))


def test_helper_mounts_only_qa_directories_and_never_private_application_data():
    argv = arguments()
    mounts = [argv[index + 1] for index, item in enumerate(argv) if item == "--volume"]
    assert mounts == [
        "/tmp/edivo-r05/browser:/work",
        "/tmp/edivo-browser/node_modules:/work/node_modules:ro",
        "/tmp/edivo-r05/fixtures:/fixtures:ro",
    ]
    assert argv[:3] == ["docker", "run", "--rm"]
    assert argv[argv.index("--network") + 1] == "container:" + "a" * 64
    assert "--privileged" not in argv and "--publish" not in argv
    assert "docker.sock" not in str(argv) and "DATABASE_URL" not in str(argv)
    assert "--cap-drop" in argv and "no-new-privileges:true" in argv
    assert argv[-3:] == [harness.IMAGE, "node", "browser-r05.mjs"]


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:5173",
        "http://example.com",
        "http://u:p@localhost:5173",
        "http://127.0.0.1:5173/private",
        "http://127.0.0.1:5173/?token=secret",
        "http://127.0.0.1:5173/#secret",
    ],
)
def test_helper_rejects_nonlocal_credentials_paths_and_query(url):
    with pytest.raises(ValueError):
        arguments(base=url)


def test_evidence_is_exclusive_and_cannot_target_historical_reports(
    tmp_path, monkeypatch
):
    evidence = tmp_path / "docs/r05/evidence"
    evidence.mkdir(parents=True)
    monkeypatch.setattr(harness, "EVIDENCE", evidence)
    destination = harness.evidence_directory(evidence / "browser-attempt-1")
    assert destination.is_dir()
    assert destination.stat().st_mode & 0o777 == 0o700
    with pytest.raises(ValueError):
        harness.evidence_directory(destination)
    with pytest.raises(ValueError):
        harness.evidence_directory(tmp_path / "docs/r04/evidence/browser-attempt-1")


def test_playwright_helper_version_must_match_official_browser_image(tmp_path):
    directory = tmp_path / "playwright"
    directory.mkdir()
    (directory / "index.mjs").write_text("export const synthetic = true")
    (directory / "package.json").write_text(json.dumps({"version": "1.58.2"}))
    harness.validate_dependencies(tmp_path)
    (directory / "package.json").write_text(json.dumps({"version": "1.60.0"}))
    with pytest.raises(ValueError):
        harness.validate_dependencies(tmp_path)
    with pytest.raises(ValueError):
        harness.validate_dependencies(tmp_path / "absent")
