from __future__ import annotations

"""
BOM Generator
-------------
Generates a Bill of Materials for the puzzle from two sources:

  1. ampersand_lap_joint.scad  — hardware specs (M3 screw length, insert OD,
                                 joint count) parsed via regex.
  2. stl/piece_N.stl           — filament volume calculated from binary STL
                                 geometry using the signed-tetrahedra method
                                 (no extra dependencies).

Outputs written to BASE_DIR:
  bom.md   — Markdown table (commit-friendly, human-readable)
  bom.html — Styled page, viewable at http://localhost:3456/bom.html
  bom.json — Raw data for programmatic use

Also prints a summary table to the terminal.

PLA density assumed: 1.24 g/cm³
"""

import json
import re
import struct
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import BASE_DIR, SCAD_FILE, STL_DIR, NUM_PIECES

PLA_DENSITY = 1.24   # g/cm³

# ── STL volume (pure Python, no deps) ─────────────────────────────────────────

def _signed_vol(ax: float, ay: float, az: float,
                bx: float, by: float, bz: float,
                cx: float, cy: float, cz: float) -> float:
    """Signed volume of the tetrahedron from the origin to triangle ABC."""
    return (ax * (by * cz - bz * cy) +
            ay * (bz * cx - bx * cz) +
            az * (bx * cy - by * cx)) / 6.0


def stl_volume_mm3(path: Path) -> float:
    """
    Compute the solid volume of an STL file (ASCII or binary) via the
    signed-tetrahedra method.  No external dependencies.

    OpenSCAD exports ASCII STL by default (header starts 'solid OpenSCAD_Model').
    Binary STL is also handled.
    """
    import re as _re

    try:
        raw = path.read_bytes()
    except OSError:
        return 0.0

    vol = 0.0

    # ── ASCII STL detection: starts with "solid " ───────────────────────────
    # Binary STLs can also start with "solid " if the header text happens to
    # match, but OpenSCAD's binary output does NOT — it uses ASCII format.
    is_ascii = raw[:6].lower() == b"solid "

    if is_ascii:
        # Quick check: if the last non-whitespace bytes are "endsolid...", it's ASCII.
        # If not, treat as binary (some edge case binary STLs start with "solid ").
        tail = raw[-64:].decode("latin-1", errors="replace").lower()
        if "endsolid" not in tail:
            is_ascii = False

    if is_ascii:
        try:
            text    = raw.decode("latin-1", errors="replace")
            coords  = _re.findall(
                r"vertex\s+([\d.eE+\-]+)\s+([\d.eE+\-]+)\s+([\d.eE+\-]+)", text
            )
            floats  = [(float(x), float(y), float(z)) for x, y, z in coords]
            for i in range(0, len(floats) - 2, 3):
                a, b, c = floats[i], floats[i+1], floats[i+2]
                vol += _signed_vol(*a, *b, *c)
        except Exception:
            return 0.0
    else:
        # Binary STL: 80-byte header + 4-byte count + N×50-byte triangles
        if len(raw) < 84:
            return 0.0
        try:
            n      = struct.unpack_from("<I", raw, 80)[0]
            offset = 84
            for _ in range(n):
                ax, ay, az = struct.unpack_from("<3f", raw, offset + 12)
                bx, by, bz = struct.unpack_from("<3f", raw, offset + 24)
                cx, cy, cz = struct.unpack_from("<3f", raw, offset + 36)
                vol    += _signed_vol(ax, ay, az, bx, by, bz, cx, cy, cz)
                offset += 50
        except struct.error:
            pass

    return abs(vol)


# ── Parse SCAD constants ───────────────────────────────────────────────────────

def _num(text: str, name: str, default: float) -> float:
    m = re.search(rf"\b{re.escape(name)}\s*=\s*([\d.]+)", text)
    return float(m.group(1)) if m else default


def parse_scad(scad_text: str) -> dict:
    # Count TAB_JOINTS entries: each line starts with optional whitespace then
    # a bracket containing two integers (donor, receiver)
    joints = re.findall(r"^\s*\[\s*\d+\s*,\s*\d+\s*,", scad_text, re.MULTILINE)
    return {
        "piece_h":      _num(scad_text, "PIECE_H",      12.0),
        "tab_h":        _num(scad_text, "TAB_H",         4.0),
        "tab_reach":    _num(scad_text, "TAB_REACH",    15.0),
        "tab_gap":      _num(scad_text, "TAB_GAP",       0.15),
        "screw_d":      _num(scad_text, "SCREW_D",       3.2),
        "cs_d":         _num(scad_text, "CS_D",          6.0),
        "cs_depth":     _num(scad_text, "CS_DEPTH",      2.5),
        "insert_d":     _num(scad_text, "INSERT_D",      4.6),
        "insert_depth": _num(scad_text, "INSERT_DEPTH",  4.0),
        "n_joints":     len(joints),
    }


