"""
Shared configuration for the ampersand puzzle agent orchestration system.
"""
import os
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR   = Path(r"D:\2026\dev\o&o")
SCAD_FILE  = BASE_DIR / "ampersand_lap_joint.scad"
STL_DIR    = BASE_DIR / "stl"
INDEX_HTML = BASE_DIR / "index.html"
AGENTS_DIR = BASE_DIR / "agents"

OPENSCAD_EXE = r"C:\Program Files\OpenSCAD\openscad.exe"

NUM_PIECES = 12  # pieces 0..11 (piece 11 may be geometrically empty)
PRINT_DIR  = STL_DIR / "print"   # print-ready STLs (same geometry, separate copy)

# ── Scaleway / S3 ──────────────────────────────────────────────────────────────
SCALEWAY_ENDPOINT   = "https://s3.fr-par.scw.cloud"
SCALEWAY_BUCKET     = "ampersand-puzzle"
SCALEWAY_REGION     = "fr-par"

# AWS CLI profile to use (configure with: aws configure --profile scaleway)
# Must have SCW_ACCESS_KEY / SCW_SECRET_KEY set, or use the named profile.
AWS_PROFILE = os.environ.get("SCW_PROFILE", "scaleway")

# ── Claude ─────────────────────────────────────────────────────────────────────
CLAUDE_MODEL = "claude-opus-4-7"

def make_client():
    """
    Return an anthropic.Anthropic() client.
    If ANTHROPIC_API_KEY is set in the environment the SDK picks it up
    automatically.  Never pass an empty string — that disables auto-detection.
    """
    import anthropic
    api_key = os.environ.get("ANTHROPIC_API_KEY") or None
    return anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
