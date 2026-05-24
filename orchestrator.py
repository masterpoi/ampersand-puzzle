"""
Orchestrator
------------
Coordinates the full pipeline for implementing a change to the ampersand
shatter puzzle.

Pipeline stages:
  1. Planner        - converts change request to structured JSON plan
  1b. Versioner     - saves plan to plans/ and commits it to git
  2. SCAD Editor    - applies SCAD search/replace changes
  3. STL Generator  - re-renders affected pieces in parallel
  4. Preview Updater - applies HTML changes
  4b. Versioner     - commits SCAD + HTML changes, creates change-NNN tag
  5. Uploader       - pushes changed files to Scaleway S3

Usage:
  python orchestrator.py "Move the joint between P3 and P7 one millimetre to the right"
  echo "..." | python orchestrator.py -
  python orchestrator.py --plan plan.json     # skip planning, use saved plan
  python orchestrator.py --dry-run "..."      # plan only, do not modify files
  python orchestrator.py history              # show recent changes from git log

Flags:
  -v / --verbose    Print detailed per-agent logs
  --skip-upload     Skip the Scaleway upload stage
  --skip-render     Skip the STL render stage (still edits SCAD + HTML)
  --skip-commit     Skip git commit/tag (useful during development)
  --plan FILE       Load a pre-computed plan and skip the Planner stage
  --save-plan FILE  Save the plan to FILE before executing (in addition to plans/)
  --dry-run         Plan only, print plan, exit without modifying any files
"""

import argparse
import json
import sys
import time
from pathlib import Path


def _step(label: str):
    print(f"\n{'='*60}", flush=True)
    print(f"  {label}", flush=True)
    print(f"{'='*60}", flush=True)


