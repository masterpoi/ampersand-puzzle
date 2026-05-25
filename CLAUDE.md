# CLAUDE.md — Agent Knowledge Base
## Ampersand Shatter Puzzle · D:\2026\dev\o&o

This file is the definitive briefing for any Claude agent working in this
repository.  Read it completely before making any changes.

---

## 1. What this project is

A 12-piece laser-cut / 3D-printed shatter puzzle shaped like an ampersand
(&), designed in OpenSCAD with lap-joint interlocking pieces fastened by
16 × M3 × 12 mm countersunk screws and brass heat-set inserts.

The project has three layers:
1. **Source design** — `ampersand_lap_joint.scad`
2. **Preview website** — `index.html` + `stl/piece_N.stl` files, served
   locally on port 3456 and hosted on Scaleway S3
3. **Agent orchestration system** — `orchestrator.py` + `agents/` +
   `config.py` — the pipeline for making, reviewing, and deploying changes

---

## 2. Repository layout

```
D:\2026\dev\o&o\
├── CLAUDE.md                    ← this file
├── ampersand_lap_joint.scad     ← THE source of truth for the puzzle geometry
├── index.html                   ← Three.js STL preview viewer
├── generate_stls.ps1            ← PowerShell batch renderer (all 12 pieces)
├── orchestrator.py              ← main entry point for the change pipeline
├── config.py                    ← shared paths, credentials, make_client()
├── requirements.txt             ← anthropic>=0.55.0
├── .gitignore                   ← stl/ is excluded; plans/ is tracked
├── agents/
│   ├── planner.py               ← Claude agent: request → JSON plan
│   ├── scad_editor.py           ← applies search/replace to the SCAD file
│   ├── stl_generator.py         ← parallel OpenSCAD renders
│   ├── preview_updater.py       ← applies search/replace to index.html
│   ├── uploader.py              ← AWS CLI uploads to Scaleway S3
│   ├── versioner.py             ← git commit / tag / log helpers
│   ├── previewer.py             ← before/after 3D diff viewer (Stage 1c)
│   └── bom_generator.py         ← Bill of Materials from SCAD + STL volumes
├── plans/                       ← audit trail: one JSON plan per run (tracked)
├── bom.md / bom.html / bom.json ← generated BOM (tracked; regenerate with 'bom')
├── stl/                         ← generated STLs (NOT in git — regenerate as needed)
│   ├── print/                   ← print-ready copies (same geometry)
│   └── preview/                 ← temporary previs renders (NOT in git)
└── .claude/
    └── launch.json              ← preview server config (port 3456)
```

---

## 3. SCAD file — critical knowledge

### 3.1 Dimensions

| Constant    | Value  | Meaning                                      |
|-------------|--------|----------------------------------------------|
| `PIECE_H`   | 12 mm  | Total piece thickness (= M3×12 screw length) |
| `TAB_H`     | 4 mm   | Tab zone height (lower z = 0…4)              |
| `TAB_REACH` | 15 mm  | How far each tab protrudes into neighbour     |
| `TAB_GAP`   | 0.15 mm| Clearance added to recess pocket on all sides |
| `SCREW_D`   | 3.2 mm | M3 shaft clearance bore                      |
| `INSERT_D`  | 4.6 mm | Heat-set insert outer diameter               |

### 3.2 RENDER_MODE values

Always use the `-D` flag to override `RENDER_MODE` and `PIECE_IDX` at
render time; never edit the file defaults for batch operations.

| Mode       | Output                                 |
|------------|----------------------------------------|
| `"all"`    | All 12 pieces assembled (default preview) |
| `"exploded"` | Pieces spread apart                  |
| `"piece"`  | Single piece STL — requires `PIECE_IDX=N` |
| `"debug"`  | 2D overlay for joint inspection        |

### 3.3 TAB_JOINTS array

The most-edited array in the file.  Each row:

```
[donor, receiver, cx, cy, dx, dy, half_w]
```

