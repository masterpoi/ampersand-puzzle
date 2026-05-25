"""
Orchestrator
------------
Coordinates the full pipeline for implementing a change to the ampersand
shatter puzzle.

Pipeline stages:
  1.  Planner        - converts change request to structured JSON plan
  1b. Versioner      - saves plan to plans/ and stages it in git
  1c. Previewer      - renders affected pieces from a patched SCAD copy,
                       opens a before/after viewer, asks for confirmation
  2.  SCAD Editor    - applies SCAD search/replace changes
  3.  STL Generator  - re-renders affected pieces in parallel
  4.  Preview Updater - applies HTML changes to index.html
  4b. Versioner      - commits SCAD + HTML changes, creates change-NNN tag
  5.  Uploader       - pushes changed files to Scaleway S3

Sub-commands (no change request needed):
  setup    - auto-configure AWS CLI profile from scw (Scaleway) CLI (run once)
  history  - show recent changes from git log
  bom      - generate Bill of Materials (bom.md / bom.html / bom.json)
  upload   - full re-sync: upload all STLs + index.html to Scaleway

Usage:
  python orchestrator.py "Move the joint between P3 and P7 one millimetre right"
  python orchestrator.py --dry-run "Change the screw size to M4"
  python orchestrator.py --plan plan.json
  python orchestrator.py history
  python orchestrator.py bom

Flags:
  -v / --verbose     Per-agent verbose output
  -y / --yes         Skip the previs confirmation prompt
  --dry-run          Plan only; no file changes
  --skip-previs      Skip the before/after preview render
  --skip-render      Skip the STL render stage
  --skip-upload      Skip the Scaleway upload stage
  --skip-commit      Skip git commit and tagging
  --plan FILE        Load a pre-computed plan, skip planning
  --save-plan FILE   Also save plan to FILE (plans/ is always written)
"""

import argparse
import json
import sys
import time
from pathlib import Path


def _step(label: str) -> None:
    print(f"\n{'='*60}", flush=True)
    print(f"  {label}", flush=True)
    print(f"{'='*60}", flush=True)


def _cleanup_previs() -> None:
    """Delete stl/preview/ and preview.html left by an aborted previs run."""
    import shutil
    from agents.previewer import PREVIEW_DIR, PREVIEW_HTML
    if PREVIEW_DIR.exists():
        shutil.rmtree(PREVIEW_DIR, ignore_errors=True)
        print("[orchestrator] Cleaned up stl/preview/")
    if PREVIEW_HTML.exists():
        try:
            PREVIEW_HTML.unlink()
            print("[orchestrator] Cleaned up preview.html")
        except OSError:
            pass


def _show_history() -> None:
    from agents.versioner import log
    rows = log(15)
    if not rows:
        print("No commits yet.")
        return
    print(f"\n{'SHA':>7}  {'Date':>19}  Message")
    print("-" * 72)
    for r in rows:
        print(f"{r['sha']:>7}  {r['date'][:19]}  {r['message'][:48]}")
    print()


def _run_bom(verbose: bool) -> None:
    _step("BOM Generator")
    from agents.bom_generator import run as bom_run
    bom_run(verbose=verbose, open_browser=True)


def _run_setup() -> None:
    import setup_aws
    setup_aws.main()


def _run_upload_all(verbose: bool) -> None:
    _step("Uploader: full re-sync to Scaleway")
    from agents.uploader import run_all
    result = run_all(verbose=verbose)
    print(f"[uploader] {len(result['ok'])} uploaded, {len(result['errors'])} errors.")