def _show_history():
    from agents.versioner import log
    rows = log(15)
    if not rows:
        print("No commits yet.")
        return
    print(f"\n{'SHA':>7}  {'Date':>22}  Message")
    print("-" * 70)
    for r in rows:
        print(f"{r['sha']:>7}  {r['date'][:19]:>19}  {r['message'][:48]}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Ampersand puzzle change orchestrator.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "request",
        nargs="?",
        help="Change request text, or '-' to read from stdin, or 'history' to list recent changes.",
    )
    parser.add_argument("--plan",        metavar="FILE", help="Load a pre-computed plan JSON, skip planning.")
    parser.add_argument("--save-plan",   metavar="FILE", help="Also save plan to this FILE (plans/ is always written).")
    parser.add_argument("--dry-run",     action="store_true", help="Plan only; do not modify any files.")
    parser.add_argument("--skip-upload", action="store_true", help="Skip the Scaleway upload stage.")
    parser.add_argument("--skip-render", action="store_true", help="Skip the STL render stage.")
    parser.add_argument("--skip-commit", action="store_true", help="Skip git commit and tagging.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output from each agent.")
    args = parser.parse_args()

    # Special sub-command: history
    if args.request == "history":
        _show_history()
        return

    if not args.plan and not args.request:
        parser.error("Provide a change request, 'history', or --plan FILE.")

    # ── 1. Planner ─────────────────────────────────────────────────────────────
    if args.plan:
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        print(f"[orchestrator] Loaded plan from {args.plan}")
    else:
        request_text = sys.stdin.read().strip() if args.request == "-" else args.request

        _step("Stage 1 — Planner: converting request to plan")
        from agents.planner import run as plan_run

        t0   = time.time()
        plan = plan_run(request_text, verbose=args.verbose)
        dt   = time.time() - t0

        print(f"\n[planner] Completed in {dt:.1f}s")
        print(f"  Summary         : {plan.get('summary', '(none)')}")
        print(f"  SCAD changes    : {len(plan.get('scad_changes', []))}")
        print(f"  Pieces to render: {plan.get('pieces_to_rerender', [])}")
        print(f"  HTML changes    : {len(plan.get('html_changes', []))}")
        print(f"  Upload paths    : {plan.get('upload_paths', [])}")

    # Always save plan to plans/ for the audit trail
    if not args.dry_run and not args.skip_commit:
        from agents.versioner import save_plan as version_save_plan
        plan_path = version_save_plan(plan, verbose=args.verbose)
        print(f"\n[versioner] Plan recorded: {plan_path.name}")

    # Optionally also save to a user-specified location
    if args.save_plan:
        Path(args.save_plan).write_text(json.dumps(plan, indent=2), encoding="utf-8")
        print(f"[orchestrator] Plan also saved to {args.save_plan}")

    if args.dry_run:
        print("\n[orchestrator] --dry-run: stopping before file changes.")
        print(json.dumps(plan, indent=2))
        return

    # ── 2. SCAD Editor ─────────────────────────────────────────────────────────
    scad_changes = plan.get("scad_changes", [])
    if scad_changes:
        _step("Stage 2 — SCAD Editor: applying changes")
        from agents.scad_editor import run as scad_run

        applied = scad_run(scad_changes, verbose=args.verbose)
        print(f"[scad_editor] {applied}/{len(scad_changes)} changes applied.")
    else:
        print("\n[orchestrator] Stage 2 skipped — no SCAD changes.")

    # ── 3. STL Generator ───────────────────────────────────────────────────────
    pieces = plan.get("pieces_to_rerender", [])
    if pieces and not args.skip_render:
        _step("Stage 3 — STL Generator: re-rendering pieces")
        from agents.stl_generator import run as stl_run

        t0     = time.time()
        result = stl_run(pieces, verbose=args.verbose)
        dt     = time.time() - t0

        print(f"[stl_gen] {len(result['ok'])}/{len(pieces)} pieces rendered in {dt:.1f}s.")
        if result["errors"]:
            print(f"[stl_gen] Errors: {result['errors']}")
    elif args.skip_render:
        print("\n[orchestrator] Stage 3 skipped (--skip-render).")
    else:
        print("\n[orchestrator] Stage 3 skipped — no pieces to re-render.")

    # ── 4. Preview Updater ─────────────────────────────────────────────────────
    html_changes = plan.get("html_changes", [])
    if html_changes:
        _step("Stage 4 — Preview Updater: applying HTML changes")
        from agents.preview_updater import run as html_run

        applied = html_run(html_changes, verbose=args.verbose)
        print(f"[preview] {applied}/{len(html_changes)} changes applied.")
    else:
        print("\n[orchestrator] Stage 4 skipped — no HTML changes.")

    # ── 4b. Versioner: commit all edits ────────────────────────────────────────
    if not args.skip_commit:
        _step("Stage 4b — Versioner: committing changes")
        from agents.versioner import commit, tag, status

        try:
            sha      = commit(plan, verbose=args.verbose)
            tag_name = tag(plan, sha, verbose=args.verbose)
            st       = status()
            print(f"[versioner] Committed {sha}  tag: {tag_name}")
            print(f"[versioner] Branch: {st['branch']}  dirty: {st['dirty']}")
        except RuntimeError as e:
            print(f"[versioner] WARNING: {e}", file=sys.stderr)
    else:
        print("\n[orchestrator] Stage 4b skipped (--skip-commit).")

    # ── 5. Uploader ────────────────────────────────────────────────────────────
    upload_paths = plan.get("upload_paths", [])
    if upload_paths and not args.skip_upload:
        _step("Stage 5 — Uploader: pushing to Scaleway")
        from agents.uploader import run as upload_run

        result = upload_run(upload_paths, verbose=args.verbose)
        print(f"[uploader] {len(result['ok'])}/{len(upload_paths)} files uploaded.")
        if result["errors"]:
            print(f"[uploader] Errors: {result['errors']}")
    elif args.skip_upload:
        print("\n[orchestrator] Stage 5 skipped (--skip-upload).")
    else:
        print("\n[orchestrator] Stage 5 skipped — no paths to upload.")

    # ── Done ───────────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  Done: {plan.get('summary', 'change applied')}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