| Field      | Meaning                                                         |
|------------|-----------------------------------------------------------------|
| `donor`    | Piece index that provides the physical tab (z = 0…TAB_H)       |
| `receiver` | Piece index that gets the recess pocket + screw clearance hole  |
| `cx`, `cy` | Midpoint of the shared edge (mm, in polygon coordinate space)   |
| `dx`, `dy` | Unit normal pointing **from** the shared edge **into** the receiver |
| `half_w`   | Tab half-width perpendicular to (dx, dy) in mm                  |

**Screw position formula:**
```
screw_x = cx + dx * TAB_REACH / 2
screw_y = cy + dy * TAB_REACH / 2
```

**When changing a joint:** always re-render both `donor` AND `receiver`
pieces.  The tab geometry lives in the donor STL; the recess and screw
hole live in the receiver STL.

**Current 16 joints (TAB_JOINTS index → connection):**

```
 0  P0 → P1   cx=43.5,  cy=36.0,  dx=0.999, dy=0.042
 1  P1 → P2   cx=101.5, cy=29.0,  dx=0.976, dy=-0.218
 2  P2 → P3   cx=156.5, cy=35.0,  dx=0.999, dy=0.043
 3  P0 → P5   cx=42.0,  cy=93.5,  dx=1.000, dy=0.000
 4  P1 → P5   cx=75.0,  cy=65.0,  dx=-0.196,dy=0.981
 5  P4 → P5   cx=48.5,  cy=126.5, dx=0.870, dy=-0.492
 6  P5 → P6   cx=100.0, cy=90.0,  dx=0.970, dy=0.242
 7  P3 → P7   cx=177.5, cy=66.0,  dx=0.175, dy=0.985
 8  P6 → P7   cx=151.5, cy=102.5, dx=0.994, dy=0.107
 9  P5 → P9   cx=71.5,  cy=130.0, dx=0.396, dy=0.918   ← modified (was cx=73.5)
10  P6 → P10  cx=120.0, cy=128.5, dx=-0.226,dy=0.974
11  P7 → P11  cx=174.0, cy=136.5, dx=-0.058,dy=0.998
12  P8 → P9   cx=51.5,  cy=169.0, dx=0.994, dy=0.112
13  P9 → P10  cx=98.5,  cy=161.0, dx=0.986, dy=-0.164
14  P10→ P11  cx=146.5, cy=167.5, dx=0.999, dy=0.046
15  P4 → P8   cx=27.5,  cy=131.5, dx=-0.230,dy=0.973
```

### 3.4 PIECE_POLYS array

12 polygons tiling a 200 × 200 mm bounding box (origin = bottom-left,
Y increases upward).  The `&` glyph is 178 mm tall, nudged by
`X_OFFSET=9`, `Y_OFFSET=18`.  **Piece 11** (bottom-right corner) has
minimal overlap with the `&` glyph — it frequently renders as an empty
or near-empty STL.  This is correct by design, not a toolchain bug.

### 3.5 Assembly order (topological sort of donor→receiver DAG)

```
Layer 1 (no incoming tabs):  P0, P4
Layer 2:                     P1, P5, P8
Layer 3:                     P2, P3, P6, P9
Layer 4:                     P7, P10
Layer 5 (last):              P11
```

Place each piece tab-side-down and screw completely before laying the
next layer.  Install heat-set inserts into tabs **before** assembly.

---

## 4. The `&` in the directory path — critical Windows gotcha

The project directory is `D:\2026\dev\o&o`.  The `&` character is a
shell meta-character.

**Rule: never call OpenSCAD directly from Python's `subprocess` or from
PowerShell without wrapping via `cmd /c`.**

### Wrong (silently fails or crashes):
```python
subprocess.run([openscad_exe, "-D", f"RENDER_MODE=...", "-o", out, scad])
```
```powershell
& "C:\Program Files\OpenSCAD\openscad.exe" -D "..." -o "..." "D:\2026\dev\o&o\..."
```

