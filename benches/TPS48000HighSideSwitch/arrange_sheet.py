#!/usr/bin/env python3
"""Placement generator for the TPS48000HighSideSwitch bench sheets (re-runnable, deterministic UUIDs).

    python3 benches/TPS48000HighSideSwitch/arrange_sheet.py [--eda DIR]

Reads eda/TPS.kicad_sch (the block's reference sheet) and eda/TPS48000HighSideSwitch_bench.kicad_sch (the root) as
`pcb apply schematic` generated them (or as an earlier run left them), and rewrites placement, wiring, labels, notes,
group boxes, paper and title blocks. Parts are selected by their Zener name (the `Path` property), never by position;
every coordinate is computed from symbol pin geometry. Nothing here changes connectivity: gate it with the netlist
diff (`netcmp.py`) after `pcb apply`.

Helpers come from the zener-library tools; set ZL_TOOLS to the directory holding place.py, overlap.py, kicad_sexpr.py
(default: ../zener-library-tools/tools/schematic next to this repo's parent). Numbers on the sheets come from
notes_rules.py (templates filled from values.json); the notes script keeps them current.
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ZL = os.environ.get("ZL_TOOLS", str(REPO.parent / "zener-library-tools" / "tools" / "schematic"))
sys.path.insert(0, ZL)
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "tools" / "schematic"))
import kicad_sexpr as kx  # noqa: E402
import notes_rules  # noqa: E402
import overlap  # noqa: E402
import passive_text as pt  # noqa: E402
import place  # noqa: E402
from place import Sheet  # noqa: E402

NS = "circuit-blocks/TPS48000HighSideSwitch"
DATE, REV, COMPANY, AUTHOR = "2026-11-16", "A", "Bryce Salmi Consulting", "Bryce Salmi"
G = 1.27
FIELD = 1.524  # reference / value text size on the parts


def tw(text, size=place.FONT):
    """Estimated width of one line of text in mm (overlap.py's calibrated table)."""
    return 1.07 * max(overlap.text_width(line, size) for line in text.split("\n"))  # +7 %: renders run wider


def g(n):
    """n grid units of 1.27 mm."""
    return round(n * G, 4)


class Sketch:
    """Thin wrapper: parts by Zener name, placement by pin, wires and labels with auto keys."""

    def __init__(self, path):
        self.sh = Sheet.load(path, NS + "/" + Path(path).stem)
        self.path = path
        self.ref = {}
        for s in self.sh.symbols():
            p = s._prop("Path")
            if p is not None:
                self.ref[p.atoms()[2].value.split(".")[1]] = s.ref
        self.n = 0

    def key(self, kind):
        self.n += 1
        return "%s%d" % (kind, self.n)

    def pt(self, name, pin):
        return self.sh.pin_point(self.ref[name], pin)

    def put(self, name, pin, x, y, rot, mirror=None):
        """Place part `name` so that its pin `pin` lands on (x, y)."""
        s = self.sh.symbol(self.ref[name])
        p = self.sh.lib_pins(s.lib_id, s.unit)[pin]
        ox, oy = place.pin_endpoint((p.x, p.y), (0, 0), rot, mirror)
        self.sh.place_symbol(self.ref[name], round(x - ox, 4), round(y - oy, 4), rot=rot, mirror=mirror)

    def field(self, name, which, x, y, justify=None, angle=0):
        s = self.sh.symbol(self.ref[name])
        if s.rot == 180:  # KiCad reads a stored 180 on a 180-degree part upside down: store 0 (verified in a render)
            angle = 180
        self.sh.set_field(self.ref[name], which, round(x - s.at[0], 4), round(y - s.at[1], 4), angle, justify)

    def fields_above(self, name, cx, y):
        """Reference over value, centred on cx, value just above the part at y."""
        self.field(name, "Reference", cx, y - 2.3)
        self.field(name, "Value", cx, y)

    def fields_right(self, name, x, ymid):
        self.field(name, "Reference", x, ymid - 1.3, "left")
        self.field(name, "Value", x, ymid + 1.3, "left")

    def wire(self, *pts):
        self.sh.wire([tuple(p) for p in pts], self.key("w"))

    def junction(self, x, y):
        self.sh.junction(x, y, self.key("j"))

    def label(self, text, x, y, direction):
        self.sh.label(text, x, y, direction, self.key("l"))

    def hlabel(self, text, x, y, direction):
        self.sh.hlabel(text, x, y, direction, self.key("h"))

    def nc(self, x, y):
        self.sh.no_connect(x, y, self.key("n"))

    def note(self, text, x, y, justify="left top"):
        self.sh.text(text, x, y, self.key("t"), justify)

    def box(self, x1, y1, x2, y2, title):
        self.sh.group_box(x1, y1, x2, y2, title, self.key("b"))

    def stub_h(self, x_pin, y, x_end, name, side):
        """Horizontal labelled stub from (x_pin, y) to x_end; the label sits on the wire at the free end."""
        self.wire((x_pin, y), (x_end, y))
        self.label(name, x_end, y, 0 if side < 0 else 180)

    def clear(self):
        self.sh.clear(kinds=place.SIDE_KINDS + ("text", "rectangle"))

    def drop_power_symbols(self):
        for s in self.sh.symbols():
            if self.sh.lib_symbol(s.lib_id).get("power") is not None:
                self.sh.remove_symbol(s.ref)


def fan(sk, uref, pin, side, jog, shift, x_end, name):
    """Labelled stub from an IC pin: out `jog` mm, vertical `shift`, out to x_end, label at the free end.
    side -1 = left of the IC, +1 = right."""
    px, py = sk.pt(uref, pin)
    xj = round(px + side * jog, 4)
    if shift == 0:
        sk.wire((px, py), (x_end, py))
        yy = py
    else:
        yy = round(py + shift, 4)
        sk.wire((px, py), (xj, py), (xj, yy), (x_end, yy))
    if name in HPORTS:
        sk.hlabel(name, x_end, yy, 180 if side < 0 else 0)
    else:
        stub_end(sk, name, x_end, yy, side)


PORT_NETS = ("BUS_IN", "BUS_OUT", "FAULT_N", "PGOOD_N")  # pcb apply manages a port label per pin use (see report)


def stub_end(sk, name, x, y, side):
    """Label at the free end of a stub. A net that is a block port ends in a hierarchical label: `pcb apply` deletes a
    local or global label that carries a port's name and re-adds its own hierarchical label or power symbol at the
    pin (probed 2026-11-16, pcb 0.4.102), so port nets cannot use local labels. Other nets: local label on the wire."""
    if name in PORT_NETS:
        sk.hlabel(name, x, y, 180 if side < 0 else 0)
    else:
        sk.label(name, x, y, 0 if side < 0 else 180)


def title_block(sh, title, desc, subdir):
    sh.set_paper(place.PAPER_TABLOID)
    sh.set_title_block(
        title=title,
        rev=REV,
        date=DATE,
        company=COMPANY,
        comments={1: place.repo_comment(str(REPO), subdir), 2: desc, 3: "Author: " + AUTHOR},
    )


# Passive component text stacks: tools/schematic/passive_text.py (shared with the other benches)


def _expected(p):
    """Analysis inputs (params.json) each decoded passive must equal."""
    return {
        "R_TOP": p["r_top_ohm"], "R_MID": p["r_mid_ohm"], "R_BOT": p["r_bot_ohm"], "R_ISCP": p["riscp_ohm"],
        "R_TMR": p["rtmr_ohm"], "R_GATE": p["r1_ohm"], "R_DAMP": p["r2_ohm"], "SHUNT": p["rshunt_ohm"],
        "R_FAULT": p["r_fault_pullup_ohm"], "R_PGOOD": p["r_pgood_pullup_ohm"],
        "C_TMR": p["ctmr_f"], "C_GATE": p["c1_f"], "C_BST": p["cbst_f"], "C_VS": p["cbyp_f"], "C_SRC": p["cbyp_f"],
    }


# ---------------------------------------------------------------------------------------------------------------
# Block (child) sheet
# ---------------------------------------------------------------------------------------------------------------
PITCH = pt.PITCH
HPORTS = ("EN", "PGOOD_N", "FAULT_N")  # ports drawn once, as hierarchical labels, at the controller pin wire


def stack(sk, name, x, ymid, expected):
    """Passive text stack of part `name` (see passive_text.stack); returns its lines."""
    return pt.stack(sk.sh, sk.sh.symbol(sk.ref[name]), x, ymid, expected[name], sk.note)


def child(path, v, f):
    sk = Sketch(path)
    sh = sk.sh
    sk.drop_power_symbols()
    sk.clear()
    title_block(sh, "TPS48000HighSideSwitch", "Reference sheet: TPS48000 high-side switch, on-block shunt, latch-off, status",
                "blocks/TPS48000HighSideSwitch")
    rf = sk.ref
    fl = notes_rules.fields(v, f)
    bn = {k: t.format(**fl) for k, t in notes_rules.BLOCK_NOTES.items()}
    expected = _expected(json.load(open(HERE / "params.json")))
    lab = lambda n: n if rf[n] == n else "%s (%s)" % (rf[n], n)  # "R4 (R_GATE)": designator first, Zener name second
    us = sh.symbol(rf["U"])
    pn = {p.name: n for n, p in sh.lib_pins(us.lib_id, us.unit).items()}  # pin number by pin name
    st = lambda name, x, ymid: stack(sk, name, x, ymid, expected)

    YP = g(30)  # power path height
    T2 = g(60)  # top of the second tier of boxes
    UX, UY = g(150), g(86)  # controller centre
    U = "U"

    # ================= POWER PATH ==========================================================================
    x_bin, x_sh, x_q, x_bout = g(32), g(84), g(200), g(284)
    sk.hlabel("BUS_IN", x_bin, YP, 180)
    sk.put("SHUNT", "4", x_sh - 12.7, YP, 0, "y")  # I1 left, E pins below
    sk.wire((x_bin, YP), sk.pt("SHUNT", "4"))
    sk.put("Q1", "5", x_q - 7.62, YP, 90)  # drain left, source right, gate below
    sk.wire(sk.pt("SHUNT", "1"), sk.pt("Q1", "5"))
    sk.label("TPS.DRAIN", x_q - 30.48, YP, 0)
    sk.wire(sk.pt("Q1", "1"), (x_bout, YP))
    sk.hlabel("BUS_OUT", x_bout, YP, 0)
    x, y = sk.pt("SHUNT", "3")
    sk.wire((x, y), (x, y + 12.7))  # one straight leg: pcb apply drops a leg of a bent stub at these pins
    sk.label("TPS.CS_P", x, y + 12.7, 90)
    x, y = sk.pt("SHUNT", "2")
    sk.wire((x, y), (x, y + 12.7))
    sk.label("TPS.CS_N", x, y + 12.7, 90)
    xg, yg = sk.pt("Q1", "4")
    sk.wire((xg, yg), (xg, yg + 12.7))
    sk.label("TPS.GATE", xg, yg + 12.7, 90)
    st("SHUNT", x_sh - 6.35, YP - 11.05)
    sk.field("Q1", "Reference", x_q + 9.0, YP + 5.0, "left")
    sk.field("Q1", "Value", x_q + 9.0, YP + 7.5, "left")
    sk.note("%s: 4-terminal Kelvin, value set per instance\n"
            "CS+ at the BUS_IN-side tap, CS- at the Q1 drain (DS Fig 7-5, p.16)" % lab("SHUNT"),
            x_sh + 20.32, YP - 2.8, "left bottom")
    sk.note("%s: high-side N-FET, source = SRC = BUS_OUT\n"
            "No reverse blocking, no TVS fitted" % lab("Q1"), x_q - 70.0, YP + 3.2)
    rl = notes_rules.lines_text(notes_rules.RATING_LINES, v, f)
    rw = max(tw(l) for l in rl)
    pw_r = x_bout + 20.32
    rx2 = pw_r - 3.81
    rx1 = rx2 - rw - 8.0
    rt = YP + 6.0
    rb = rt + len(rl) * PITCH + 11.0
    sk.box(rx1, rt, rx2, rb, "LOAD TURN-ON RATING")
    sk.note("\n".join(rl), rx1 + 3.81, rt + 6.8)
    pw_top, pw_bot = YP - 20.0, rb + 3.81
    sk.box(g(12), pw_top, pw_r, pw_bot, "POWER PATH")

    # ================= CONTROLLER ==========================================================================
    sk.put(U, "1", UX - 27.94, UY - 10.16, 0)
    pxl, pxr = UX - 27.94, UX + 27.94
    x_endL, x_endR = round(pxl - 22.86, 4), round(pxr + 30.48, 4)
    x_gnd = round(x_endL - 6.35, 4)
    for pin, name, jog, shift in (
        ("1", "TPS.EN_NODE", 2.54, -10.16),
        ("2", "TPS.OV_NODE", 5.08, -7.62),
        ("3", "EN", 7.62, -5.08),
        ("8", "TPS.ISCP_NET", 5.08, 2.54),
        ("9", "TPS.TMR_NET", 2.54, 5.08),
    ):
        fan(sk, U, pin, -1, jog, shift, x_endL, name)
    # PGOOD_N and FAULT_N: one wire group each, from the controller pin into the STATUS / CONTROL box, with the
    # pull-up hanging from the wire (junction) and the single port at the wire end
    x_pgp, x_fp = g(60), g(24)
    x_pp, x_pf = g(77), g(38)
    fan(sk, U, "4", -1, 10.16, -2.54, x_pgp, "PGOOD_N")
    fan(sk, U, "5", -1, 0, 0, x_fp, "FAULT_N")
    for pu, xp, pin, shift in (("R_PGOOD", x_pp, "4", -2.54), ("R_FAULT", x_pf, "5", 0)):
        ys = round(sk.pt(U, pin)[1] + shift, 4)
        sk.put(pu, "1", xp, ys, 270)
        sk.junction(xp, ys)
        x2, y2 = sk.pt(pu, "2")
        sk.wire((x2, y2), (x2, y2 - 7.62))
        if pu == "R_PGOOD":
            sk.hlabel("V3V3", x2, y2 - 7.62, 90)  # the one V3V3 port of the sheet
        else:
            sk.label("V3V3", x2, y2 - 7.62, 270)
        st(pu, xp + 3.2, ys - 6.35)
    x6, y6 = sk.pt(U, "6")
    x7, y7 = sk.pt(U, "7")
    sk.wire((x6, y6), (x_gnd, y6), (x_gnd, y7 + 2.54))
    sk.wire((x7, y7), (x_gnd, y7))
    sk.junction(x_gnd, y7)
    sk.hlabel("GND", x_gnd, y7 + 2.54, 270)  # the one GND port of the sheet
    for pin, name, jog, shift in (
        ("18", "TPS.CS_P", 5.08, -12.7),
        ("17", "TPS.CS_N", 7.62, -10.16),
        ("15", "TPS.PU_NET", 10.16, -7.62),
        ("14", "TPS.GATE", 12.7, -5.08),
        ("12", "TPS.BST_NET", 0, 0),
    ):
        fan(sk, U, pin, 1, jog, shift, x_endR, name)
    x10, y10 = sk.pt(U, "10")
    sk.wire((x10, y10), (x10 + 5.08, y10), (x10 + 5.08, y10 + 5.08))
    sk.label("GND", x10 + 5.08, y10 + 5.08, 90)
    for pin in ("11", "19"):
        sk.nc(*sk.pt(U, pin))
    sk.field(U, "Reference", UX, UY - 20.1)
    sk.field(U, "Value", UX, UY - 22.7)
    # bypass capacitors hang from the wire of the pin they decouple (one port flag per wire group)
    for name, pin, net, jog, shift in (("C_VS", "20", "BUS_IN", 2.54, -12.7), ("C_SRC", "13", "BUS_OUT", 15.24, -2.54)):
        px, py = sk.pt(U, pin)
        xj, yy = round(px + jog, 4), round(py + shift, 4)
        xc, xw = round(x_endR + 5.08, 4), round(x_endR + 10.16, 4)
        sk.wire((px, py), (xj, py), (xj, yy), (xw, yy))
        sk.junction(xc, yy)
        sk.hlabel(net, xw, yy, 0)
        sk.wire((xc, yy), (xc, yy + 5.08))
        sk.put(name, "1", xc, yy + 5.08, 270)
        x2, y2 = sk.pt(name, "2")
        sk.wire((x2, y2), (x2, y2 + 5.08))
        sk.label("GND", x2, y2 + 5.08, 90)
        st(name, xc + 3.2, yy + 5.08 + 3.8)
    ctl_l, ctl_r = g(97), place.snap(x_endR + 28.0)
    ctl_top = T2
    sk.note("EN: active high, 3.3 V or 5 V logic (V(INP_H) max 2 V)\nEN low-high toggle clears a latch (DS p.19)",
            ctl_l + 3.81, T2 + 4.6)
    notes_c = ("%s: %s VS pin %s, place at pin\n"
               "%s: %s SRC pin %s, place at pin\n"
               "DS p.5: VS, SRC to GND >= 22 nF\n"
               "CS_SEL to GND: high-side sensing (DS p.3, Table 4-1); SCP_TEST to GND; pins 11, 19 no connect"
               % (lab("C_VS"), rf["U"], pn["VS"], lab("C_SRC"), rf["U"], pn["SRC"]))
    ync = UY + 24.0
    sk.note(notes_c, ctl_l + 3.81, ync)
    ctl_bot = ync + 4 * PITCH + 3.5
    sk.box(ctl_l, ctl_top, ctl_r, ctl_bot, "CONTROLLER")

    # ================= STATUS / CONTROL (left column, beside the controller) ================================
    uv_l, uv_r = g(12), g(94)
    ynote = UY + 6.0
    sk.note("PGOOD_N: open drain, low = Q1 fully enhanced\nFAULT_N: open drain, low on short, UVLO, OV or SCP\n"
            "Pull-ups DNP if the host supplies them", g(15), ynote)
    st_b = ynote + 3 * PITCH + 3.5
    sk.box(uv_l, T2, uv_r, st_b, "STATUS / CONTROL")

    # ================= UVLO / OV (left column, vertical ladder) =============================================
    XL = g(34)
    uv_t = place.snap(st_b + 5.08)
    y0 = place.snap(uv_t + 14.0)
    sk.wire((XL, y0), (XL, y0 - 5.08), (XL + 17.78, y0 - 5.08))
    stub_end(sk, "BUS_IN", XL + 17.78, y0 - 5.08, 1)
    y = y0
    for i, (name, net) in enumerate((("R_TOP", "TPS.EN_NODE"), ("R_MID", "TPS.OV_NODE"), ("R_BOT", None))):
        sk.put(name, "1", XL, y, 90)
        x2, y2 = sk.pt(name, "2")
        st(name, XL - 12.7, (y + y2) / 2)
        if net:
            sk.wire((x2, y2), (x2, y2 + 5.08))
            sk.junction(x2, y2)
            sk.wire((x2, y2), (x2 + 17.78, y2))
            sk.label(net, x2 + 17.78, y2, 180)
            y = round(y2 + 5.08, 4)
        else:
            sk.wire((x2, y2), (x2, y2 + 5.08))
            sk.label("GND", x2, y2 + 5.08, 90)
            ygnd = y2 + 5.08
    sk.note("UVLO / OV ladder: DS Fig 7-5, p.16, eq. 12, 13", g(15), ygnd + 6.0)
    uv_b = ygnd + 6.0 + 2.0 + 3.5
    sk.box(uv_l, uv_t, uv_r, uv_b, "UVLO / OV")

    # ================= SOFT START / GATE DRIVE (right) =====================================================
    soft_l = ctl_r + 5.08
    xg0 = soft_l + 7.62
    y1 = g(76)
    y2_ = y1 + 7.62
    xr1 = xg0 + 17.78
    sk.wire((xg0, y1), (xr1, y1))
    sk.label("TPS.PU_NET", xg0, y1, 0)
    sk.put("R_GATE", "1", xr1, y1, 0, "y")
    xa, _ = sk.pt("R_GATE", "2")
    x_pd, x_n = round(xa + 6.35, 4), round(xa + 12.7, 4)
    x_gl = round(x_n + 25.4, 4)
    sk.wire((xa, y1), (x_gl, y1))
    sk.label("TPS.GATE", x_gl, y1, 180)
    sk.junction(x_pd, y1)
    sk.junction(x_n, y1)
    sk.wire((xg0, y2_), (x_pd, y2_), (x_pd, y1))
    sk.label("TPS.GATE", xg0, y2_, 0)
    st("R_GATE", xr1, y1 - 5.0 - 1.5 * PITCH)
    sk.wire((x_n, y1), (x_n, y1 + 5.08))
    sk.put("R_DAMP", "1", x_n, y1 + 5.08, 90)
    xb, yb = sk.pt("R_DAMP", "2")
    st("R_DAMP", x_n + 3.2, (y1 + 5.08 + yb) / 2)
    yc1 = round(yb + 5.08, 4)
    sk.wire((xb, yb), (xb, yc1))
    sk.junction(xb, round(yb + 2.54, 4))
    sk.wire((xb, round(yb + 2.54, 4)), (xb - 17.78, round(yb + 2.54, 4)))
    sk.label("TPS.C1_NODE", xb - 17.78, round(yb + 2.54, 4), 0)
    sk.put("C_GATE", "1", x_n, yc1, 270)
    xc2, yc2 = sk.pt("C_GATE", "2")
    sk.wire((xc2, yc2), (xc2, yc2 + 5.08))
    sk.label("GND", xc2, yc2 + 5.08, 90)
    st("C_GATE", x_n + 3.2, yc1 + 3.8)
    gate_lines = (bn["gate"], bn["damp"], "PD tied to the gate: fast turn-off (DS p.16)")
    y_gn = yc2 + 5.08 + 6.0
    sk.note("\n".join(gate_lines), xg0, y_gn)
    gate_b = y_gn + 3 * PITCH + 3.5
    # bootstrap capacitor: its own small group, BST to SRC
    bst_t = place.snap(gate_b + 5.08)
    yrow = place.snap(bst_t + 12.0)
    sk.wire((xg0, yrow), (xg0 + 17.78, yrow))
    sk.label("TPS.BST_NET", xg0, yrow, 0)
    sk.put("C_BST", "1", xg0 + 17.78, yrow, 0)
    xe, _ = sk.pt("C_BST", "2")
    sk.wire((xe, yrow), (xe + 17.78, yrow))
    stub_end(sk, "BUS_OUT", xe + 17.78, yrow, 1)
    st("C_BST", xg0 + 17.78, yrow + 3.5 + 2.0 * PITCH)
    y_bn = yrow + 4.0 + 5 * PITCH + 3.0
    sk.note(bn["bst"] + "\n" + bn["tdrv"], xg0, y_bn)
    bst_b = y_bn + 2 * PITCH + 3.5
    soft_r = round(max(tw(l) for l in gate_lines + (bn["bst"], bn["tdrv"])) + xg0 + 10.0, 4)
    sk.box(soft_l, T2, soft_r, gate_b, "SOFT START / GATE DRIVE")
    sk.box(soft_l, bst_t, soft_r, bst_b, "BOOTSTRAP")

    # ================= SHORT-CIRCUIT / LATCH =================================================================
    lt_t = place.snap(ctl_bot + 5.08)
    ytw = lt_t + g(10)
    xi = g(116)
    xct = round(xi + g(26), 4)
    xrt = round(xct + g(26), 4)
    ygb = ytw + 17.78
    sk.put("R_ISCP", "1", xi, ytw, 90)
    sk.wire((xi, ytw), (xi - 17.78, ytw))
    sk.label("TPS.ISCP_NET", xi - 17.78, ytw, 0)
    sk.wire((xi, ytw + 12.7), (xi, ygb))
    sk.label("GND", xi, ygb, 90)
    st("R_ISCP", xi + 3.2, ytw + 6.35)
    sk.put("C_TMR", "1", xct, ytw, 270)
    sk.put("R_TMR", "1", xrt, ytw, 90)
    sk.wire((xct, ytw), (xrt + 25.4, ytw))
    sk.junction(xrt, ytw)
    sk.label("TPS.TMR_NET", xrt + 25.4, ytw, 180)
    sk.wire((xct, ytw + 7.62), (xct, ygb))
    sk.label("GND", xct, ygb, 90)
    sk.wire((xrt, ytw + 12.7), (xrt, ygb))
    sk.label("GND", xrt, ygb, 90)
    st("C_TMR", xct + 3.2, ytw + 6.6)
    st("R_TMR", xrt + 3.2, ytw + 6.35)
    sk.note(bn["scp"] + "\n" + bn["tmr"], ctl_l + 3.81, ygb + 6.0)
    lt_b = ygb + 6.0 + 2 * PITCH + 3.5
    sk.box(ctl_l, lt_t, ctl_r, lt_b, "SHORT-CIRCUIT / LATCH")
    sk.sh.save()
    return {"ctl": (ctl_l, ctl_top, ctl_r, ctl_bot), "soft": (soft_l, T2, soft_r, gate_b), "bst": (bst_t, bst_b),
            "latch_b": lt_b, "left_b": uv_b, "power": (pw_top, pw_bot)}


# ---------------------------------------------------------------------------------------------------------------
# Root (bench) sheet: block diagram of one block instance
# ---------------------------------------------------------------------------------------------------------------
VBUS_IN_AS_LABEL = os.environ.get("VBUS_IN_AS_LABEL", "1") == "1"


def set_sheet_pin(sheet, name, x, y, angle):
    pin = [p for p in sheet.nodes("pin") if p.args()[0] == name][0]
    pin.get("at").set_nums(x, y, angle)
    j = pin.get("effects").get("justify")
    j.atoms()[1].raw = {180: "left", 0: "right", 90: "right", 270: "left"}[angle]


def root(path, v, f):
    sk = Sketch.__new__(Sketch)
    sk.sh = Sheet.load(path, NS + "/" + Path(path).stem)
    sk.path, sk.ref, sk.n = path, {}, 0
    sh = sk.sh
    fl = notes_rules.fields(v, f)
    title_block(sh, "TPS48000HighSideSwitch bench", notes_rules.BENCH_DESC.format(**fl), "benches/TPS48000HighSideSwitch")
    pw = {}
    for s_ in sh.symbols():
        if sh.lib_symbol(s_.lib_id).get("power") is not None:
            pw[s_.value] = s_.ref
    sh.clear(kinds=place.SIDE_KINDS + ("text", "rectangle"))
    if VBUS_IN_AS_LABEL and "VBUS_IN" in pw:
        sh.remove_symbol(pw.pop("VBUS_IN"))
    sheet = sh.root.get("sheet")
    x0, y0, w, h = g(140), g(66), g(50), g(40)
    sheet.get("at").set_nums(x0, y0)
    sheet.get("size").set_nums(w, h)
    yp = y0 + 12.7
    xmid = x0 + 38.1
    set_sheet_pin(sheet, "BUS_IN", x0, yp, 180)
    set_sheet_pin(sheet, "BUS_OUT", x0 + w, yp, 0)
    set_sheet_pin(sheet, "EN", x0 + w, yp + 17.78, 0)
    set_sheet_pin(sheet, "PGOOD_N", x0 + w, yp + 22.86, 0)
    set_sheet_pin(sheet, "FAULT_N", x0 + w, yp + 27.94, 0)
    yv = y0 + h - 12.7  # V3V3: lower-left, left edge
    set_sheet_pin(sheet, "V3V3", x0, yv, 180)
    set_sheet_pin(sheet, "GND", xmid, y0 + h, 270)
    for prop, (px, py, just) in {"Sheetname": (x0, y0 - 1.9, "left bottom"), "Sheetfile": (x0, y0 + h + 1.9, "left top")}.items():
        pr = [p for p in sheet.nodes("property") if p.args()[0] == prop][0]
        pr.get("at").set_nums(px, py, 0)
    # rails: power symbols on the parent (up / down)
    xv = x0 - 25.4
    sh.place_symbol(pw["V3V3"], xv, yv)
    sh.wire([(xv, yv), (x0, yv)], "w.v3v3")
    sh.place_symbol(pw["GND"], xmid, y0 + h + 10.16)
    sh.wire([(xmid, y0 + h), (xmid, y0 + h + 10.16)], "w.gnd")
    # power in (left)
    xin = x0 - 76.2
    sk.wire((xin, yp), (x0, yp))
    if VBUS_IN_AS_LABEL:
        sk.label("VBUS_IN", xin, yp, 0)
    else:
        sh.place_symbol(pw["VBUS_IN"], xin, yp)
    nin = notes_rules.lines_text(notes_rules.ROOT_NOTES["in"], v, f)
    sk.note("\n".join(nin[:2]), xin - 2.0, yp - 5.5, "left bottom")
    sk.note("\n".join(nin[2:]), xin - 2.0, yp + 3.2)
    wi = max(tw(l) for l in nin) + 4.0
    sk.box(xin - 5.08, yp - 5.5 - 2 * 2.03 - 7.5, xin - 5.08 + wi + 4.0, yp + 3.2 + 2 * 2.03 + 5.0, "POWER IN")
    # power out (right)
    xo = x0 + w
    xe = round(xo + 45.72, 4)
    sk.wire((xo, yp), (xe, yp))
    sk.label("VBUS_OUT", xe, yp, 180)
    nst = notes_rules.lines_text(notes_rules.ROOT_NOTES["status"], v, f)
    wst = max(tw(l) for l in nst)
    nout = notes_rules.lines_text(notes_rules.ROOT_NOTES["out"], v, f)
    sk.note("\n".join(nout), xe + 5.08, yp - 3.0)
    wo = max(tw(l) for l in nout)
    sk.box(xo + 7.62, yp - 9.0, xe + 5.08 + max(wst, wo) + 4.0, yp + 6.0, "POWER OUT")
    # status / control: EN, PGOOD_N, FAULT_N together
    ys = [yp + 17.78, yp + 22.86, yp + 27.94]
    for y, name in zip(ys, ("SW_EN", "SW_PGOOD_N", "SW_FAULT_N")):
        sk.wire((xo, y), (xe, y))
        sk.label(name, xe, y, 180)
    sk.note("\n".join(nst), xe + 5.08, ys[0] - 3.0)
    sk.box(xo + 7.62, yp + 10.5, xe + 5.08 + max(wst, wo) + 4.0, ys[2] + 7.0, "STATUS / CONTROL")
    # V3V3 note
    nv = notes_rules.lines_text(notes_rules.ROOT_NOTES["v3v3"], v, f)
    sk.note("\n".join(nv), xv - 3.81, yv, "right")
    # design values
    ytop = y0 + h + 18.0
    cols = [notes_rules.lines_text(c, v, f) for c in notes_rules.DV_COLUMNS]
    x = xin - 5.08
    left = x
    xs = []
    x += 3.81
    for c in cols:
        xs.append(x)
        x += max(tw(l) for l in c) + 8.0
    right = x - 8.0 + 3.81
    for xc, c in zip(xs, cols):
        sk.note("\n".join(c), xc, ytop + 6.8)
    nrows = max(len(c) for c in cols)
    sk.box(left, ytop, right, ytop + 6.8 + nrows * 2.03 + 3.5,
           notes_rules.DV_TITLE.format(**fl))
    sh.save()


def check_stacks(path):
    return pt.check_stacks(path, NS + "/" + Path(path).stem, _expected(json.load(open(HERE / "params.json"))))


if __name__ == "__main__":
    eda = HERE / "eda"
    if "--eda" in sys.argv:
        eda = Path(sys.argv[sys.argv.index("--eda") + 1])
    if "--check" in sys.argv:
        problems = check_stacks(str(eda / "TPS.kicad_sch"))
        for p_ in problems:
            print("passive text drift:", p_)
        print("passive text: %s" % ("DRIFT" if problems else "in sync"))
        sys.exit(1 if problems else 0)
    v = json.load(open(HERE / "docs/design-analysis/values.json"))
    f = lambda x, n: f"{x:.{n}f}"
    info = child(str(eda / "TPS.kicad_sch"), v, f)
    root(str(eda / "TPS48000HighSideSwitch_bench.kicad_sch"), v, f)
    print(info)
