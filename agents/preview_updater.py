from __future__ import annotations

"""
Preview Updater Agent
──────────────────────
Applies HTML changes from the plan to index.html, then validates the page
still loads correctly in a headless check (optional).

Like the SCAD editor, this is primarily a search/replace applier with a
Claude fallback for fuzzy matching.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import INDEX_HTML, CLAUDE_MODEL, make_client


def _apply_change(text: str, search: str, replace: str) -> tuple[str, bool]:
    if search in text:
        return text.replace(search, replace, 1), True
    return text, False


def _claude_fuzzy_repair(html_text: str, change: dict, verbose: bool) -> dict | None:
    client = make_client()

    prompt = f"""I need to apply this edit to an HTML file but the exact search string was not found:

DESCRIPTION: {change['description']}

INTENDED SEARCH:
```
{change['search']}
```

INTENDED REPLACEMENT:
```
{change['replace']}
```

The current HTML file content (first 8000 chars shown):
```html
{html_text[:8000]}
```

Find the correct existing text matching the intent, and return a corrected JSON object with keys "search" and "replace" applicable via Python str.replace().
Return ONLY the JSON object, no explanation."""

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2048,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": prompt}],
    )

    text_blocks = [b for b in response.content if hasattr(b, "text")]
    if not text_blocks:
        return None

    raw = text_blocks[-1].text.strip()
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    try:
        corrected = json.loads(raw)
        if "search" in corrected and "replace" in corrected:
            if verbose:
                print(f"[preview] Fuzzy repair found: {repr(corrected['search'][:60])}", file=sys.stderr)
            return corrected
    except json.JSONDecodeError:
        pass
    return None


def run(html_changes: list[dict], verbose: bool = False) -> int:
    """
    Apply html_changes to index.html.
    Returns number of changes applied.
    """
    if not html_changes:
        print("[preview] No HTML changes to apply.", file=sys.stderr)
        return 0

    text    = INDEX_HTML.read_text(encoding="utf-8")
    applied = 0

    for i, change in enumerate(html_changes):
        desc    = change.get("description", f"change {i}")
        search  = change["search"]
        replace = change["replace"]

        new_text, found = _apply_change(text, search, replace)

        if found:
            text = new_text
            applied += 1
            if verbose:
                print(f"[preview] Applied ({i+1}/{len(html_changes)}): {desc}", file=sys.stderr)
        else:
            print(f"[preview] WARN: search string not found for: {desc}", file=sys.stderr)
            corrected = _claude_fuzzy_repair(text, change, verbose)
            if corrected:
                new_text, found2 = _apply_change(text, corrected["search"], corrected["replace"])
                if found2:
                    text = new_text
                    applied += 1
                    print(f"[preview] Fuzzy repair succeeded for: {desc}", file=sys.stderr)
                else:
                    print(f"[preview] ERROR: fuzzy repair also failed for: {desc}", file=sys.stderr)
            else:
                print(f"[preview] ERROR: Claude could not repair: {desc}", file=sys.stderr)

    INDEX_HTML.write_text(text, encoding="utf-8")
    print(f"[preview] Saved {INDEX_HTML}  ({applied}/{len(html_changes)} changes applied)", file=sys.stderr)
    return applied


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Preview updater — applies HTML changes to index.html.")
    parser.add_argument("plan_json", nargs="?", help="Path to plan JSON, or '-' for stdin.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if args.plan_json and args.plan_json != "-":
        plan = json.loads(Path(args.plan_json).read_text())
    else:
        plan = json.loads(sys.stdin.read())

    changes = plan.get("html_changes", [])
    n = run(changes, verbose=args.verbose)
    print(json.dumps({"applied": n}))
