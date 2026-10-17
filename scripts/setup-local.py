#!/usr/bin/env python3
"""Prepare configuração e storage locais sem substituir arquivos existentes."""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    env_file = root / ".env"
    if env_file.exists():
        print(".env existente preservado; confira as variáveis em .env.example.")
    else:
        password = secrets.token_hex(32)
        lines = [
            "# Configuração local gerada; não versionar.",
            "EDIVO_ENV=development",
            "EDIVO_LOG_LEVEL=INFO",
            "EDIVO_API_PORT=8000",
            "EDIVO_FRONTEND_PORT=5173",
            f"EDIVO_UID={os.getuid()}",
            f"EDIVO_GID={os.getgid()}",
            "POSTGRES_USER=edivo",
            "POSTGRES_DB=edivo",
            f"POSTGRES_PASSWORD={password}",
            'EDIVO_CORS_ORIGINS=\'["http://127.0.0.1:5173","http://localhost:5173"]\'',
            f"EDIVO_DATABASE_URL=postgresql+psycopg://edivo:{password}@db:5432/edivo",
            "EDIVO_STORAGE_ROOT=../storage",
            "EDIVO_FFMPEG_BIN=ffmpeg",
            "EDIVO_FFPROBE_BIN=ffprobe",
            "EDIVO_DATABASE_CONNECT_TIMEOUT=2",
            "EDIVO_FFMPEG_TIMEOUT_SECONDS=5",
            "EDIVO_CAPABILITIES_CACHE_SECONDS=300",
            "",
        ]
        try:
            descriptor = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            print(".env criado por outro processo; arquivo preservado.")
        else:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write("\n".join(lines))
            print(".env criado com permissões 0600 e senha local aleatória.")

    storage = root / "storage"
    for directory in (
        storage,
        *(storage / part for part in ("originals", "previews", "exports", "workspaces", "temp")),
    ):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not os.access(directory, os.R_OK | os.W_OK | os.X_OK):
            print("Storage sem acesso para o usuário atual; confira UID/GID e proprietário.")
            return 1
    print("Storage preparado; arquivos e permissões existentes foram preservados.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
