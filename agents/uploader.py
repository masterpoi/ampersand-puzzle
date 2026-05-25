from __future__ import annotations

"""
Uploader
─────────
Uploads changed files to the Scaleway ampersand-puzzle S3 bucket.

The upload_paths in the plan are relative to BASE_DIR (project root).
Each file is uploaded to the same relative key in the bucket, e.g.:
  stl/piece_5.stl  ->  s3://ampersand-puzzle/stl/piece_5.stl

Uploads also go to stl/print/ if the print-ready copy exists.

Content-Type is set per extension so the website serves correctly.

Auth resolution order (first match wins):
  1. Named AWS CLI profile (default: "scaleway", override via SCW_PROFILE env var)
  2. AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY environment variables
  If neither is available, a clear setup error is raised.
"""

import configparser
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    BASE_DIR, STL_DIR, PRINT_DIR,
    SCALEWAY_ENDPOINT, SCALEWAY_BUCKET, SCALEWAY_REGION, AWS_PROFILE,
)

WEBSITE_URL = f"http://{SCALEWAY_BUCKET}.s3-website.{SCALEWAY_REGION}.scw.cloud"

CONTENT_TYPES = {
    ".html": "text/html",
    ".js":   "application/javascript",
    ".css":  "text/css",
    ".stl":  "application/octet-stream",
    ".json": "application/json",
    ".txt":  "text/plain",
}


# ── Auth helpers ───────────────────────────────────────────────────────────────

def _profile_exists(profile: str) -> bool:
    """Return True if the named AWS CLI profile is configured on this machine."""
    for p in [Path.home() / ".aws" / "credentials",
              Path.home() / ".aws" / "config"]:
        if p.exists():
            cfg = configparser.ConfigParser()
            cfg.read(p)
            if profile in cfg or f"profile {profile}" in cfg:
                return True
    return False


def _auth_args() -> list[str]:
    """
    Return the AWS CLI args needed for authentication.
    Uses the named profile when available; falls back to environment variables.
    If neither is configured, auto-runs setup_aws.py (reads from scw CLI).
    """
    if _profile_exists(AWS_PROFILE):
        return ["--profile", AWS_PROFILE]

    if os.environ.get("AWS_ACCESS_KEY_ID"):
        return ["--region", SCALEWAY_REGION]

    # Profile missing and no env vars — auto-configure from scw CLI
    print(f"[uploader] AWS profile '{AWS_PROFILE}' not found — running auto-setup from scw CLI...",
          file=sys.stderr)
    sys.path.insert(0, str(BASE_DIR))
    import setup_aws
    setup_aws.main()

    # After setup, the profile should exist now
    if _profile_exists(AWS_PROFILE):
        return ["--profile", AWS_PROFILE]

    raise RuntimeError(
        f"Auto-setup ran but profile '{AWS_PROFILE}' still not found.\n"
        f"Run manually: python setup_aws.py"
    )


# ── Core upload ────────────────────────────────────────────────────────────────

def _aws(args: list[str], verbose: bool) -> tuple[bool, str]:
    """Run an AWS CLI command against the Scaleway endpoint."""
    cmd = ["aws", "s3api", "--endpoint-url", SCALEWAY_ENDPOINT] + _auth_args() + args

    if verbose:
        print(f"[uploader] {' '.join(cmd[:10])}...", file=sys.stderr)

    result = subprocess.run(cmd, capture_output=True, text=True)
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


def _upload_file(local_path: Path, s3_key: str, verbose: bool) -> bool:
    """Upload a single file using aws s3api put-object."""
    if not local_path.exists():
        print(f"[uploader] SKIP (missing): {local_path}", file=sys.stderr)
        return False

    content_type = CONTENT_TYPES.get(local_path.suffix.lower(), "application/octet-stream")

    ok, out = _aws(
        [
            "put-object",
            "--bucket", SCALEWAY_BUCKET,
            "--key",    s3_key,
            "--body",   str(local_path),
            "--content-type", content_type,
        ],
        verbose,
    )

    if ok:
        print(f"[uploader]  ok  {s3_key}", file=sys.stderr)
    else:
        print(f"[uploader]  ERR {s3_key}  ->  {out}", file=sys.stderr)

    return ok


def run(upload_paths: list[str], verbose: bool = False) -> dict:
    """
    Upload each path (relative to BASE_DIR) to Scaleway.
    Also uploads print/ copies of any stl/piece_N.stl files.
    Returns {"ok": [...], "errors": [...]}
    """
    if not upload_paths:
        print("[uploader] Nothing to upload.", file=sys.stderr)
        return {"ok": [], "errors": []}

    ok_list:  list[str] = []
    err_list: list[str] = []

    # Deduplicate and expand print/ copies
    all_uploads: list[tuple[Path, str]] = []  # (local_path, s3_key)
    seen: set[str] = set()

    for rel_path in upload_paths:
        local = BASE_DIR / rel_path
        key   = rel_path.replace("\\", "/")  # S3 uses forward slashes
        if key not in seen:
            all_uploads.append((local, key))
            seen.add(key)

        # If it's a piece STL, also upload the print-ready copy
        if rel_path.startswith("stl/piece_") and rel_path.endswith(".stl"):
            filename    = Path(rel_path).name
            print_local = PRINT_DIR / filename
            print_key   = f"stl/print/{filename}"
            if print_key not in seen:
                all_uploads.append((print_local, print_key))
                seen.add(print_key)

    print(f"[uploader] Uploading {len(all_uploads)} file(s) to s3://{SCALEWAY_BUCKET}/",
          file=sys.stderr)

    for local_path, s3_key in all_uploads:
        if _upload_file(local_path, s3_key, verbose):
            ok_list.append(s3_key)
        else:
            err_list.append(s3_key)

    print(
        f"[uploader] Done: {len(ok_list)}/{len(all_uploads)} uploaded."
        + (f"  Errors: {len(err_list)}" if err_list else ""),
        file=sys.stderr,
    )

    if ok_list:
        print(f"[uploader] Site -> {WEBSITE_URL}", file=sys.stderr)

    return {"ok": ok_list, "errors": err_list}


def run_all(verbose: bool = False) -> dict:
    """
    Upload every piece STL in stl/ (plus print/ copies) and index.html.
    Use this to recover from a failed upload or to do a full re-sync.
    """
    stl_paths = sorted(STL_DIR.glob("piece_*.stl"))
    rel_stl   = [f"stl/{p.name}" for p in stl_paths]

    all_paths = rel_stl + ["index.html"]
    print(f"[uploader] Full re-sync: {len(stl_paths)} STLs + index.html", file=sys.stderr)
    return run(all_paths, verbose=verbose)


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Uploader — uploads files to Scaleway S3.")
    parser.add_argument(
        "paths",
        nargs="*",
        help="Relative paths to upload (e.g. stl/piece_5.stl index.html).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Upload all STLs + index.html (full re-sync).",
    )
    parser.add_argument("--plan", help="Path to plan JSON (uses upload_paths field).")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if args.all:
        result = run_all(verbose=args.verbose)
    elif args.plan:
        plan   = json.loads(Path(args.plan).read_text())
        result = run(plan.get("upload_paths", []), verbose=args.verbose)
    elif args.paths:
        result = run(args.paths, verbose=args.verbose)
    else:
        plan   = json.loads(sys.stdin.read())
        result = run(plan.get("upload_paths", []), verbose=args.verbose)

    print(json.dumps(result, indent=2))