# ── Filament per piece ─────────────────────────────────────────────────────────

def calc_filament(verbose: bool = False) -> list[dict]:
    rows = []
    for i in range(NUM_PIECES):
        path = STL_DIR / f"piece_{i}.stl"
        if path.exists():
            vol_mm3  = stl_volume_mm3(path)
            vol_cm3  = vol_mm3 / 1_000.0
            weight_g = vol_cm3 * PLA_DENSITY
            status   = "ok"
        else:
            vol_mm3 = vol_cm3 = weight_g = 0.0
            status  = "missing"
        rows.append({
            "piece":    i,
            "vol_mm3":  round(vol_mm3,  1),
            "vol_cm3":  round(vol_cm3,  2),
            "weight_g": round(weight_g, 1),
            "status":   status,
        })
        if verbose:
            tag = " (STL missing)" if status == "missing" else ""
            print(f"[bom] P{i:02d}: {vol_cm3:6.2f} cm³  {weight_g:5.1f} g{tag}", file=sys.stderr)
    return rows


# ── Markdown ───────────────────────────────────────────────────────────────────

def render_markdown(specs: dict, filament: list[dict]) -> str:
    n     = specs["n_joints"]
    ph    = int(specs["piece_h"])
    id_d  = specs["insert_d"]
    id_dp = int(specs["insert_depth"])

    total_cm3 = sum(r["vol_cm3"]  for r in filament)
    total_g   = sum(r["weight_g"] for r in filament)
    est_lo    = round(total_g * 0.25, 0)   # ~15% infill + 2 walls
    est_hi    = round(total_g * 0.32, 0)   # ~20% infill + 3 walls

    lines = [
        "# Bill of Materials — Ampersand Shatter Puzzle",
        "",
        "## Hardware",
        "",
        "| Item | Qty | Specification | Notes |",
        "|------|----:|---------------|-------|",
        f"| Countersunk screw | {n} | M3 × {ph} mm DIN 7991 | Flat socket head (hex key) |",
        f"| Brass heat-set insert | {n} | M3, OD {id_d} mm, depth {id_dp} mm | Press-fit with soldering iron, install before assembly |",
        "",
        "## Filament (PLA, 100 % solid geometry)",
        "",
        "| Piece | Volume (cm³) | Solid weight (g) | |",
        "|------:|-------------:|-----------------:|-|",
    ]
    for r in filament:
        flag = " ⚠ STL missing" if r["status"] == "missing" else ""
        lines.append(
            f"| P{r['piece']:02d} | {r['vol_cm3']:>6.2f} | {r['weight_g']:>5.1f} |{flag} |"
        )
    lines += [
        f"| **Total** | **{total_cm3:.2f}** | **{total_g:.1f}** | |",
        "",
        "> **Estimated actual print weight** (filament + walls, no support):  ",
        f"> &nbsp;• 15 % infill, 2 perimeters ≈ **{est_lo:.0f} g**  ",
        f"> &nbsp;• 20 % infill, 3 perimeters ≈ **{est_hi:.0f} g**  ",
        "> *(Varies by slicer — treat as a rough guide only.)*",
        "",
        "## Recommended Print Settings",
        "",
        "| Setting | Recommended value |",
        "|---------|-------------------|",
        "| Layer height | 0.20 mm |",
        "| Infill | 15–20 % (gyroid or honeycomb) |",
        "| Perimeters / walls | 2–3 |",
        "| Print orientation | Flat, tab-side down — no supports needed |",
        "| Material | PLA or PETG |",
        "| Bed adhesion | Brim recommended for small pieces |",
        "",
        "## Assembly Order",
        "",
        "Install heat-set inserts into **all** tab pockets before starting.",
        "Place each piece tab-side-down; screw down fully before the next layer.",
        "",
        "| Layer | Pieces |",
        "|------:|--------|",
        "| 1 (no incoming tabs) | P0, P4 |",
        "| 2 | P1, P5, P8 |",
        "| 3 | P2, P3, P6, P9 |",
        "| 4 | P7, P10 |",
        "| 5 (last) | P11 |",
        "",
        "*Generated by `agents/bom_generator.py`*",
    ]
    return "\n".join(lines) + "\n"


# ── HTML ───────────────────────────────────────────────────────────────────────

