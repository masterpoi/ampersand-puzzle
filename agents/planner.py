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
import threading
import time
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



def _start_spinner(label: str) -> "tuple[threading.Event, threading.Thread]":
    """Spin a progress indicator to stderr while the model is thinking."""
    stop_ev  = threading.Event()
    start_at = time.time()

    def _spin() -> None:
        frames = ("|", "/", "-", "\\")
        i = 0
        while not stop_ev.is_set():
            elapsed    = time.time() - start_at
            m, s       = divmod(int(elapsed), 60)
            t_str      = f"{m}:{s:02d}" if m else f"{s}s"
            print(f"\r  {label} {frames[i % 4]}  {t_str} ", end="", flush=True, file=sys.stderr)
            i += 1
            stop_ev.wait(0.12)
        # Erase the spinner line so the next real output lands cleanly
        print(f"\r{' ' * 64}\r", end="", flush=True, file=sys.stderr)

    th = threading.Thread(target=_spin, daemon=True)
    th.start()
    return stop_ev, th


def _stop_spinner(stop_ev: "threading.Event", th: "threading.Thread") -> None:
    stop_ev.set()
    th.join(timeout=1.0)


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
Your ONLY output must be a single call to the `output_plan` tool. Never write prose.

Key SCAD knowledge (read actual values from the file — these are defaults):
- PIECE_H = 12 mm, TAB_H = 5 mm, TAB_REACH = 15 mm, TAB_GAP = 0.15 mm
- TAB_JOINTS: each row is [donor, receiver, cx, cy, dx, dy, half_w]
  cx/cy = joint midpoint; dx/dy = unit normal pointing into receiver.
  Screw sits at (cx + dx*TAB_REACH/2, cy + dy*TAB_REACH/2).
- PIECE_POLYS: 12 polygon outlines. Moving a cut line means changing the shared
  vertex coordinates between the two adjacent piece polygons.
- Always re-render both pieces on either side of any modified boundary.

Rules for `scad_changes`:
- Every search string must be a verbatim literal substring of the SCAD file.
- When editing a PIECE_POLYS entry, include the entire polygon line so the
  replacement is unambiguous (no partial-line matches).

Rules for `pieces_to_rerender`: every piece whose polygon or joint changes.
Rules for `upload_paths`: every rebuilt stl/piece_N.stl; add index.html if HTML changed."""

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
                        "Analyse the SCAD file above, then call output_plan with your "
                        "complete structured plan. Your response must be exactly one "
                        "output_plan tool call — no prose before or after."
                    ),
                },
            ],
        }
    ]

    # Only output_plan is available — the model's only legal move is to produce the plan.
    # (tool_choice="any" is incompatible with thinking=adaptive, so we reduce the
    #  choice set to one tool and rely on forceful prompting + nudges.)
    tools = [OUTPUT_PLAN_TOOL]

    plan      = None
    nudges    = 0
    MAX_NUDGES = 4   # allow several attempts before giving up

    for _turn in range(MAX_NUDGES + 1):
        # Progress indicator — shown when not in verbose mode (verbose prints
        # its own per-turn stats which would interleave with the spinner).
        stop_ev = spin_th = None
        if not verbose:
            if _turn == 0:
                spin_label = "Planning"
            else:
                spin_label = f"Re-planning (nudge {nudges}/{MAX_NUDGES})"
            stop_ev, spin_th = _start_spinner(spin_label)

        # stream=True required by SDK when max_tokens is high enough to risk
        # exceeding the 10-minute non-streaming timeout.
        try:
            with client.messages.stream(
                model=CLAUDE_MODEL,
                max_tokens=32000,
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
            ) as stream:
                response = stream.get_final_message()
        finally:
            if stop_ev is not None:
                _stop_spinner(stop_ev, spin_th)

        if verbose:
            u = response.usage
            cache_hit  = getattr(u, "cache_read_input_tokens",    0) or 0
            cache_new  = getattr(u, "cache_creation_input_tokens", 0) or 0
            print(
                f"[planner] turn={_turn}  stop={response.stop_reason}"
                f"  in={u.input_tokens}  out={u.output_tokens}"
                f"  cache_hit={cache_hit}  cache_new={cache_new}",
                file=sys.stderr,
            )

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "tool_use":
            for block in response.content:
                if block.type == "tool_use" and block.name == "output_plan":
                    plan = block.input
                    if verbose:
                        print("[planner] output_plan captured.", file=sys.stderr)
                    break
            if plan is not None:
                break

        elif response.stop_reason in ("end_turn", "max_tokens"):
            nudges += 1
            if nudges > MAX_NUDGES:
                break
            reason = "hit the token limit" if response.stop_reason == "max_tokens" else "replied with text"
            print(f"[planner] WARNING: model {reason} (nudge {nudges}/{MAX_NUDGES})",
                  file=sys.stderr)
            messages.append({
                "role": "user",
                "content": (
                    "Your analysis is complete — now call output_plan to record it. "
                    "Required fields: summary (str), scad_changes (list of "
                    "{description, search, replace}), pieces_to_rerender (list of ints), "
                    "html_changes (list), upload_paths (list of str). "
                    "Use verbatim strings from the SCAD file for every search value. "
                    "Call output_plan NOW — no prose."
                ),
            })
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
