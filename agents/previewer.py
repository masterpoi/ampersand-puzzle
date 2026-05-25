from __future__ import annotations

"""
Previewer
---------
Pre-visualisation stage that runs between planning and file editing.

Given a plan dict it:
  1. Prints a coloured terminal diff of every planned SCAD change.
  2. Applies the changes to a temporary SCAD copy and renders only the
     affected pieces into stl/preview/.
  3. Writes preview.html — a side-by-side before/after Three.js viewer
     where BOTH panels share one camera so you orbit both simultaneously.
  4. Optionally opens http://localhost:3456/preview.html in the browser.

The orchestrator calls run() then asks the user whether to proceed.
The previewer never modifies the real SCAD file or stl/ directory.
"""

import json
import os
import subprocess
import sys
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import BASE_DIR, SCAD_FILE, STL_DIR, OPENSCAD_EXE

PREVIEW_DIR  = STL_DIR / "preview"
PREVIEW_HTML = BASE_DIR / "preview.html"
TEMP_SCAD    = BASE_DIR / "_preview_patch.scad"

# ANSI — degrade silently on non-TTY stdout
def _c(code: str, text: str) -> str:
    return f"{code}{text}\033[0m" if sys.stdout.isatty() else text

RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"


# ── 1. Terminal diff ───────────────────────────────────────────────────────────

def show_diff(plan: dict) -> None:
    """Print a human-readable summary of every planned change."""
    summary = plan.get("summary", "(no summary)")
    print(f"\n{_c(BOLD, 'Plan: ' + summary)}")

    scad_changes = plan.get("scad_changes", [])
    if scad_changes:
        print(f"\n  {_c(CYAN, f'SCAD changes  ({len(scad_changes)}):')}")
        for i, c in enumerate(scad_changes):
            desc = c.get("description", "")
            print(f"\n    {_c(YELLOW, f'[{i+1}] {desc}')}")
            for line in c["search"].splitlines():
                print(f"    {_c(RED,   '  - ' + line)}")
            for line in c["replace"].splitlines():
                print(f"    {_c(GREEN, '  + ' + line)}")
    else:
        print(f"\n  {_c(DIM, 'No SCAD changes.')}")

    pieces = plan.get("pieces_to_rerender", [])
    html_c = plan.get("html_changes", [])
    upload = plan.get("upload_paths", [])

    if pieces: print(f"\n  {_c(CYAN, 'Pieces to re-render:')} {pieces}")
    if html_c: print(f"  {_c(CYAN, f'HTML changes: {len(html_c)}')}")
    if upload: print(f"  {_c(CYAN, 'Upload paths:')} {upload}")
    print()


# ── 2. Render preview STLs ────────────────────────────────────────────────────

def _patch_text(scad_changes: list[dict]) -> str | None:
    """Return patched SCAD text, or None if nothing changed."""
    text = patched = SCAD_FILE.read_text(encoding="utf-8")
    for c in scad_changes:
        patched = patched.replace(c["search"], c["replace"], 1)
    return patched if patched != text else None


def _render_piece(scad_path: str, idx: int, out: Path) -> tuple[int, bool, str]:
    inner  = f'"{OPENSCAD_EXE}" -D "RENDER_MODE=\\"piece\\";PIECE_IDX={idx}" -o "{out}" "{scad_path}"'
    result = subprocess.run(["cmd", "/c", inner], capture_output=True, text=True, timeout=300)
    if result.returncode == 0 and out.exists():
        kb = round(out.stat().st_size / 1024, 1)
        return idx, True, f"{kb} KB"
    return idx, False, (result.stderr or result.stdout or "failed").strip()[:120]


def render_preview_stls(plan: dict, verbose: bool = False) -> dict:
    """Render affected pieces from a patched SCAD copy into stl/preview/."""
    pieces       = plan.get("pieces_to_rerender", [])
    scad_changes = plan.get("scad_changes", [])

    if not pieces:
        return {"ok": [], "errors": [], "skipped": True}

    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

    patched = _patch_text(scad_changes) if scad_changes else None
    if patched:
        TEMP_SCAD.write_text(patched, encoding="utf-8")
        scad_path = str(TEMP_SCAD)
    else:
        scad_path = str(SCAD_FILE)

    ok_list, err_list = [], []
    try:
        tasks = [(idx, PREVIEW_DIR / f"piece_{idx}.stl") for idx in pieces]
        workers = min(len(tasks), os.cpu_count() or 4)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            fmap = {pool.submit(_render_piece, scad_path, idx, path): idx
                    for idx, path in tasks}
            for fut in as_completed(fmap):
                idx, ok, msg = fut.result()
                if ok:
                    ok_list.append(idx)
                    print(f"[previewer]  piece_{idx}: {msg}", file=sys.stderr)
                else:
                    err_list.append({"piece": idx, "error": msg})
                    print(f"[previewer]  piece_{idx}: FAILED — {msg}", file=sys.stderr)
    finally:
        if patched and TEMP_SCAD.exists():
            TEMP_SCAD.unlink()

    return {"ok": sorted(ok_list), "errors": err_list}


