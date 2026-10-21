"""Run R04 browser QA in the isolated official Playwright 1.58.2 helper image.

Requires an external npm install, scripts/browser-r04.mjs and synthetic fixtures
under WORK/fixtures. Only the QA directory and read-only QA dependencies are
mounted. Reports and screenshots are copied exclusively to docs/r04/evidence.
Existing application services, media and historical evidence are preserved.
"""

import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "mcr.microsoft.com/playwright:v1.58.2-noble"


def command(args: list[str]) -> str:
    return subprocess.run(
        args, cwd=ROOT, check=True, capture_output=True, text=True, timeout=30
    ).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage", choices=("composition", "preview", "verify"), default="composition"
    )
    parser.add_argument("--work", type=Path, default=Path("/tmp/edivo-r04"))
    parser.add_argument(
        "--qa-node-modules", type=Path, default=Path("/tmp/edivo-browser/node_modules")
    )
    parser.add_argument(
        "--project-id",
        type=UUID,
        help="Recover a retained synthetic QA project by GET if its temporary seed was lost.",
    )
    options = parser.parse_args()
    work = options.work.resolve()
    dependencies = options.qa_node_modules.resolve()
    source = ROOT / "scripts" / "browser-r04.mjs"
    if not source.is_file():
        parser.error("O roteiro scripts/browser-r04.mjs precisa existir antes da execução.")
    if not (dependencies / "playwright" / "index.mjs").is_file():
        parser.error("Instale Playwright 1.58.2 no diretório auxiliar de QA indicado.")
    package = json.loads((dependencies / "playwright" / "package.json").read_text())
    if package.get("version") != "1.58.2":
        parser.error("A dependência auxiliar precisa usar Playwright 1.58.2, igual à imagem.")
    for name in ("horizontal.mp4", "music-short.mp3"):
        if not (work / "fixtures" / name).is_file():
            parser.error("As amostras sintéticas FFmpeg precisam existir em WORK/fixtures.")
    work.mkdir(mode=0o700, parents=True, exist_ok=True)
    retained_seed = ROOT / "docs" / "r04" / "evidence" / "browser-seed.json"
    if options.project_id and not (work / "seed-r04.json").exists() and retained_seed.is_file():
        saved = json.loads(retained_seed.read_text())
        if saved.get("project", {}).get("id") == str(options.project_id) and saved.get(
            "project", {}
        ).get("name", "").startswith("QA R04 "):
            shutil.copyfile(retained_seed, work / "seed-r04.json")
    artifacts = work / "artifacts"
    artifacts.mkdir(mode=0o700, exist_ok=True)
    shutil.copyfile(source, work / source.name)

    frontend = command(["docker", "compose", "ps", "--quiet", "frontend"])
    if not frontend:
        parser.error("O frontend Edivo precisa estar em execução.")
    run_id = f"r04-{options.stage}-{time.time_ns()}"
    try:
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                run_id,
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--network",
                f"container:{frontend}",
                "--shm-size",
                "256m",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--volume",
                f"{work}:/work",
                "--volume",
                f"{dependencies}:/work/node_modules:ro",
                "--workdir",
                "/work",
                "--env",
                "EDIVO_BROWSER_BASE_URL=http://127.0.0.1:5173",
                "--env",
                f"EDIVO_BROWSER_RUN_ID={run_id}",
                "--env",
                f"EDIVO_BROWSER_STAGE={options.stage}",
                "--env",
                f"EDIVO_BROWSER_PROJECT_ID={options.project_id or ''}",
                "--env",
                "EDIVO_PLAYWRIGHT_MODULE=/work/node_modules/playwright/index.mjs",
                IMAGE,
                "node",
                source.name,
            ],
            cwd=ROOT,
            check=False,
            timeout=600,
        )
        returncode = result.returncode
    except subprocess.TimeoutExpired:
        # Stop only this invocation's disposable helper, never an app service.
        subprocess.run(
            ["docker", "stop", "--time", "5", run_id],
            cwd=ROOT,
            check=False,
            capture_output=True,
            timeout=30,
        )
        print(json.dumps({"status": "failed", "code": "browser_qa_timeout"}))
        returncode = 124

    evidence = ROOT / "docs" / "r04" / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    report = artifacts / f"{run_id}.json"
    if report.is_file():
        shutil.copyfile(report, evidence / report.name)
        if returncode == 0:
            shutil.copyfile(report, evidence / f"browser-{options.stage}.json")
    for screenshot in artifacts.glob(f"{run_id}-*.png"):
        shutil.copyfile(screenshot, evidence / screenshot.name)
    seed_path = work / "seed-r04.json"
    if returncode == 0 and seed_path.is_file():
        # The seed contains only API metadata for this synthetic QA project.
        # Retaining it lets later gates recover the same revisions after /tmp loss.
        shutil.copyfile(seed_path, evidence / "browser-seed.json")
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
