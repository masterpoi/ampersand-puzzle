// ══════════════════════════════════════════════════════════════════════════
//  AMPERSAND SHATTER PUZZLE — PIECE-TO-PIECE LAP JOINT VERSION
//  OpenSCAD  |  12 pieces  |  no backing plate  |  16 × M3 × 12 mm
//
//  MECHANISM
//  ─────────────────────────────────────────────────────────────────────
//  Each piece is z-split at TAB_H = 5 mm:
//    Lower zone (z = 0 … TAB_H):   tab slabs extending into neighbours
//    Upper zone (z = TAB_H … PIECE_H):  main body of this piece
//
//  At every shared edge one piece provides a TAB (at z=0..TAB_H) that
//  extends TAB_REACH mm into the adjacent piece's x-y region.
//  The receiving piece has a matching RECESS pocket (slightly enlarged
//  by TAB_GAP) and a countersunk clearance hole through its body.
//  A heat-set insert in the donor tab receives the screw from above.
//
//  SCREW FIT (M3 × 12 mm, DIN 7991)
//  ──────────────────────────────────
//  PIECE_H = 12 mm = screw length → shaft tip reaches z = 0 exactly.
//    Head (flush at z=12)  →  CS cone down to z=9.5  →  clearance shaft
//    through receiver body (z=9.5..5 = 4.5 mm)  →  insert engagement
//    in donor tab (z=5..0 = 5 mm).  Total shaft = 12 mm. ✓
//
//  ASSEMBLY SEQUENCE (topological sort of donor→receiver DAG)
//  ──────────────────────────────────────────────────────────
//  Layer 1 (no incoming tabs):  P0, P4
//  Layer 2:                     P1, P5, P8
//  Layer 3:                     P2, P3, P6, P9
//  Layer 4:                     P7, P10
//  Layer 5 (last):              P11
//
//  Place each piece tab-side-down; screw that piece down completely before
//  laying the next piece.  Insert heat-set inserts into tabs BEFORE assembly.
//
//  RENDER_MODE
//  ───────────
//   "all"      →  all 12 pieces assembled (preview)
//   "exploded" →  spread apart for inspection
//   "piece"    →  single piece → STL (set PIECE_IDX 0–11)
//   "debug"    →  2D overlay: polygons + tab footprints + screw centres
//
//  BATCH EXPORT
//  ────────────
//  for i in $(seq 0 11); do
//    openscad -D "RENDER_MODE=\"piece\";PIECE_IDX=$i" \
//             -o piece_${i}.stl ampersand_lap_joint.scad
//  done
// ══════════════════════════════════════════════════════════════════════════

RENDER_MODE = "all";
PIECE_IDX   = 0;

// ── Dimensions ────────────────────────────────────────────────────────────
PIECE_H   = 12;     // mm — total piece thickness (= M3×12 screw length)
TAB_H     =  5;     // mm — tab height; lower zone z = 0 … TAB_H
TAB_REACH = 15;     // mm — how far each tab protrudes into neighbour's xy
TAB_GAP   =  0.15;  // mm — recess clearance added on all sides of tab

CHAR_SIZE = 178;    // & glyph size
X_OFFSET  =   9;    // nudge glyph to centre in polygon grid
Y_OFFSET  =  18;    // baseline height above z=0 plane
FONT      = "Liberation Serif:style=Bold";
// Fallbacks if Liberation Serif is unavailable on your system:
// FONT = "DejaVu Serif:style=Bold";
// FONT = "FreeSerif:style=Bold";
// FONT = "Georgia:style=Bold";

// ── Hardware ──────────────────────────────────────────────────────────────
SCREW_D      =  3.2;  // M3 shaft clearance Ø (mm)
CS_D         =  6.0;  // M3 DIN 7991 countersunk head Ø (mm)
CS_DEPTH     =  2.5;  // countersink cone depth from top face (mm)
INSERT_D     =  4.6;  // heat-set insert outer Ø (mm)
INSERT_DEPTH =  5.0;  // insert pocket depth = TAB_H (fills full tab zone)