def render_html(specs: dict, filament: list[dict]) -> str:
    n    = specs["n_joints"]
    ph   = int(specs["piece_h"])
    id_d = specs["insert_d"]
    idp  = int(specs["insert_depth"])

    total_cm3 = sum(r["vol_cm3"]  for r in filament)
    total_g   = sum(r["weight_g"] for r in filament)
    est_lo = round(total_g * 0.25, 0)
    est_hi = round(total_g * 0.32, 0)

    piece_rows = ""
    for r in filament:
        cls  = ' class="missing"' if r["status"] == "missing" else ""
        flag = " ⚠" if r["status"] == "missing" else ""
        piece_rows += (
            f'<tr{cls}>'
            f'<td>P{r["piece"]:02d}</td>'
            f'<td class="n">{r["vol_cm3"]:.2f}</td>'
            f'<td class="n">{r["weight_g"]:.1f}{flag}</td>'
            f'</tr>\n'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BOM — Ampersand Shatter Puzzle</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#1a1a2e;color:#e0e0e0;font-family:'Segoe UI',sans-serif;
        padding:28px 32px;max-width:820px;margin:0 auto;line-height:1.5}}
  h1{{color:#e94560;font-size:1.3rem;margin-bottom:6px}}
  .sub{{color:#888;font-size:.8rem;margin-bottom:24px}}
  h2{{color:#7fb3d3;font-size:.85rem;text-transform:uppercase;
      letter-spacing:.08em;margin:28px 0 10px;border-bottom:1px solid #0f3460;
      padding-bottom:4px}}
  table{{width:100%;border-collapse:collapse;font-size:.85rem;margin-bottom:14px}}
  th{{background:#0f3460;color:#e94560;padding:8px 12px;text-align:left;
      font-weight:600}}
  td{{padding:7px 12px;border-bottom:1px solid #1a2a3a}}
  td.n{{text-align:right;font-variant-numeric:tabular-nums}}
  tr.total td{{font-weight:700;color:#e94560;border-top:2px solid #e94560;
               background:#0f1a2e}}
  tr.missing td{{color:#e07070}}
  tr:not(.total):hover td{{background:#16213e}}
  .note{{font-size:.78rem;color:#aaa;margin:6px 0 0;line-height:1.7}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));
         gap:8px;font-size:.82rem}}
  .card{{background:#16213e;border:1px solid #0f3460;border-radius:4px;
         padding:9px 12px}}
  .card .lbl{{color:#888;font-size:.72rem;margin-bottom:3px}}
  .card .val{{color:#e0e0e0;font-weight:600}}
  ol{{padding-left:18px;font-size:.84rem}}
  ol li{{margin:4px 0}}
  footer{{margin-top:40px;font-size:.7rem;color:#444;border-top:1px solid #0f3460;
          padding-top:10px}}
</style>
</head>
<body>

<h1>&#38; Ampersand Shatter Puzzle &mdash; Bill of Materials</h1>
<div class="sub">12 pieces &nbsp;|&nbsp; {n} joints &nbsp;|&nbsp; {n} screws &nbsp;|&nbsp; {n} inserts</div>

<h2>Hardware</h2>
<table>
  <tr><th>Item</th><th>Qty</th><th>Specification</th><th>Notes</th></tr>
  <tr>
    <td>Countersunk screw</td><td>{n}</td>
    <td>M3 &times; {ph}&nbsp;mm &nbsp;DIN&nbsp;7991</td>
    <td>Flat socket head (hex key)</td>
  </tr>
  <tr>
    <td>Brass heat-set insert</td><td>{n}</td>
    <td>M3, OD&nbsp;{id_d}&nbsp;mm, depth&nbsp;{idp}&nbsp;mm</td>
    <td>Press-fit with soldering iron &mdash; install <b>before</b> assembly</td>
  </tr>
</table>

<h2>Filament &nbsp;<span style="color:#888;font-size:.75rem;font-weight:400">PLA @ 1.24 g/cm&sup3; &mdash; 100&nbsp;% solid geometry</span></h2>
<table>
  <tr><th>Piece</th><th class="n">Volume (cm&sup3;)</th><th class="n">Max weight (g)</th></tr>
  {piece_rows}
  <tr class="total">
    <td>Total</td>
    <td class="n">{total_cm3:.2f}</td>
    <td class="n">{total_g:.1f}</td>
  </tr>
</table>
<p class="note">
  Estimated <b>actual</b> print weight (infill + walls, no support):<br>
  &nbsp; 15&nbsp;% infill, 2 perimeters &asymp; <b>{est_lo:.0f}&nbsp;g</b>
  &nbsp;&nbsp;&bull;&nbsp;&nbsp;
  20&nbsp;% infill, 3 perimeters &asymp; <b>{est_hi:.0f}&nbsp;g</b>
</p>

<h2>Print Settings</h2>
<div class="grid">
  <div class="card"><div class="lbl">Layer height</div><div class="val">0.20 mm</div></div>
  <div class="card"><div class="lbl">Infill</div><div class="val">15&ndash;20&nbsp;% (gyroid)</div></div>
  <div class="card"><div class="lbl">Perimeters</div><div class="val">2&ndash;3</div></div>
  <div class="card"><div class="lbl">Orientation</div><div class="val">Flat, tab-side down</div></div>
  <div class="card"><div class="lbl">Supports</div><div class="val">None required</div></div>
  <div class="card"><div class="lbl">Material</div><div class="val">PLA or PETG</div></div>
</div>

<h2>Assembly Order</h2>
<ol>
  <li><b>Layer 1</b> &mdash; no incoming tabs: P0, P4</li>
  <li><b>Layer 2:</b> P1, P5, P8</li>
  <li><b>Layer 3:</b> P2, P3, P6, P9</li>
  <li><b>Layer 4:</b> P7, P10</li>
  <li><b>Layer 5</b> &mdash; last: P11</li>
</ol>
<p class="note">Install heat-set inserts into all tab pockets <b>before</b> starting assembly.</p>

<footer>Generated by agents/bom_generator.py</footer>
</body>
</html>"""


# -- Terminal summary -----------------------------------------------------------

def print_summary(specs: dict, filament: list[dict]) -> None:
    total_cm3 = sum(r["vol_cm3"]  for r in filament)
    total_g   = sum(r["weight_g"] for r in filament)
    est_lo    = round(total_g * 0.25, 0)
    n    = specs["n_joints"]
    ph   = int(specs["piece_h"])
    id_d = specs["insert_d"]
    sep  = "-" * 54
    missing = [r["piece"] for r in filament if r["status"] == "missing"]

    print("\n  HARDWARE")
    print("  " + sep)
    print(f"  {'Countersunk screw':<28} {n:>3}x  M3 x {ph} mm DIN 7991")
    print(f"  {'Brass heat-set insert':<28} {n:>3}x  M3 OD {id_d} mm")
    print("\n  FILAMENT  (solid volume)")
    print("  " + sep)
    print(f"  {'Total solid volume':<28}        {total_cm3:.1f} cm3")
    print(f"  {'Total solid weight (PLA)':<28}        {total_g:.1f} g")
    print(f"  {'Estimated print weight':<28}       ~{est_lo:.0f} g  (15% infill)")
    if missing:
        run_args = " ".join(str(p) for p in missing)
        print(f"\n  Warning: STL files missing for pieces: {missing}")
        print(f"  Run: python agents/stl_generator.py {run_args}")
    print("\n  Saved: bom.md  bom.html  bom.json")

# ── Public entry point ────────────────────────────────────────────────────────

def run(verbose: bool = False, open_browser: bool = False) -> dict:
    """
    Generate the BOM and write bom.md, bom.html, bom.json.
    Returns the raw data dict.
    """
    scad_text = SCAD_FILE.read_text(encoding="utf-8")
    specs     = parse_scad(scad_text)
    filament  = calc_filament(verbose=verbose)

    total_cm3 = round(sum(r["vol_cm3"]  for r in filament), 2)
    total_g   = round(sum(r["weight_g"] for r in filament), 1)

    data = {
        "hardware": {
            "screws":  {
                "qty":  specs["n_joints"],
                "spec": f"M3 x {int(specs['piece_h'])} mm DIN 7991",
            },
            "inserts": {
                "qty":  specs["n_joints"],
                "spec": (f"M3 OD {specs['insert_d']} mm "
                         f"depth {int(specs['insert_depth'])} mm"),
            },
        },
        "filament": {
            "pieces":    filament,
            "total_cm3": total_cm3,
            "total_g":   total_g,
            "density_g_cm3": PLA_DENSITY,
            "material":  "PLA",
        },
        "specs": specs,
    }

    md   = render_markdown(specs, filament)
    html = render_html(specs, filament)

    (BASE_DIR / "bom.md"  ).write_text(md,                         encoding="utf-8")
    (BASE_DIR / "bom.html").write_text(html,                       encoding="utf-8")
    (BASE_DIR / "bom.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

    print_summary(specs, filament)

    if open_browser:
        try:
            webbrowser.open("http://localhost:3456/bom.html")
        except Exception:
            pass

    return data


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="BOM generator.")
    parser.add_argument("-v", "--verbose",  action="store_true")
    parser.add_argument("--open",           action="store_true", help="Open bom.html in browser.")
    args = parser.parse_args()

    run(verbose=args.verbose, open_browser=args.open)