### Correct (via cmd.exe which handles & literally inside quotes):
```python
inner = f'"{exe}" -D "RENDER_MODE=\\"piece\\";PIECE_IDX={idx}" -o "{out}" "{scad}"'
cmd   = ["cmd", "/c", inner]
subprocess.run(cmd, ...)
```

This is already implemented in `agents/stl_generator.py`.  Do not
refactor it away.

---

## 5. STL generation

### Running the orchestrator (preferred):
```powershell
python orchestrator.py "your change request here"
```

### Running all pieces manually (PowerShell):
```powershell
.\generate_stls.ps1
```

### Running a single piece manually (PowerShell):
```powershell
$openscad = "C:\Program Files\OpenSCAD\openscad.exe"
$scad     = "D:\2026\dev\o&o\ampersand_lap_joint.scad"
$out      = "D:\2026\dev\o&o\stl\piece_5.stl"
cmd /c "`"$openscad`" -D `"RENDER_MODE=\`"piece\`";PIECE_IDX=5`" -o `"$out`" `"$scad`""
```

### OpenSCAD exports ASCII STL, not binary:
OpenSCAD's output files start with `solid OpenSCAD_Model\n`.  Any code
that reads STL files must handle ASCII STL format (vertex lines, not
packed float32 triangles).  `agents/bom_generator.py` handles both;
use its `stl_volume_mm3()` function as a reference if you need volumes.
Binary STL detection: if `data[:6].lower() == b"solid "` AND
`"endsolid"` appears near the end, treat as ASCII.

### About piece_11:
`stl/piece_11.stl` will often be absent or empty.  The bottom-right
polygon of the grid barely overlaps the `&` glyph.  OpenSCAD produces
a valid (empty) geometry.  The preview site handles this gracefully
(shows an error badge on button P11).  Do not attempt to "fix" this.

### STL files are not in git:
`stl/` is in `.gitignore`.  To regenerate from any historical commit:
```powershell
git checkout <sha>
python orchestrator.py --plan plans/<plan-file>.json --skip-upload
```

---

## 6. Preview website

`index.html` is a Three.js STL viewer.  It loads `stl/piece_N.stl`
via relative HTTP paths.

### Starting the local server:
```powershell
python -m http.server 3456 --directory "D:\2026\dev\o&o"
```
Then open http://localhost:3456

**Do not open `index.html` directly as a `file://` URL** — browsers
block cross-origin requests and the STLs will silently fail to load.

The `.claude/launch.json` contains the server configuration and can
be used to start the server from within Claude Code.

### Preview features:
- **All Pieces** / **Exploded** / **Single** view modes
- Colour by piece, material (PLA beige), or neutral grey
- Hover highlighting with emissive glow
- Click a piece to select it in Single mode
- Piece buttons (P0–P11) in the toolbar show load state

### Explode vectors (JS matches SCAD):
The `EXPLODE_VEC` array in `index.html` mirrors `EXPLODE_VEC` in the
SCAD file.  If you change the SCAD explode vectors, update the HTML too.

---

## 7. Scaleway S3 hosting

| Setting          | Value                              |
|------------------|------------------------------------|
| Endpoint         | https://s3.fr-par.scw.cloud        |
| Bucket           | ampersand-puzzle                   |
| Region           | fr-par                             |
| AWS CLI profile  | `scaleway` (default; override via `SCW_PROFILE` env var) |
| Website URL      | http://ampersand-puzzle.s3-website.fr-par.scw.cloud |

### Upload a file:
```powershell
aws s3api put-object `
  --endpoint-url https://s3.fr-par.scw.cloud `
  --profile scaleway `
  --bucket ampersand-puzzle `
  --key stl/piece_5.stl `
  --body "D:\2026\dev\o&o\stl\piece_5.stl" `
  --content-type application/octet-stream
