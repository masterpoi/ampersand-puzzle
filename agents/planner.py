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

READ_FILE_TOOL = {
    "name": "read_file",
    "description": (
        "Read the contents of a source file (SCAD or HTML). "
        "Use this to understand the current state before drafting changes."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "enum": ["scad", "html"],
                "description": "'scad' for the OpenSCAD puzzle file, 'html' for the preview page.",
            }
        },
        "required": ["path"],
    },
}

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


def _handle_tool(tool_name: str, tool_input: dict) -> str:
    if tool_name == "read_file":
        path_key = tool_input["path"]
        if path_key == "scad":
            return SCAD_FILE.read_text(encoding="utf-8")
        elif path_key == "html":
            return INDEX_HTML.read_text(encoding="utf-8")
        else:
            return f"Unknown path key: {path_key}"
    return "Unknown tool"


def run(change_request: str, verbose: bool = False) -> dict:
    """
    Run the planner agent for the given change_request.
    Returns the plan dict.
    """
    client = make_client()

    system_prompt = """You are a precision engineering planning agent for a 12-piece ampersand shatter puzzle built in OpenSCAD.

Your job:
1. Read the SCAD file (and HTML if relevant) to understand the current state.
2. Analyse the user's change request carefully.
3. Output a structured JSON plan via the `output_plan` tool.

Key SCAD knowledge:
- TAB_JOINTS array: each row is [donor, receiver, cx, cy, dx, dy, half_w]
  where cx/cy is the joint centre and dx/dy is the unit normal to the shared edge.
  The screw sits at (cx + dx*TAB_REACH/2, cy + dy*TAB_REACH/2).
- PIECE_H = 12 mm, TAB_H = 4 mm, TAB_REACH = 8 mm (unless overridden).
- Pieces are labelled P0–P11. Piece 11 is often geometrically empty (no glyph overlap).
- Always identify the TAB_JOINTS row index(es) that correspond to the affected connection.
- When changing a joint cx/cy, both donor and receiver pieces must be re-rendered.

For `scad_changes`, provide exact literal strings so that a simple str.replace() can apply them safely.
For `pieces_to_rerender`, include both donor and receiver piece indices for any changed joint.
For `upload_paths`, include every changed STL (e.g. stl/piece_N.stl) plus index.html if HTML changed."""

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": change_request,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }
    ]

    tools = [READ_FILE_TOOL, OUTPUT_PLAN_TOOL]

    plan = None

    while True:
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
            print(f"[planner] stop_reason={response.stop_reason}", file=sys.stderr)

        # Accumulate assistant turn
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                if verbose:
                    print(f"[planner] tool_call: {block.name}({json.dumps(block.input)[:120]})", file=sys.stderr)

                if block.name == "output_plan":
                    plan = block.input  # capture the plan
                    result_text = "Plan recorded."
                else:
                    result_text = _handle_tool(block.name, block.input)

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                })

            messages.append({"role": "user", "content": tool_results})
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
