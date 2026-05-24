from __future__ import annotations

"""
Versioner
---------
Manages git version control for the puzzle pipeline.

Responsibilities:
  - save_plan()   : writes plan JSON to plans/<timestamp>_<slug>.json and
                    stages it for the upcoming commit.
  - commit()      : stages all changed tracked files (SCAD, HTML, plans/)
                    and creates a git commit with the plan summary as the
                    commit message.
  - tag()         : creates a lightweight git tag (e.g. change-003) so that
                    any rendered state can be checked out and re-rendered later.
  - status()      : returns a short dict describing the current repo state.

The versioner does NOT track STL binaries (they are in .gitignore).  The
git history of the SCAD file is the canonical version history; STLs can
always be regenerated from any commit via the orchestrator's --skip-upload flag.

Git must be installed and available on PATH.
"""

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import BASE_DIR, SCAD_FILE, INDEX_HTML

PLANS_DIR = BASE_DIR / "plans"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command in BASE_DIR."""
    result = subprocess.run(
        ["git"] + list(args),
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed:\n{result.stderr.strip()}"
        )
    return result


def _slug(text: str, max_len: int = 40) -> str:
    """Turn a summary string into a filesystem/tag-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower())
    slug = slug.strip("-")[:max_len]
    return slug or "change"


def _next_tag_number() -> int:
    """Return the next integer for change-NNN tags."""
    result = _git("tag", "--list", "change-*", check=False)
    existing = [t.strip() for t in result.stdout.splitlines() if t.strip()]
    nums = []
    for tag in existing:
        m = re.search(r"change-(\d+)$", tag)
        if m:
            nums.append(int(m.group(1)))
    return (max(nums) + 1) if nums else 1


# ── Public API ────────────────────────────────────────────────────────────────

def save_plan(plan: dict, verbose: bool = False) -> Path:
    """
    Write the plan to plans/<timestamp>_<slug>.json.
    Stages the file so it is included in the next commit.
    Returns the path of the saved plan file.
    """
    PLANS_DIR.mkdir(exist_ok=True)

    ts   = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = _slug(plan.get("summary", "change"))
    name = f"{ts}_{slug}.json"
    path = PLANS_DIR / name

    path.write_text(json.dumps(plan, indent=2), encoding="utf-8")

    # Stage the plan file
    _git("add", str(path.relative_to(BASE_DIR)))

    if verbose:
        print(f"[versioner] Plan saved: {path.relative_to(BASE_DIR)}", file=sys.stderr)

    return path


def commit(plan: dict, verbose: bool = False) -> str:
    """
    Stage all modified tracked files (SCAD, HTML) and commit.
    Returns the short commit SHA.
    """
    # Stage the files we care about (ignore errors for untracked/unchanged)
    for f in [SCAD_FILE, INDEX_HTML]:
        rel = f.relative_to(BASE_DIR)
        _git("add", str(rel), check=False)

    # Stage any new/changed files in plans/
    _git("add", "plans/", check=False)

    # Check if there is anything to commit
    diff = _git("diff", "--cached", "--name-only", check=False)
    staged = [l.strip() for l in diff.stdout.splitlines() if l.strip()]

    if not staged:
        if verbose:
            print("[versioner] Nothing staged — skipping commit.", file=sys.stderr)
        sha = _git("rev-parse", "--short", "HEAD").stdout.strip()
        return sha

    summary  = plan.get("summary", "Apply puzzle change")
    pieces   = plan.get("pieces_to_rerender", [])
    n_scad   = len(plan.get("scad_changes",  []))
    n_html   = len(plan.get("html_changes",  []))

    body_lines = [
        f"SCAD edits : {n_scad}",
        f"HTML edits : {n_html}",
        f"Re-rendered: {pieces if pieces else 'none'}",
        "",
        "Changed files:",
    ] + [f"  {f}" for f in staged]

    message = f"{summary}\n\n" + "\n".join(body_lines) + "\n\nCo-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"

    _git("commit", "-m", message)

    sha = _git("rev-parse", "--short", "HEAD").stdout.strip()
    if verbose:
        print(f"[versioner] Committed: {sha}  \"{summary}\"", file=sys.stderr)

    return sha


def tag(plan: dict, sha: str, verbose: bool = False) -> str:
    """
    Create a lightweight git tag change-NNN pointing at sha.
    Returns the tag name.
    """
    n        = _next_tag_number()
    slug     = _slug(plan.get("summary", "change"))
    tag_name = f"change-{n:03d}-{slug}"

    _git("tag", tag_name, sha)

    if verbose:
        print(f"[versioner] Tagged: {tag_name} -> {sha}", file=sys.stderr)

    return tag_name


def status(verbose: bool = False) -> dict:
    """Return a dict describing the current git state."""
    sha    = _git("rev-parse", "--short", "HEAD", check=False).stdout.strip() or "none"
    branch = _git("rev-parse", "--abbrev-ref", "HEAD", check=False).stdout.strip() or "unknown"
    tags_r = _git("tag", "--points-at", "HEAD", check=False).stdout.strip()
    tags   = [t for t in tags_r.splitlines() if t.strip()]
    dirty  = _git("status", "--porcelain", check=False).stdout.strip()

    info = {
        "sha": sha,
        "branch": branch,
        "tags": tags,
        "dirty": bool(dirty),
        "dirty_files": [l.strip() for l in dirty.splitlines() if l.strip()],
    }

    if verbose:
        print(f"[versioner] status: {info}", file=sys.stderr)

    return info


def log(n: int = 10) -> list[dict]:
    """Return the last n commits as a list of dicts."""
    fmt    = "%H%x1f%h%x1f%s%x1f%ai"
    result = _git("log", f"-{n}", f"--format={fmt}", check=False)
    rows   = []
    for line in result.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            rows.append({
                "sha":     parts[1].strip(),
                "full":    parts[0].strip(),
                "message": parts[2].strip(),
                "date":    parts[3].strip(),
            })
    return rows


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Versioner — git helpers for the puzzle pipeline.")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("status", help="Show current git state.")
    p_log = sub.add_parser("log", help="Show recent commits.")
    p_log.add_argument("-n", type=int, default=10)

    args = parser.parse_args()

    if args.cmd == "status":
        print(json.dumps(status(verbose=True), indent=2))
    elif args.cmd == "log":
        print(json.dumps(log(args.n), indent=2))
    else:
        parser.print_help()