// ── Exploded view ─────────────────────────────────────────────────────────
EXPLODE_SCALE = 1.0;  // 0 = assembled, 1 = default spread

// ══════════════════════════════════════════════════════════════════════════
//  PIECE POLYGONS
//  12 irregular polygons tiling a 200 × 200 mm bounding box.
//  Coordinate origin at bottom-left, Y increases upward (OpenSCAD convention).
//  Piece-4 fix applied (v2): piece 0 extended to absorb left-wall fragment;
//  piece 4 shrunk to small triangle; piece 5 widened to pentagon.
// ══════════════════════════════════════════════════════════════════════════
PIECE_POLYS = [
    /* 0 top-left+mid */ [[  0,  0],[ 45,  0],[ 42, 72],[ 42,115],[  0,115]],
    /* 1 top-ctr-L    */ [[ 45,  0],[ 95,  0],[108, 58],[ 42, 72]],
    /* 2 top-ctr-R    */ [[ 95,  0],[158,  0],[155, 60],[108, 48]],
    /* 3 top-right    */ [[158,  0],[200,  0],[200, 62],[155, 70]],
    /* 4 mid-left     */ [[  0,115],[ 42,115],[ 55,138],[  0,125]],
    /* 5 mid-ctr-L    */ [[ 42, 72],[ 42,115],[ 55,138],[ 92,122],[108, 58]],
    /* 6 mid-ctr-R    */ [[108, 48],[155, 60],[155,135],[ 92,122]],
    /* 7 mid-right    */ [[155, 70],[200, 62],[200,138],[155,135]],
    /* 8 bot-left     */ [[  0,125],[ 55,138],[ 48,200],[  0,200]],
    /* 9 bot-ctr-L    */ [[ 55,138],[ 92,122],[105,200],[ 48,200]],
    /* 10 bot-ctr-R   */ [[ 92,122],[155,135],[145,200],[105,200]],
    /* 11 bot-right   */ [[155,135],[200,138],[200,200],[145,200]],
];

// ══════════════════════════════════════════════════════════════════════════
//  TAB JOINT DEFINITIONS — 16 joints
//
//  Each entry: [donor, receiver, cx, cy, dx, dy, half_w]
//    donor/receiver — piece indices 0–11
//    cx, cy         — midpoint of shared edge (mm, polygon space)
//    dx, dy         — unit vector pointing FROM shared edge INTO receiver
//    half_w         — tab half-width perpendicular to (dx,dy) in mm
//
//  Screw xy = (cx + dx·TAB_REACH/2,  cy + dy·TAB_REACH/2)
//  Connections per piece:
//    P0: 2 out (→P1,→P5)                   P6: 2 out (→P7,→P10) 1 in
//    P1: 2 out (→P2,→P5)  1 in             P7: 1 out (→P11)     2 in
//    P2: 1 out (→P3)      1 in             P8: 1 out (→P9)      1 in
//    P3: 1 out (→P7)      1 in             P9: 1 out (→P10)     2 in
//    P4: 2 out (→P5,→P8)                   P10:1 out (→P11)     2 in
//    P5: 2 out (→P6,→P9)  3 in             P11: —               2 in
// ══════════════════════════════════════════════════════════════════════════
TAB_JOINTS = [
    //  dn  rv    cx      cy      dx       dy    hw
    [  0,  1,  43.5,  36.0,  0.999,  0.042, 10 ],  //  0  P0 → P1
    [  1,  2, 101.5,  29.0,  0.976, -0.218, 10 ],  //  1  P1 → P2
    [  2,  3, 156.5,  35.0,  0.999,  0.043, 10 ],  //  2  P2 → P3
    [  0,  5,  42.0,  93.5,  1.000,  0.000, 10 ],  //  3  P0 → P5  (vertical seam)
    [  1,  5,  75.0,  65.0, -0.196,  0.981,  8 ],  //  4  P1 → P5  (diagonal seam)
    [  4,  5,  48.5, 126.5,  0.870, -0.492,  7 ],  //  5  P4 → P5
    [  5,  6, 100.0,  90.0,  0.970,  0.242, 10 ],  //  6  P5 → P6
    [  3,  7, 177.5,  66.0,  0.175,  0.985, 10 ],  //  7  P3 → P7
    [  6,  7, 155.0, 102.5,  1.000,  0.000, 10 ],  //  8  P6 → P7
    [  5,  9,  71.5, 130.0,  0.396,  0.918, 10 ],  //  9  P5 → P9
    [  6, 10, 123.5, 128.5, -0.202,  0.979, 10 ],  // 10  P6 → P10
    [  7, 11, 177.5, 136.5, -0.067,  0.998, 10 ],  // 11  P7 → P11
    [  8,  9,  51.5, 169.0,  0.994,  0.112, 10 ],  // 12  P8 → P9
    [  9, 10,  98.5, 161.0,  0.986, -0.164, 10 ],  // 13  P9 → P10
    [ 10, 11, 150.0, 167.5,  0.988,  0.152, 10 ],  // 14  P10 → P11
    [  4,  8,  27.5, 131.5, -0.230,  0.973,  7 ],  // 15  P4 → P8
];

