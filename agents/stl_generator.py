from __future__ import annotations

"""
STL Generator
─────────────
Runs OpenSCAD in parallel to render specified piece indices to STL files.

Uses the same `cmd /c` trick as generate_stls.ps1 to handle the `&` in
the project path on Windows.

Also writes a separate set of "print-ready" STLs to stl/print/ with the
same geometry — they are identical since OpenSCAD's output is already
manifold. (If you need orientation transforms for printing, add them here.)
"""

import json
import subprocess
import sys
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import SCAD_FILE, STL_DIR, PRINT_DIR, OPENSCAD_EXE, NUM_PIECES


def _render_piece(idx: int, out_path: Path, verbose: bool) -> tuple[int, bool, str]:
    """Render one piece. Returns (idx, success, message)."""
    scad  = str(SCAD_FILE)
    exe   = OPENSCAD_EXE
    out   = str(out_path)

    # Build the command — must go through cmd /c because the project
    # directory contains `&` which PowerShell / subprocess mis-parses.
    inner = f'"{exe}" -D "RENDER_MODE=\\"piece\\";PIECE_IDX={idx}" -o "{out}" "{scad}"'
    cmd   = ["cmd", "/c", inner]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode == 0:
            size_kb = round(out_path.stat().st_size / 1024, 1) if out_path.exists() else 0
            return idx, True, f"{out_path.name}  ({size_kb} KB)"
        else:
            stderr = (result.stderr or result.stdout or "no output").strip()
            return idx, False, stderr
    except subprocess.TimeoutExpired:
        return idx, False, "timed out after 300 s"
    except Exception as e:
        return idx, False, str(e)


def run(piece_indices: list[int], verbose: bool = False) -> dict:
    """
    Render the given piece indices.
    Returns {"ok": [...], "errors": [...]}
    """
    if not piece_indices:
        print("[stl_gen] No pieces to render.", file=sys.stderr)
        return {"ok": [], "errors": []}

    STL_DIR.mkdir(exist_ok=True)
    PRINT_DIR.mkdir(exist_ok=True)

    tasks: list[tuple[int, Path]] = []
    for idx in piece_indices:
        if idx < 0 or idx >= NUM_PIECES:
            print(f"[stl_gen] WARN: piece index {idx} out of range, skipping.", file=sys.stderr)
            continue
        tasks.append((idx, STL_DIR / f"piece_{idx}.stl"))

    print(f"[stl_gen] Rendering {len(tasks)} piece(s): {[t[0] for t in tasks]}", file=sys.stderr)

    ok_list:  list[int] = []
    err_list: list[dict] = []

    max_workers = min(len(tasks), os.cpu_count() or 4)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_render_piece, idx, path, verbose): idx for idx, path in tasks}
        for future in as_completed(futures):
            idx, success, msg = future.result()
            if success:
                ok_list.append(idx)
                print(f"[stl_gen]  ✓ piece_{idx}  {msg}", file=sys.stderr)

                # Copy to print/ directory (same geometry)
                src  = STL_DIR / f"piece_{idx}.stl"
                dest = PRINT_DIR / f"piece_{idx}.stl"
                if src.exists():
                    dest.write_bytes(src.read_bytes())
            else:
                err_list.append({"piece": idx, "error": msg})
                print(f"[stl_gen]  ✗ piece_{idx}  {msg}", file=sys.stderr)

    print(
        f"[stl_gen] Done: {len(ok_list)}/{len(tasks)} rendered. "
        f"Errors: {len(err_list)}",
        file=sys.stderr,
    )
    return {"ok": sorted(ok_list), "errors": err_list}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="STL generator — renders specified pieces via OpenSCAD.")
    parser.add_argument(
        "indices",
        nargs="*",
        type=int,
        help="Piece indices to render (0–11). Omit to render ALL pieces.",
    )
    parser.add_argument("--plan", help="Path to plan JSON (uses pieces_to_rerender field).")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if args.plan:
        plan = json.loads(Path(args.plan).read_text())
        indices = plan.get("pieces_to_rerender", list(range(NUM_PIECES)))
    elif args.indices:
        indices = args.indices
    else:
        indices = list(range(NUM_PIECES))

    result = run(indices, verbose=args.verbose)
    print(json.dumps(result, indent=2))
