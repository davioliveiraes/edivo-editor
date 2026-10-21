"""Trusted POSIX resource launcher; arguments originate only from backend code."""

import json
import os
import resource
import sys


def main() -> int:
    try:
        limits = json.loads(sys.argv[1])
        memory, cpu, file_size, open_files = (
            limits["memory_bytes"],
            limits["cpu_seconds"],
            limits["file_size_bytes"],
            limits["open_files"],
        )
        if any(
            type(value) is not int or value <= 0 for value in (memory, cpu, file_size, open_files)
        ):
            return 126
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_size, file_size))
        resource.setrlimit(resource.RLIMIT_NOFILE, (open_files, open_files))
        command = sys.argv[2:]
        if not command:
            return 126
        os.execvp(command[0], command)
    except (KeyError, ValueError, TypeError, OSError, IndexError):
        # No arguments, subprocess errors or local paths enter application logs.
        return 126
    return 126


if __name__ == "__main__":
    raise SystemExit(main())
