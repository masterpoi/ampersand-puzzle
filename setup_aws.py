"""
setup_aws.py
------------
One-time setup: configure the AWS CLI profile used for Scaleway S3 uploads
by reading credentials directly from the scw (Scaleway) CLI.

Requires:
  - scw CLI installed and logged in  (https://www.scaleway.com/en/cli/)
  - aws CLI installed                (https://aws.amazon.com/cli/)

Usage:
  python setup_aws.py
  python orchestrator.py setup      # same thing, via the orchestrator
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import AWS_PROFILE, SCALEWAY_ENDPOINT, SCALEWAY_REGION, SCALEWAY_BUCKET


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _require(cmd: list[str], label: str) -> str:
    """Run cmd, return stdout.strip(), or exit with a helpful message."""
    r = _run(cmd)
    val = r.stdout.strip()
    if r.returncode != 0 or not val:
        print(f"ERROR: could not read {label}")
        print(f"  command : {' '.join(cmd)}")
        print(f"  stderr  : {r.stderr.strip()}")
        sys.exit(1)
    return val


def main() -> None:
    # ── 1. Check scw CLI ───────────────────────────────────────────────────────
    r = _run(["scw", "version"])
    if r.returncode != 0:
        print("ERROR: scw CLI not found.")
        print("  Install: https://www.scaleway.com/en/cli/")
        print("  Then run: scw init")
        sys.exit(1)
    version_line = r.stdout.splitlines()[0] if r.stdout else "scw (unknown version)"
    print(f"scw CLI : {version_line}")

    # ── 2. Read Scaleway credentials ───────────────────────────────────────────
    access_key = _require(["scw", "config", "get", "access-key"], "access-key")
    secret_key = _require(["scw", "config", "get", "secret-key"], "secret-key")

    print(f"  access-key : {access_key[:8]}...")
    print(f"  secret-key : {secret_key[:4]}{'*' * (len(secret_key) - 4)}")

    # ── 3. Write to AWS CLI profile ────────────────────────────────────────────
    settings = [
        ("aws_access_key_id",     access_key),
        ("aws_secret_access_key", secret_key),
        ("region",                SCALEWAY_REGION),
        ("output",                "json"),
    ]
    for key, val in settings:
        r = _run(["aws", "configure", "set", key, val, "--profile", AWS_PROFILE])
        if r.returncode != 0:
            print(f"ERROR: aws configure set {key} failed: {r.stderr.strip()}")
            sys.exit(1)

    print(f"\nAWS CLI profile '{AWS_PROFILE}' configured.")

    # ── 4. Verify by listing the bucket ───────────────────────────────────────
    print(f"Verifying access to s3://{SCALEWAY_BUCKET}/ ...")
    r = _run([
        "aws", "s3api", "list-objects",
        "--endpoint-url", SCALEWAY_ENDPOINT,
        "--profile", AWS_PROFILE,
        "--bucket", SCALEWAY_BUCKET,
        "--max-items", "1",
    ])
    if r.returncode == 0:
        print("Verification: OK")
        print(f"\nReady. Run:  python orchestrator.py upload")
    else:
        print(f"Verification failed: {r.stderr.strip()}")
        print("Check that your Scaleway API key has S3 read/write permissions.")
        sys.exit(1)


if __name__ == "__main__":
    main()
