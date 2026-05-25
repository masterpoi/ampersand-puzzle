from __future__ import annotations

"""
Planner Agent
─────────────
Takes a free-text change request and converts it into a structured JSON plan
that the other agents can act on.

The plan schema:
{
  "summary": "one-line human-readable description",
  "scad_changes": [
    {
      "description": "what to change and why",
      "search":      "exact string to find in the SCAD file",
      "replace":     "replacement string"
    },
    ...
  ],
  "pieces_to_rerender": [0, 5, 9, ...],   // list of PIECE_IDX integers
  "html_changes": [
    {
      "description": "what to update in index.html",
      "search":      "exact string to find",
      "replace":     "replacement string"
    },
    ...
  ],
  "upload_paths": ["stl/piece_5.stl", "stl/piece_9.stl", "index.html", ...]
}

If a category has no changes, it should be an empty list / null.
"""

import json
import sys
from pathlib import Path

# Allow running from repo root or agents/ subdirectory
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import SCAD_FILE, INDEX_HTML, CLAUDE_MODEL, make_client

# ── Tool definitions for the planner ──────────────────────────────────────────

OUTPUT_PLAN_TOOL = {
    "name": "output_plan",
    "description": (
        "Emit the final structured change plan as JSON. "
        "Call this once you have read the relevant files and understand all required changes."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "One-line human-readable description of what will change.",
            },
            "scad_changes": {
                "type": "array",
                "description": "Ordered list of search/replace edits to apply to the SCAD file.",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "search":      {"type": "string"},
                        "replace":     {"type": "string"},
                    },
                    "required": ["description", "search", "replace"],
                },
            },
            "pieces_to_rerender": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Piece indices (0–11) that need their STL rebuilt.",
            },
            "html_changes": {
                "type": "array",
                "description": "Ordered list of search/replace edits to apply to index.html.",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "search":      {"type": "string"},
                        "replace":     {"type": "string"},
                    },
                    "required": ["description", "search", "replace"],
                },
            },
            "upload_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Paths relative to the project root that should be uploaded to Scaleway, "
                    "e.g. ['stl/piece_5.stl', 'index.html']."
                ),
            },
        },
        "required": ["summary", "scad_changes", "pieces_to_rerender", "html_changes", "upload_paths"],
    },
}



def run(change_request: str, verbose: bool = False) -> dict:
    """
    Run the planner agent for the given change_request.
    Returns the plan dict.

    Architecture: both source files are embedded directly in the user message so
    the model has full context in one shot. Only the `output_plan` tool is
    offered — there is no read_file round-trip — which means the model's only
    available action is to produce the plan.  This is necessary because
    `tool_choice="any"` is incompatible with `thinking=adaptive` (API 400), so
    we cannot force tool use directly; instead we reduce the choice set to one.
    """
    client = make_client()

    # Read source files once upfront
    scad_text = SCAD_FILE.read_text(encoding="utf-8")
    html_text = INDEX_HTML.read_text(encoding="utf-8")

    system_prompt = """You are a precision engineering planning agent for a 12-piece ampersand shatter puzzle built in OpenSCAD.

The SCAD source file and the HTML preview page are provided in the user message.
Your ONLY available action is to call the `output_plan` tool — do not write prose.

Key SCAD knowledge:
- TAB_JOINTS array: each row is [donor, receiver, cx, cy, dx, dy, half_w]
  where cx/cy is the joint midpoint and dx/dy is the unit normal into the receiver.
  Screw position: (cx + dx*TAB_REACH/2, cy + dy*TAB_REACH/2).
- PIECE_H = 12 mm, TAB_H = 4 mm, TAB_REACH = 8 mm (unless file says otherwise).
- Pieces are P0–P11. Piece 11 is often geometrically near-empty (fine by design).
- PIECE_POLYS defines polygon outlines for each piece. Moving a cut line means
  adjusting the shared vertex x (or y) coordinates in the two adjacent piece polygons.
- Always re-render both the donor AND receiver of any modified joint.

Rules for `scad_changes`:
- Each search string must be an exact literal substring of the SCAD file.
- If a polygon vertex must change, include the full polygon array in search/replace
  so the replacement is unambiguous.

Rules for `pieces_to_rerender`: list every piece index whose SCAD geometry changes.
Rules for `upload_paths`: every rebuilt stl/piece_N.stl plus index.html if HTML changed."""

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"SCAD source (ampersand_lap_joint.scad):\n\n{scad_text}",
                    "cache_control": {"type": "ephemeral"},
                },
                {
                    "type": "text",
                    "text": f"HTML preview (index.html):\n\n{html_text}",
                    "cache_control": {"type": "ephemeral"},
                },
                {
                    "type": "text",
                    "text": (
                        f"Change request: {change_request}\n\n"
                        "Call output_plan now with the complete structured plan."
                    ),
                },
            ],
        }
    ]

    # Only output_plan is available — the model's only legal move is to produce the plan.
    tools = [OUTPUT_PLAN_TOOL]

    plan   = None
    nudges = 0
    MAX_TURNS = 5

    for _turn in range(MAX_TURNS):
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=8192,
            thinking={"type": "adaptive"},
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=tools,
            messages=messages,
        )

        if verbose:
            print(f"[planner] turn={_turn} stop_reason={response.stop_reason}", file=sys.stderr)

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "tool_use":
            for block in response.content:
                if block.type == "tool_use" and block.name == "output_plan":
                    plan = block.input
                    if verbose:
                        print(f"[planner] output_plan called — plan captured.", file=sys.stderr)
                    break
            if plan is not None:
                break

        elif response.stop_reason == "end_turn":
            if nudges < 2:
                nudges += 1
                print(f"[planner] WARNING: model replied with text (nudge {nudges}/2)", file=sys.stderr)
                messages.append({
                    "role": "user",
                    "content": "You must call the output_plan tool now. Do not reply with text.",
                })
            else:
                break
        else:
            break

    if plan is None:
        raise RuntimeError("Planner agent did not call output_plan — no plan produced.")

    return plan


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Planner agent — converts a change request into a plan.")
    parser.add_argument("request", nargs="?", help="Change request text (or pipe via stdin).")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    request_text = args.request or sys.stdin.read().strip()
    if not request_text:
        parser.error("Provide a change request as an argument or via stdin.")

    plan = run(request_text, verbose=args.verbose)
    print(json.dumps(plan, indent=2))
