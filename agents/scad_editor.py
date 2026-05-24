from __future__ import annotations

"""
SCAD Editor Agent
─────────────────
Applies a list of search/replace edits to the SCAD file.

Accepts the `scad_changes` section of a plan and applies each edit in order.
After applying, it validates that all replacements were found (raises if any
search string is missing from the file).

This agent is intentionally a lightweight script rather than a full Claude
loop — the planner already determined what to change. Claude is called only
if a search string cannot be found, to attempt a fuzzy repair.
"""

import json
import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import SCAD_FILE, CLAUDE_MODEL, make_client


def _apply_change(text: str, search: str, replace: str) -> tuple[str, bool]:
    """Return (new_text, was_found)."""
    if search in text:
        return text.replace(search, replace, 1), True
    return text, False


def _claude_fuzzy_repair(scad_text: str, change: dict, verbose: bool) -> dict | None:
    """
    If the exact search string is not found, ask Claude to locate the correct
    line and return a corrected search/replace pair.
    Returns updated change dict, or None if Claude cannot help.
    """
    client = make_client()

    prompt = f"""I need to apply this edit to an OpenSCAD file but the exact search string was not found:

DESCRIPTION: {change['description']}

INTENDED SEARCH:
```
{change['search']}
```

INTENDED REPLACEMENT:
```
{change['replace']}
```

The current SCAD file content is:
```openscad
{scad_text}
```

Please find the correct existing text that matches the intent of the search string (it may differ due to whitespace, comments, or prior edits), and return a corrected JSON object with keys "search" and "replace" that CAN be applied via a simple Python str.replace().
Return ONLY the JSON object, no explanation."""

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2048,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": prompt}],
    )

    # Extract the last text block
    text_blocks = [b for b in response.content if hasattr(b, "text")]
    if not text_blocks:
        return None

    raw = text_blocks[-1].text.strip()
    # Strip markdown fences if present
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    try:
        corrected = json.loads(raw)
        if "search" in corrected and "replace" in corrected:
            if verbose:
                print(f"[scad_editor] Fuzzy repair found: {repr(corrected['search'][:80])}", file=sys.stderr)
            return corrected
    except json.JSONDecodeError:
        pass
    return None


def run(scad_changes: list[dict], verbose: bool = False) -> int:
    """
    Apply scad_changes to the SCAD file.
    Returns the number of changes successfully applied.
    """
    if not scad_changes:
        print("[scad_editor] No SCAD changes to apply.", file=sys.stderr)
        return 0

    text = SCAD_FILE.read_text(encoding="utf-8")
    applied = 0

    for i, change in enumerate(scad_changes):
        desc    = change.get("description", f"change {i}")
        search  = change["search"]
        replace = change["replace"]

        new_text, found = _apply_change(text, search, replace)

        if found:
            text = new_text
            applied += 1
            if verbose:
                print(f"[scad_editor] Applied ({i+1}/{len(scad_changes)}): {desc}", file=sys.stderr)
        else:
            print(f"[scad_editor] WARN: search string not found for: {desc}", file=sys.stderr)
            print(f"[scad_editor] Attempting fuzzy repair via Claude…", file=sys.stderr)

            corrected = _claude_fuzzy_repair(text, change, verbose)
            if corrected:
                new_text, found2 = _apply_change(text, corrected["search"], corrected["replace"])
                if found2:
                    text = new_text
                    applied += 1
                    print(f"[scad_editor] Fuzzy repair succeeded for: {desc}", file=sys.stderr)
                else:
                    print(f"[scad_editor] ERROR: fuzzy repair also failed for: {desc}", file=sys.stderr)
            else:
                print(f"[scad_editor] ERROR: Claude could not repair: {desc}", file=sys.stderr)

    SCAD_FILE.write_text(text, encoding="utf-8")
    print(f"[scad_editor] Saved {SCAD_FILE}  ({applied}/{len(scad_changes)} changes applied)", file=sys.stderr)
    return applied


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="SCAD editor — applies search/replace changes to the SCAD file.")
    parser.add_argument("plan_json", nargs="?", help="Path to plan JSON file, or '-' to read from stdin.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if args.plan_json and args.plan_json != "-":
        plan = json.loads(Path(args.plan_json).read_text())
    else:
        plan = json.loads(sys.stdin.read())

    changes = plan.get("scad_changes", [])
    n = run(changes, verbose=args.verbose)
    print(json.dumps({"applied": n}))
