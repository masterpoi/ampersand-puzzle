from __future__ import annotations

"""
Uploader
─────────
Uploads changed files to the Scaleway ampersand-puzzle S3 bucket.

The upload_paths in the plan are relative to BASE_DIR (project root).
Each file is uploaded to the same relative key in the bucket, e.g.:
  stl/piece_5.stl  →  s3://ampersand-puzzle/stl/piece_5.stl

Uploads also go to stl/print/ if the print-ready copy exists.

Content-Type is set per extension so the website serves correctly.
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    BASE_DIR, STL_DIR, PRINT_DIR,
    SCALEWAY_ENDPOINT, SCALEWAY_BUCKET, AWS_PROFILE,
)

CONTENT_TYPES = {
    ".html": "text/html",
    ".js":   "application/javascript",
    ".css":  "text/css",
    ".stl":  "application/octet-stream",
    ".json": "application/json",
    ".txt":  "text/plain",
}


def _aws(args: list[str], verbose: bool) -> tuple[bool, str]:
    """Run an AWS CLI command against the Scaleway endpoint."""
    cmd = [
        "aws", "s3api",
        "--endpoint-url", SCALEWAY_ENDPOINT,
        "--profile", AWS_PROFILE,
    ] + args

    if verbose:
        print(f"[uploader] {' '.join(cmd[:8])}…", file=sys.stderr)

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
        print(f"[uploader]  ✓ {s3_key}", file=sys.stderr)
    else:
        print(f"[uploader]  ✗ {s3_key}  →  {out}", file=sys.stderr)

    return ok


def run(upload_paths: list[str], verbose: bool = False) -> dict:
    """
    Upload each path (relative to BASE_DIR) to Scaleway.
    Also uploads print/ copies of any STL files.
    Returns {"ok": [...], "errors": [...]}
    """
    if not upload_paths:
        print("[uploader] Nothing to upload.", file=sys.stderr)
        return {"ok": [], "errors": []}

    ok_list:  list[str] = []
    err_list: list[str] = []

    # Deduplicate and expand print/ copies
    all_uploads: list[tuple[Path, str]] = []  # (local_path, s3_key)
    seen = set()

    for rel_path in upload_paths:
        local = BASE_DIR / rel_path
        key   = rel_path.replace("\\", "/")  # S3 uses forward slashes
        if key not in seen:
            all_uploads.append((local, key))
            seen.add(key)

        # If it's a preview STL, also upload the print-ready copy
        if rel_path.startswith("stl/piece_") and rel_path.endswith(".stl"):
            filename    = Path(rel_path).name
            print_local = PRINT_DIR / filename
            print_key   = f"stl/print/{filename}"
            if print_key not in seen:
                all_uploads.append((print_local, print_key))
                seen.add(print_key)

    print(f"[uploader] Uploading {len(all_uploads)} file(s) to s3://{SCALEWAY_BUCKET}/", file=sys.stderr)

    for local_path, s3_key in all_uploads:
        if _upload_file(local_path, s3_key, verbose):
            ok_list.append(s3_key)
        else:
            err_list.append(s3_key)

    print(
        f"[uploader] Done: {len(ok_list)}/{len(all_uploads)} uploaded. "
        f"Errors: {len(err_list)}",
        file=sys.stderr,
    )
    return {"ok": ok_list, "errors": err_list}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Uploader — uploads files to Scaleway S3.")
    parser.add_argument(
        "paths",
        nargs="*",
        help="Relative paths to upload (e.g. stl/piece_5.stl index.html). Reads plan from stdin if omitted.",
    )
    parser.add_argument("--plan", help="Path to plan JSON (uses upload_paths field).")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if args.plan:
        plan  = json.loads(Path(args.plan).read_text())
        paths = plan.get("upload_paths", [])
    elif args.paths:
        paths = args.paths
    else:
        plan  = json.loads(sys.stdin.read())
        paths = plan.get("upload_paths", [])

    result = run(paths, verbose=args.verbose)
    print(json.dumps(result, indent=2))