// ── Explode offset vectors [dx, dy] per piece ──────────────────────────────
EXPLODE_VEC = [
    [-20,-12], [ -5,-22], [  8,-22], [ 22,-20],
    [-22,  8], [ -6,  0], [  8, -2], [ 22,  0],
    [-22, 20], [ -5, 22], [  8, 22], [ 22, 20],
];


// ══════════════════════════════════════════════════════════════════════════
//  MODULES
// ══════════════════════════════════════════════════════════════════════════

// ── & glyph ───────────────────────────────────────────────────────────────
module amp_2d() {
    translate([X_OFFSET, Y_OFFSET])
        text("&", size=CHAR_SIZE, font=FONT,
             halign="left", valign="baseline");
}

// Extruded & solid, slightly taller than PIECE_H for clean booleans.
module amp_solid() {
    linear_extrude(height = PIECE_H + 2)
        amp_2d();
}

// ── Tab geometry ───────────────────────────────────────────────────────────

// 2D footprint: rectangle starting at (cx,cy) and extending TAB_REACH
// in direction (dx,dy), with total width 2·half_w.
module tab_footprint_2d(cx, cy, dx, dy, hw) {
    angle = atan2(dy, dx);
    translate([cx, cy])
    rotate([0, 0, angle])
    polygon([
        [0,        -hw],
        [TAB_REACH, -hw],
        [TAB_REACH,  hw],
        [0,          hw],
    ]);
}

// 3D tab slab added to donor piece (z = 0 … TAB_H).
// Clipped to amp_solid so tabs don't protrude into character counters.
module tab_slab(cx, cy, dx, dy, hw) {
    intersection() {
        linear_extrude(height = TAB_H)
            tab_footprint_2d(cx, cy, dx, dy, hw);
        amp_solid();
    }
}

// 3D recess pocket subtracted from receiver piece (z = 0 … TAB_H + overrun).
// Enlarged by TAB_GAP on all sides for sliding fit.
module recess_pocket(cx, cy, dx, dy, hw) {
    linear_extrude(height = TAB_H + 0.4)
        offset(delta = TAB_GAP)
        tab_footprint_2d(cx, cy, dx, dy, hw);
}

// ── Screw geometry ─────────────────────────────────────────────────────────
// Screw xy sits at TAB_REACH/2 from the shared-edge midpoint — the centre
// of the tab in the receiver's territory.
function screw_x(cx, dx) = cx + dx * TAB_REACH / 2;
function screw_y(cy, dy) = cy + dy * TAB_REACH / 2;

