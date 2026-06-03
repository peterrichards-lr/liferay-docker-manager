#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml


def validate_yaml(file_path: Path) -> bool:
    try:
        with file_path.open("r", encoding="utf-8") as f:
            yaml.safe_load(f)
        print(f"✅ YAML Syntax OK: {file_path}")
        return True
    except Exception as e:
        print(f"❌ YAML Syntax Error in {file_path}: {e}", file=sys.stderr)
        return False


def validate_docker_compose(file_path: Path) -> bool:
    cmd = None
    if shutil.which("docker"):
        try:
            res = subprocess.run(
                ["docker", "compose", "version"],
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                cmd = ["docker", "compose"]
        except Exception:
            pass

    if not cmd and shutil.which("docker-compose"):
        cmd = ["docker-compose"]

    if not cmd:
        print(
            "⚠️  Warning: Neither 'docker compose' nor 'docker-compose' found on PATH. Skipping schema validation.",
            file=sys.stderr,
        )
        return True

    try:
        env = dict(os.environ)
        if "LDM_CERTS_DIR" not in env or not env["LDM_CERTS_DIR"]:
            env["LDM_CERTS_DIR"] = "/tmp"

        res = subprocess.run(
            [*cmd, "-f", str(file_path), "config"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            env=env,
        )
        if res.returncode == 0:
            print(f"✅ Docker Compose Schema OK: {file_path}")
            return True
        print(
            f"❌ Docker Compose Schema Error in {file_path}:\n{res.stderr.strip()}",
            file=sys.stderr,
        )
        return False
    except Exception as e:
        print(f"❌ Error running Docker Compose validation: {e}", file=sys.stderr)
        return False


def main():
    root = Path(__file__).resolve().parent.parent
    file_path = root / "ldm_core" / "resources" / "infra-compose.yml"

    if not file_path.exists():
        print(f"❌ Error: {file_path} not found!", file=sys.stderr)
        sys.exit(1)

    success = True
    if not validate_yaml(file_path):
        success = False

    if success and not validate_docker_compose(file_path):
        success = False

    if not success:
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
