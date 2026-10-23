"""Isolated Chromium R05 QA, with exclusive evidence and no app lifecycle actions.

Uses installed Playwright1.58.2 dependencies and its matching official image.
The disposable helper shares only Edivo frontend networking; it never mounts the
Docker socket, application storage, home directories or environment secrets.
"""

import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/r05/evidence"
IMAGE = "mcr.microsoft.com/playwright:v1.58.2-noble"


def validate_dependencies(path):
    if not (path / "playwright/index.mjs").is_file():
        raise ValueError("Playwright auxiliar não encontrado.")
    package = json.loads((path / "playwright/package.json").read_text())
    if package.get("version") != "1.58.2":
        raise ValueError("Use Playwright1.58.2, correspondente à imagem de QA.")


def evidence_directory(path):
    destination = path.resolve()
    if destination.parent != EVIDENCE.resolve() or destination.exists():
        raise ValueError(
            "--output deve ser um diretório novo diretamente em docs/r05/evidence."
        )
    destination.mkdir(mode=0o700)
    return destination


def docker_arguments(*, frontend, work, fixtures, dependencies, run_id, stage, base):
    parsed = urlsplit(base)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError(
            "O navegador QA exige URL HTTP local, sem credenciais ou path."
        )
    return [
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
        "--cpus",
        "2",
        "--memory",
        "1536m",
        "--pids-limit",
        "256",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--volume",
        f"{work}:/work",
        "--volume",
        f"{dependencies}:/work/node_modules:ro",
        "--volume",
        f"{fixtures}:/fixtures:ro",
        "--workdir",
        "/work",
        "--env",
        f"EDIVO_BROWSER_BASE_URL={base}",
        "--env",
        f"EDIVO_BROWSER_RUN_ID={run_id}",
        "--env",
        f"EDIVO_BROWSER_STAGE={stage}",
        "--env",
        "EDIVO_BROWSER_FIXTURES=/fixtures",
        "--env",
        "EDIVO_PLAYWRIGHT_MODULE=/work/node_modules/playwright/index.mjs",
        IMAGE,
        "node",
        "browser-r05.mjs",
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("export", "verify"), default="export")
    parser.add_argument("--work", type=Path, default=Path("/tmp/edivo-r05/browser"))
    parser.add_argument(
        "--fixtures", type=Path, default=Path("/tmp/edivo-r05/fixtures")
    )
    parser.add_argument(
        "--qa-node-modules", type=Path, default=Path("/tmp/edivo-browser/node_modules")
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Diretório novo docs/r05/evidence/browser-attempt-N.",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:5173",
        help="URL interna no namespace de rede do frontend Edivo.",
    )
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    work, fixtures, dependencies = (
        args.work.resolve(),
        args.fixtures.resolve(),
        args.qa_node_modules.resolve(),
    )
    try:
        validate_dependencies(dependencies)
        for name in ("horizontal.mp4", "short.mp3"):
            if not (fixtures / name).is_file():
                raise ValueError(
                    "Amostras sintéticas horizontal.mp4/short.mp3 não encontradas."
                )
        if not 30 <= args.timeout <= 1800:
            raise ValueError("Use timeout entre30 e1800 segundos.")
        output = evidence_directory(args.output)
        work.mkdir(mode=0o700, parents=True, exist_ok=True)
        (work / "artifacts").mkdir(mode=0o700, exist_ok=True)
        shutil.copyfile(ROOT / "scripts/browser-r05.mjs", work / "browser-r05.mjs")
        frontend = subprocess.run(
            ["docker", "compose", "ps", "--quiet", "frontend"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        if not frontend:
            raise ValueError("O frontend Edivo precisa estar em execução.")
        run_id = f"r05-{args.stage}-{time.time_ns()}"
        command = docker_arguments(
            frontend=frontend,
            work=work,
            fixtures=fixtures,
            dependencies=dependencies,
            run_id=run_id,
            stage=args.stage,
            base=args.base_url,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.error(
            str(error)
            if isinstance(error, ValueError)
            else "Preparo do QA indisponível."
        )
    returncode = 1
    with (
        (output / "stdout.log").open("x") as stdout,
        (output / "stderr.log").open("x") as stderr,
    ):
        try:
            returncode = subprocess.run(
                command,
                cwd=ROOT,
                check=False,
                stdout=stdout,
                stderr=stderr,
                timeout=args.timeout,
            ).returncode
        except subprocess.TimeoutExpired:
            # Stop only this invocation's disposable helper, never an app service.
            subprocess.run(
                ["docker", "stop", "--time", "5", run_id],
                cwd=ROOT,
                check=False,
                capture_output=True,
                timeout=30,
            )
            returncode = 124
        except OSError:
            stderr.write("browser_helper_unavailable\n")
    for artifact in (work / "artifacts").glob(f"{run_id}*"):
        if artifact.is_file():
            shutil.copyfile(artifact, output / artifact.name)
    if (work / "seed-r05.json").is_file():
        shutil.copyfile(work / "seed-r05.json", output / "seed-r05.json")
    summary = {
        "run_id": run_id,
        "stage": args.stage,
        "exit_code": returncode,
        "status": "passed" if returncode == 0 else "failed",
        "application_lifecycle_performed": False,
        "historical_evidence_modified": False,
    }
    (output / "invocation.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary))
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