// Countersink + clearance shaft through the receiver's body (top → z=0).
module screw_clearance(cx, cy, dx, dy) {
    sx = screw_x(cx, dx);
    sy = screw_y(cy, dy);
    translate([sx, sy, PIECE_H - CS_DEPTH]) {
        // Countersink cone
        cylinder(h = CS_DEPTH + 0.2, d1 = SCREW_D, d2 = CS_D, $fn=32);
        // Full-depth clearance shaft to z = 0
        translate([0, 0, -PIECE_H])
            cylinder(h = PIECE_H, d = SCREW_D, $fn=32);
    }
}

// Insert pocket in donor tab (opens from z = 0, the bottom face of the tab).
module insert_pocket(cx, cy, dx, dy) {
    sx = screw_x(cx, dx);
    sy = screw_y(cy, dy);
    translate([sx, sy, -0.1])
        cylinder(h = INSERT_DEPTH + 0.2, d = INSERT_D, $fn=32);
}

// ── Main piece ─────────────────────────────────────────────────────────────
module piece(idx) {
    difference() {
        union() {
            // ① Body: & glyph clipped to this piece's polygon region.
            intersection() {
                linear_extrude(height = PIECE_H)
                    offset(delta = 0.02)
                    polygon(PIECE_POLYS[idx]);
                amp_solid();
            }
            // ② Outgoing tabs: protrude into neighbours at z = 0 … TAB_H.
            for (j = TAB_JOINTS) {
                if (j[0] == idx) {
                    tab_slab(j[2], j[3], j[4], j[5], j[6]);
                }
            }
        }
        // ③ Recess pockets: where incoming tabs from neighbours slot in.
        for (j = TAB_JOINTS) {
            if (j[1] == idx) {
                recess_pocket(j[2], j[3], j[4], j[5], j[6]);
            }
        }
        // ④ Clearance holes: for incoming screws through this body.
        for (j = TAB_JOINTS) {
            if (j[1] == idx) {
                screw_clearance(j[2], j[3], j[4], j[5]);
            }
        }
        // ⑤ Insert pockets: in outgoing tabs, opening from z = 0.
        for (j = TAB_JOINTS) {
            if (j[0] == idx) {
                insert_pocket(j[2], j[3], j[4], j[5]);
            }
        }
    }
}


// ══════════════════════════════════════════════════════════════════════════
//  RENDER DISPATCH
// ══════════════════════════════════════════════════════════════════════════

if (RENDER_MODE == "all") {
    for (i = [0:11]) piece(i);
}

else if (RENDER_MODE == "exploded") {
    for (i = [0:11]) {
        ev = EXPLODE_VEC[i];
        translate([ev[0] * EXPLODE_SCALE * 25/22,
                   ev[1] * EXPLODE_SCALE * 25/20, 0])
            piece(i);
    }
}

else if (RENDER_MODE == "piece") {
    piece(PIECE_IDX);
}

else if (RENDER_MODE == "debug") {
    // Blue = & silhouette,  Red = polygon outlines
    // Gold = screw centres,  White = shared-edge midpoints
    color("DeepSkyBlue", 0.35) amp_2d();
    for (i = [0:11]) color("Tomato", 0.18) polygon(PIECE_POLYS[i]);
    for (j = TAB_JOINTS) {
        // Shared-edge midpoint (white dot)
        color("White", 0.55)
            translate([j[2], j[3]]) circle(d=3, $fn=16);
        // Screw centre (gold circle at TAB_REACH/2 into receiver)
        color("Gold", 0.85)
            translate([screw_x(j[2],j[4]), screw_y(j[3],j[5])])
            circle(d=CS_D, $fn=32);
        // Tab footprint outline
        color("Cyan", 0.25)
            tab_footprint_2d(j[2], j[3], j[4], j[5], j[6]);
    }
}