# ── 3. Generate preview.html ──────────────────────────────────────────────────

def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def generate_preview_html(plan: dict, render_result: dict) -> Path:
    """Write a two-panel before/after Three.js page to preview.html."""
    summary  = plan.get("summary", "Proposed change")
    pieces   = sorted(render_result.get("ok", []))
    changes  = plan.get("scad_changes", [])

    diff_items = ""
    for c in changes:
        s0 = _esc(c["search"].splitlines()[0]  if c["search"]  else "")
        r0 = _esc(c["replace"].splitlines()[0] if c["replace"] else "")
        diff_items += (
            f'<li><b>{_esc(c.get("description",""))}</b>'
            f'<br><code class="rem">- {s0}</code>'
            f'<br><code class="add">+ {r0}</code></li>'
        )
    diff_html = f"<ul>{diff_items}</ul>" if diff_items else "<p style='color:#888'>No SCAD changes.</p>"

    piece_labels = ", ".join(f"P{i}" for i in pieces) or "none"
    pieces_js    = json.dumps(pieces)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Previs: {_esc(summary)}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #1a1a2e; color: #e0e0e0; font-family: 'Segoe UI', sans-serif;
          height: 100vh; display: flex; flex-direction: column; overflow: hidden; }}
  header {{ padding: 8px 20px; background: #16213e; border-bottom: 1px solid #0f3460; flex-shrink: 0; }}
  header h1 {{ font-size: 0.95rem; color: #e94560; }}
  header .sub {{ font-size: 0.72rem; color: #888; margin-top: 2px; }}
  .diff {{ padding: 6px 20px; background: #12172a; border-bottom: 1px solid #0f3460;
           font-size: 0.73rem; max-height: 110px; overflow-y: auto; flex-shrink: 0; }}
  .diff ul {{ list-style: none; display: flex; flex-wrap: wrap; gap: 12px; }}
  .diff li {{ min-width: 260px; }}
  code.rem {{ color: #e07070; display: block; }}
  code.add {{ color: #70e070; display: block; }}
  .panels {{ flex: 1; display: flex; gap: 2px; min-height: 0; }}
  .panel {{ flex: 1; display: flex; flex-direction: column; min-height: 0; }}
  .lbl {{ text-align: center; padding: 3px 0; font-size: 0.78rem; font-weight: 700;
           letter-spacing: 0.04em; flex-shrink: 0; }}
  .lbl.before {{ background: #271a1a; color: #e07070; }}
  .lbl.after  {{ background: #1a271a; color: #70e070; }}
  canvas {{ display: block; width: 100% !important; flex: 1; min-height: 0; }}
  #status {{ position: fixed; bottom: 8px; left: 50%; transform: translateX(-50%);
             background: rgba(0,0,0,.75); padding: 3px 12px; border-radius: 10px;
             font-size: 0.72rem; color: #aaa; pointer-events: none; }}
  .sp {{ display: inline-block; width: 9px; height: 9px; border: 2px solid #aaa;
         border-top-color: #e94560; border-radius: 50%; animation: spin .7s linear infinite; }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
</style>
</head>
<body>

<header>
  <h1>Previs &mdash; {_esc(summary)}</h1>
  <div class="sub">Affected: {piece_labels} &nbsp;|&nbsp; Drag either panel to orbit both simultaneously</div>
</header>

<div class="diff">
  <b style="color:#aaa;margin-right:8px">Planned SCAD changes:</b>
  {diff_html}
</div>

<div class="panels">
  <div class="panel">
    <div class="lbl before">BEFORE (current stl/)</div>
    <canvas id="cb"></canvas>
  </div>
  <div class="panel">
    <div class="lbl after">AFTER (stl/preview/)</div>
    <canvas id="ca"></canvas>
  </div>
</div>

<div id="status"><span class="sp"></span> Loading&hellip;</div>

<script type="importmap">
{{
  "imports": {{
    "three":          "https://cdn.jsdelivr.net/npm/three@0.165.0/build/three.module.js",
    "three/addons/":  "https://cdn.jsdelivr.net/npm/three@0.165.0/examples/jsm/"
  }}
}}
</script>

<script type="module">
import * as THREE from 'three';
import {{ OrbitControls }} from 'three/addons/controls/OrbitControls.js';
import {{ STLLoader }}     from 'three/addons/loaders/STLLoader.js';

const PIECES = {pieces_js};

// ── Two scenes, one shared camera ─────────────────────────────────────────────
function mkScene(bg) {{
  const s = new THREE.Scene();
  s.background = new THREE.Color(bg);
  s.add(new THREE.AmbientLight(0xffffff, 0.55));
  const sun = new THREE.DirectionalLight(0xffffff, 1.1);
  sun.position.set(200, -100, 300);
  s.add(sun);
  const fill = new THREE.DirectionalLight(0x8899cc, 0.35);
  fill.position.set(-150, 200, 100);
  s.add(fill);
  return s;
}}

const sBefore = mkScene(0x1a1a2e);
const sAfter  = mkScene(0x1a2216);

const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 2000);
camera.position.set(100, -220, 280);
camera.up.set(0, 0, 1);

const cb = document.getElementById('cb');
const ca = document.getElementById('ca');
const rB = new THREE.WebGLRenderer({{ canvas: cb, antialias: true }});
const rA = new THREE.WebGLRenderer({{ canvas: ca, antialias: true }});
rB.setPixelRatio(devicePixelRatio);
rA.setPixelRatio(devicePixelRatio);

// Both controls share the same camera — drag either panel and both update
const ctrlB = new OrbitControls(camera, cb);
const ctrlA = new OrbitControls(camera, ca);
[ctrlB, ctrlA].forEach(c => {{
  c.target.set(100, 100, 6);
  c.enableDamping = true;
  c.dampingFactor = 0.08;
  c.update();
}});

function resize() {{
  [['cb', rB], ['ca', rA]].forEach(([id, r]) => {{
    const el = document.getElementById(id);
    const w  = el.parentElement.clientWidth;
    const h  = el.parentElement.clientHeight - 24;
    r.setSize(w, h, false);
  }});
  const w = cb.parentElement.clientWidth;
  const h = cb.parentElement.clientHeight - 24;
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}}
window.addEventListener('resize', resize);
resize();

// ── Load STLs ─────────────────────────────────────────────────────────────────
const loader    = new STLLoader();
let   loaded    = 0;
const total     = PIECES.length * 2;
const statusEl  = document.getElementById('status');

function setStatus(msg) {{ statusEl.innerHTML = msg; }}

function loadMesh(url, color, scene) {{
  loader.load(url,
    geo => {{
      geo.computeVertexNormals();
      scene.add(new THREE.Mesh(geo,
        new THREE.MeshPhongMaterial({{ color, specular: 0x222222, shininess: 40 }})));
      if (++loaded === total) setStatus('Loaded — drag to orbit');
      else setStatus(`<span class="sp"></span> ${{loaded}} / ${{total}}`);
    }},
    undefined,
    () => {{
      if (++loaded >= total) setStatus('Done (some STLs missing)');
    }}
  );
}}

for (const idx of PIECES) {{
  loadMesh(`stl/piece_${{idx}}.stl`,         0x607d8b, sBefore);
  loadMesh(`stl/preview/piece_${{idx}}.stl`, 0x2ecc71, sAfter);
}}

// ── Render loop ────────────────────────────────────────────────────────────────
(function animate() {{
  requestAnimationFrame(animate);
  ctrlB.update();
  ctrlA.update();
  rB.render(sBefore, camera);
  rA.render(sAfter,  camera);
}})();
</script>
</body>
</html>"""

    PREVIEW_HTML.write_text(html, encoding="utf-8")
    return PREVIEW_HTML


# ── Public entry point ────────────────────────────────────────────────────────

def run(plan: dict, open_browser: bool = True, verbose: bool = False) -> bool:
    """
    Full previs flow: diff → render → html → (open browser).
    Returns True on success (even if some pieces failed to render).
    """
    show_diff(plan)

    pieces = plan.get("pieces_to_rerender", [])
    if not pieces:
        print("[previewer] No geometry changes — skipping preview render.", file=sys.stderr)
        return True

    print(f"[previewer] Rendering {len(pieces)} preview piece(s)…", file=sys.stderr)
    result = render_preview_stls(plan, verbose=verbose)

    html_path = generate_preview_html(plan, result)
    url = "http://localhost:3456/preview.html"
    print(f"[previewer] Preview ready → {url}")

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    if result.get("errors"):
        print(f"[previewer] {len(result['errors'])} piece(s) failed to render: "
              f"{[e['piece'] for e in result['errors']]}", file=sys.stderr)

    return True


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Previewer — show before/after for a plan.")
    parser.add_argument("plan_json", help="Path to plan JSON file.")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    plan = json.loads(Path(args.plan_json).read_text())
    run(plan, open_browser=not args.no_browser, verbose=args.verbose)
