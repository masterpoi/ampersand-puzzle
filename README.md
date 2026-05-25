# Ampersand Shatter Puzzle

A 12-piece interlocking shatter puzzle shaped like an **&**, designed in OpenSCAD.
Pieces are held together by M3 countersunk screws and brass heat-set inserts —
no glue, no magnets, fully reversible.

**[Live 3D viewer →](http://ampersand-puzzle.s3-website.fr-par.scw.cloud)**

---

## How it works

The `&` glyph is divided into a 4 × 3 grid of polygons, each extruded to 12 mm.
Adjacent pieces interlock via lap joints: one piece donates a 5 mm tab that slides
into a matching recess in its neighbour, fastened with a single M3 screw into a
brass heat-set insert.

```
Layer 1 (base):   P0  P4
Layer 2:          P1  P5  P8
Layer 3:          P2  P3  P6  P9
Layer 4:          P7  P10
Layer 5 (top):    P11
```

Assemble bottom-to-top, tab-side-down. Install inserts before assembly.
Piece 11 (bottom-right corner) barely overlaps the glyph and may print as a
thin sliver — this is by design.

---

## Bill of materials

| Component | Qty | Spec |
|-----------|-----|------|
| Countersunk screws | 16 | M3 × 12 mm DIN 7991 |
| Brass heat-set inserts | 16 | M3, OD 4.6 mm, depth 5 mm |
| Filament | ~200 g | PLA or PETG, single colour recommended |

Assembled size: ~200 × 200 × 12 mm.

---

## Print settings

| Setting | Value |
|---------|-------|
| Layer height | 0.2 mm |
| Infill | 15 % |
| Supports | None needed |
| Orientation | Flat (tab-side down = z = 0) |
| Perimeters | 3+ recommended for joint strength |

Print all 12 pieces in the same orientation. The tab zone (bottom 5 mm) must be
solid — set a minimum solid layer count if your slicer uses sparse infill near the
bed.

---

## Assembly

1. Press heat-set inserts into the **donor tab** of each piece (the protruding
   tab, not the recess). Use a soldering iron at ~200 °C.
2. Start with Layer 1 (P0, P4). Lay tab-side-down on a flat surface.
3. Add each subsequent layer, aligning tabs into recesses.
4. Drive an M3 × 12 screw through each countersunk hole. Do not overtighten —
   finger-tight plus a quarter turn is enough.
5. The puzzle disassembles by removing screws in reverse layer order.

---

## Repository structure

```
ampersand_lap_joint.scad   ← sole source of truth for the geometry
index.html                 ← Three.js in-browser STL viewer
orchestrator.py            ← agent pipeline entry point
agents/
  planner.py               ← Claude: change request → JSON plan
  scad_editor.py           ← applies SCAD search/replace edits
  stl_generator.py         ← parallel OpenSCAD renders
  preview_updater.py       ← applies HTML edits
  previewer.py             ← before/after 3D diff viewer
  uploader.py              ← Scaleway S3 upload
  versioner.py             ← git commit + tag helpers
  bom_generator.py         ← bill of materials from STL volumes
plans/                     ← JSON audit trail of every change (tracked)
stl/                       ← generated STLs (not in git)
```

---

## Agent pipeline

Changes to the puzzle flow through a Claude-powered pipeline:

```
User request (plain English)
  → Planner         reads SCAD + HTML, emits a structured JSON plan
  → Previewer       renders a before/after 3D diff, asks for confirmation
  → SCAD Editor     applies search/replace edits to the .scad file
  → STL Generator   re-renders affected pieces in parallel via OpenSCAD
  → Preview Updater applies any HTML changes to index.html
  → Versioner       git commit + change-NNN tag
  → Uploader        pushes changed files to Scaleway S3
```

### Prerequisites

- Python 3.8+
- [OpenSCAD](https://openscad.org/downloads.html) installed at `C:\Program Files\OpenSCAD\openscad.exe`
- [scw CLI](https://www.scaleway.com/en/cli/) installed and logged in
- [AWS CLI](https://aws.amazon.com/cli/) installed
- Anthropic API key

### Quick start

```powershell
pip install -r requirements.txt
$env:ANTHROPIC_API_KEY = "sk-ant-..."

python orchestrator.py setup    # configure Scaleway upload credentials (once)
python orchestrator.py upload   # sync all STLs to the hosted site
```

### Making a change

```powershell
python orchestrator.py "Move the joint between P5 and P9 two millimetres to the left"
```

The pipeline shows a before/after 3D preview and asks for confirmation before
modifying any files.

### Other commands

```powershell
python orchestrator.py --dry-run "your request"   # plan only, no file changes
python orchestrator.py bom                         # regenerate bill of materials
python orchestrator.py history                     # show change history
python orchestrator.py upload                      # re-upload all STLs
```

---

## Local preview

```powershell
python -m http.server 3456 --directory .
```

Open http://localhost:3456 — the viewer auto-starts when the previewer runs.
Do not open `index.html` directly as a `file://` URL; browsers block the STL
cross-origin requests.

---

## Hosted site

Files are served from a Scaleway S3 bucket (`fr-par` region).  
Public URL: **http://ampersand-puzzle.s3-website.fr-par.scw.cloud**