```

### Scaleway bucket policy resource format:
Scaleway does NOT use ARN prefixes.  The resource field must be:
```json
"Resource": "ampersand-puzzle/*"
```
NOT `"arn:aws:s3:::ampersand-puzzle/*"` — that returns a policy error.

### The bucket is NOT set to public-read ACL.
Access is controlled via a bucket policy.  Do not run
`put-bucket-acl --acl public-read`.  The existing `hoard-of-desire-frontend`
bucket on the same account is the reference for correct policy setup.

---

## 8. Agent orchestration system

### Running the full pipeline:
```powershell
python orchestrator.py "describe the change you want in plain English"
```

### Sub-commands:
```powershell
python orchestrator.py history    # show recent git-tagged changes
python orchestrator.py bom        # generate BOM (opens bom.html in browser)
```

### Useful flags:
```
--dry-run          Print the plan diff only — no files modified, no renders
--skip-previs      Skip the before/after preview render (Stage 1c)
-y / --yes         Auto-confirm the previs prompt (useful in CI / scripts)
--skip-render      Skip OpenSCAD rendering (still edits SCAD + HTML)
--skip-upload      Skip Scaleway upload
--skip-commit      Skip git commit/tag
--plan FILE        Load a pre-computed plan, skip the Planner agent
--save-plan FILE   Also save the plan to FILE (plans/ is always written)
-v / --verbose     Verbose per-agent output
```

### Pipeline stages in order:

| Stage | File | What it does |
|-------|------|--------------|
| 1 | `agents/planner.py` | Claude reads SCAD + HTML, emits a JSON plan via `output_plan` tool |
| 1b | `agents/versioner.py` | Saves plan to `plans/` and stages it |
| 1c | `agents/previewer.py` | Renders affected pieces from patched SCAD to `stl/preview/`; writes `preview.html`; asks y/N to proceed |
| 2 | `agents/scad_editor.py` | Applies `scad_changes` as `str.replace()` edits |
| 3 | `agents/stl_generator.py` | Parallel OpenSCAD renders for `pieces_to_rerender` |
| 4 | `agents/preview_updater.py` | Applies `html_changes` to `index.html` |
| 4b | `agents/versioner.py` | `git commit` + `git tag change-NNN-slug` |
| 5 | `agents/uploader.py` | AWS CLI `put-object` for each path in `upload_paths` |

### Plan JSON schema:
```json
{
  "summary": "one-line description",
  "scad_changes": [
    { "description": "...", "search": "exact string", "replace": "new string" }
  ],
  "pieces_to_rerender": [0, 5, 9],
  "html_changes": [
    { "description": "...", "search": "exact string", "replace": "new string" }
  ],
  "upload_paths": ["stl/piece_5.stl", "stl/piece_9.stl", "index.html"]
}
```

All `search` strings must be **exact literal matches** in the file —
`scad_editor.py` and `preview_updater.py` use plain `str.replace()`.
If a search string is not found, they call Claude to attempt a fuzzy
repair before giving up.

### Versioner — git tags:
Each successful run creates a tag like `change-001-move-joint-between-p5-and-p9`.
To list all change tags:
```powershell
git tag --list "change-*"
```
To regenerate STLs for a tagged state:
```powershell
git checkout change-001-move-joint-between-p5-and-p9
python agents/stl_generator.py   # renders all pieces from that SCAD state
```

---

## 9. Claude client — critical API key rule

**Never pass an empty string as `api_key`.**  The Anthropic SDK treats
an empty string as an explicit override, which disables its automatic
`ANTHROPIC_API_KEY` environment-variable lookup, producing:

```
TypeError: "Could not resolve authentication method. Expected either
api_key or auth_token to be set..."
```

Always use the `make_client()` helper from `config.py`:
```python
from config import make_client
client = make_client()
```

This passes `api_key` only when the env var is actually set, otherwise
calls `anthropic.Anthropic()` with no args so the SDK self-configures.

---

## 10. Python version

The system runs **Python 3.8**.  This means:

- Use `from __future__ import annotations` at the top of every file
  that uses `list[str]`, `dict[str, int]`, `tuple[str, bool]`, etc.
  in function signatures.  Without it, Python 3.8 raises
  `TypeError: 'type' object is not subscriptable` at import time.
- `match` statements are not available (3.10+).
- Walrus operator `:=` is available (3.8+).

All existing agents already have `from __future__ import annotations`.
New agents must too.

---

## 11. Default model and SDK usage

- **Model:** `claude-opus-4-7`
- **Thinking:** `{"type": "adaptive"}` — always use this; do not set
  `budget_tokens` (deprecated on Opus 4.7; will 400).
- **Streaming:** not currently used — all calls use `client.messages.create()`
  with `max_tokens=8192` for agent loops.
- **Prompt caching:** applied via `cache_control: {"type": "ephemeral"}`
  on the system prompt and on large stable content (SCAD file body).

---

## 12. Hardware BOM (for reference when answering questions)

| Component | Spec |
|-----------|------|
| Screws    | 16 × M3 × 12 mm DIN 7991 (countersunk socket head) |
| Inserts   | 16 × M3 brass heat-set inserts, OD 4.6 mm, depth 4 mm |
| Material  | PLA (or PETG), single colour recommended |
| Size      | ~200 × 200 × 12 mm assembled |
| Font      | Liberation Serif Bold (fallbacks: DejaVu Serif, FreeSerif, Georgia) |

---

## 13. Common tasks — quick reference

### Move a joint (e.g. shift P5→P9 joint 2 mm left):
```powershell
python orchestrator.py "Move the joint between P5 and P9 two millimetres to the left"
```
The planner will find TAB_JOINTS row 9 `[5, 9, ...]`, decrement `cx` by 2,
identify pieces 5 and 9 for re-render, and set upload_paths accordingly.

### Change a global dimension (e.g. increase TAB_GAP):
```powershell
python orchestrator.py "Increase TAB_GAP from 0.15 to 0.20 mm for a looser fit"
```
This touches only the SCAD header; all 12 pieces need re-rendering.

### Update only the preview site (no geometry change):
```powershell
python orchestrator.py --skip-render "Change the background colour default to black"
```

### Regenerate all STLs without a change request:
```powershell
python agents/stl_generator.py           # all 12
python agents/stl_generator.py 5 9       # specific pieces
```

### Upload specific files manually:
```powershell
python agents/uploader.py stl/piece_5.stl stl/piece_9.stl index.html
```

### Review what the planner would do before committing:
```powershell
python orchestrator.py --dry-run "your request here"
```

### Generate / refresh the BOM:
```powershell
python orchestrator.py bom        # prints summary, opens bom.html in browser
python agents/bom_generator.py    # same, run directly
```
The BOM reads hardware specs from the SCAD file and computes filament
volume from ASCII STL files using the signed-tetrahedra formula (no deps).
Piece 11 is often missing — the BOM reports it as a warning, not an error.

### See change history:
```powershell
python orchestrator.py history
git log --oneline
git tag --list "change-*"
```

---

## 14. Environment setup (first time on a new machine)

```powershell
# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Set the Anthropic API key
$env:ANTHROPIC_API_KEY = "sk-ant-..."

# 3. Configure AWS CLI for Scaleway
aws configure --profile scaleway
# Access Key ID:     <Scaleway access key>
# Secret Access Key: <Scaleway secret key>
# Default region:    fr-par
# Output format:     json

# 4. Ensure OpenSCAD is installed at:
#    C:\Program Files\OpenSCAD\openscad.exe

# 5. Start the preview server
python -m http.server 3456 --directory "D:\2026\dev\o&o"
# Then open: http://localhost:3456

# 6. Regenerate STLs if stl/ is empty
python agents/stl_generator.py
```