def _run_planner(request_text: str, verbose: bool) -> dict:
    """Invoke the planner and print a summary of the resulting plan."""
    from agents.planner import run as plan_run
    t0   = time.time()
    plan = plan_run(request_text, verbose=verbose)
    dt   = time.time() - t0
    print(f"\n[planner] Completed in {dt:.1f}s")
    print(f"  Summary         : {plan.get('summary', '(none)')}")
    print(f"  SCAD changes    : {len(plan.get('scad_changes', []))}")
    print(f"  Pieces to render: {plan.get('pieces_to_rerender', [])}")
    print(f"  HTML changes    : {len(plan.get('html_changes', []))}")
    print(f"  Upload paths    : {plan.get('upload_paths', [])}")
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ampersand puzzle change orchestrator.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "request",
        nargs="?",
        help="Change request, 'history', or 'bom'.",
    )
    parser.add_argument("--plan",        metavar="FILE", help="Load a pre-computed plan, skip planning.")
    parser.add_argument("--save-plan",   metavar="FILE", help="Also save plan to FILE (plans/ always written).")
    parser.add_argument("--dry-run",     action="store_true", help="Plan only; do not modify files.")
    parser.add_argument("--skip-previs", action="store_true", help="Skip the before/after preview stage.")
    parser.add_argument("--skip-render", action="store_true", help="Skip the STL render stage.")
    parser.add_argument("--skip-upload", action="store_true", help="Skip Scaleway upload.")
    parser.add_argument("--skip-commit", action="store_true", help="Skip git commit and tagging.")
    parser.add_argument("-y", "--yes",   action="store_true", help="Auto-confirm the previs prompt.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose per-agent output.")
    args = parser.parse_args()

    # ── Sub-commands ───────────────────────────────────────────────────────────
    if args.request == "history":
        _show_history()
        return

    if args.request == "bom":
        _run_bom(args.verbose)
        return

    if args.request == "setup":
        _run_setup()
        return

    if args.request == "upload":
        _run_upload_all(args.verbose)
        return

    if not args.plan and not args.request:
        parser.error("Provide a change request, 'history', 'bom', or --plan FILE.")

    # ── Stage 1: Planner ───────────────────────────────────────────────────────
    can_replan   = False
    full_request = None

    if args.plan:
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        print(f"[orchestrator] Loaded plan from {args.plan}")
    else:
        full_request = sys.stdin.read().strip() if args.request == "-" else args.request
        can_replan   = True
        _step("Stage 1 — Planner: converting request to plan")
        plan = _run_planner(full_request, args.verbose)

    # Optionally save to an explicit user path (plans/ audit written after confirmation)
    if args.save_plan:
        Path(args.save_plan).write_text(json.dumps(plan, indent=2), encoding="utf-8")
        print(f"[orchestrator] Plan also saved to {args.save_plan}")

    if args.dry_run:
        from agents.previewer import show_diff
        show_diff(plan)
        print("[orchestrator] --dry-run: stopping before file changes.")
        return

    # auto-proceed when --yes or stdin is not a TTY (piped/scripted)
    confirmed = args.yes or not sys.stdin.isatty()

    # ── Stage 1c: Previs + confirmation loop ───────────────────────────────────
    # The loop lets the user optionally type a correction that triggers a
    # re-plan.  Entering 'y' or 'yes' proceeds, 'n'/empty aborts, anything
    # else is treated as a correction appended to the original request.
    from agents.previewer import run as previs_run, show_diff

    while True:
        has_geometry = bool(plan.get("pieces_to_rerender"))
        has_changes  = bool(
            plan.get("scad_changes") or plan.get("html_changes") or has_geometry
        )

        if not args.skip_previs:
            if has_geometry:
                _step("Stage 1c — Previs: before/after preview")
                previs_run(plan, open_browser=True, verbose=args.verbose)
            elif has_changes:
                show_diff(plan)

        # When --skip-previs or nothing to confirm, proceed immediately
        if args.skip_previs or not has_changes:
            break

        if confirmed:
            print("[orchestrator] --yes / non-interactive: proceeding automatically.")
            break

        # Interactive prompt — accepts y, n, or a free-text correction
        try:
            answer = input(
                "\n  [y] proceed  [n] abort  "
                "[or type a correction to re-plan]:\n  > "
            ).strip()
        except (KeyboardInterrupt, EOFError):
            answer = ""

        low = answer.lower()
        if low in ("y", "yes"):
            break
        elif low in ("", "n", "no"):
            _cleanup_previs()
            print("[orchestrator] Aborted.")
            return
        else:
            # Free-text correction — re-plan with the additional context
            if not can_replan:
                print(
                    "[orchestrator] Cannot re-plan: was loaded from --plan FILE. "
                    "Enter y to proceed or n to abort."
                )
                continue
            _cleanup_previs()
            full_request += f"\n\nRevision: {answer}"
            _step("Stage 1 (revised) — Planner: re-planning with correction")
            plan = _run_planner(full_request, args.verbose)
            # Loop back to show the updated previs

    # ── Stage 1b: Save plan to audit trail (only after confirmation) ──────────
    if not args.skip_commit:
        from agents.versioner import save_plan as version_save_plan
        plan_path = version_save_plan(plan, verbose=args.verbose)
        print(f"\n[versioner] Plan recorded: {plan_path.name}")

    # ── Stage 2: SCAD Editor ──────────────────────────────────────────────────
    scad_changes = plan.get("scad_changes", [])
    if scad_changes:
        _step("Stage 2 — SCAD Editor: applying changes")
        from agents.scad_editor import run as scad_run
        applied = scad_run(scad_changes, verbose=args.verbose)
        print(f"[scad_editor] {applied}/{len(scad_changes)} changes applied.")
    else:
        print("\n[orchestrator] Stage 2 skipped — no SCAD changes.")

    # ── Stage 3: STL Generator ────────────────────────────────────────────────
    pieces = plan.get("pieces_to_rerender", [])
    if pieces and not args.skip_render:
        _step("Stage 3 — STL Generator: re-rendering pieces")
        from agents.stl_generator import run as stl_run
        t0     = time.time()
        result = stl_run(pieces, verbose=args.verbose)
        dt     = time.time() - t0
        print(f"[stl_gen] {len(result['ok'])}/{len(pieces)} pieces in {dt:.1f}s.")
        if result["errors"]:
            print(f"[stl_gen] Errors: {result['errors']}")
    elif args.skip_render:
        print("\n[orchestrator] Stage 3 skipped (--skip-render).")
    else:
        print("\n[orchestrator] Stage 3 skipped — no pieces to re-render.")

    # ── Stage 4: Preview Updater ──────────────────────────────────────────────
    html_changes = plan.get("html_changes", [])
    if html_changes:
        _step("Stage 4 — Preview Updater: applying HTML changes")
        from agents.preview_updater import run as html_run
        applied = html_run(html_changes, verbose=args.verbose)
        print(f"[preview] {applied}/{len(html_changes)} changes applied.")
    else:
        print("\n[orchestrator] Stage 4 skipped — no HTML changes.")

    # ── Stage 4b: Versioner ───────────────────────────────────────────────────
    if not args.skip_commit:
        _step("Stage 4b — Versioner: committing")
        from agents.versioner import commit, tag, status
        try:
            sha      = commit(plan, verbose=args.verbose)
            tag_name = tag(plan, sha, verbose=args.verbose)
            st       = status()
            print(f"[versioner] {sha}  tag: {tag_name}  branch: {st['branch']}")
        except RuntimeError as e:
            print(f"[versioner] WARNING: {e}", file=sys.stderr)
    else:
        print("\n[orchestrator] Stage 4b skipped (--skip-commit).")

    # ── Stage 5: Uploader ─────────────────────────────────────────────────────
    upload_paths = plan.get("upload_paths", [])
    if upload_paths and not args.skip_upload:
        _step("Stage 5 — Uploader: pushing to Scaleway")
        from agents.uploader import run as upload_run, WEBSITE_URL
        result = upload_run(upload_paths, verbose=args.verbose)
        ok_n   = len(result["ok"])
        tot_n  = len(upload_paths)
        print(f"[uploader] {ok_n}/{tot_n} files uploaded.")
        if result["errors"]:
            print(f"[uploader] Errors: {result['errors']}")
            print(f"[uploader] Re-run failed uploads:  python orchestrator.py upload")
        if ok_n:
            print(f"[uploader] Site -> {WEBSITE_URL}")
    elif args.skip_upload:
        print("\n[orchestrator] Stage 5 skipped (--skip-upload).")
    else:
        print("\n[orchestrator] Stage 5 skipped — no paths to upload.")

    # ── Done ──────────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  Done: {plan.get('summary', 'change applied')}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
