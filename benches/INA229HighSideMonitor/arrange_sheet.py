#!/usr/bin/env python3
"""Placement generator for the INA229HighSideMonitor bench sheets (re-runnable, deterministic UUIDs).

    python3 benches/INA229HighSideMonitor/arrange_sheet.py [--eda DIR]      write placement, wiring, notes, boxes
    python3 benches/INA229HighSideMonitor/arrange_sheet.py --check [--eda DIR]   passive text stacks vs the library

Reads eda/INA.kicad_sch (the block's reference sheet) and eda/INA229HighSideMonitor_bench.kicad_sch (the root) as
`pcb apply schematic` generated them (or as an earlier run left them), and rewrites placement, wiring, labels, notes,
group boxes, paper and title blocks. Parts are selected by their Zener name (the `Path` property), never by position;
every coordinate is computed from symbol pin geometry. Nothing here changes connectivity: gate it with the netlist
diff (netcmp.py) after `pcb apply`. Same structure as benches/TPS48000HighSideSwitch/arrange_sheet.py.

Helpers come from the zener-library tools; set ZL_TOOLS to the directory holding place.py, overlap.py, kicad_sexpr.py
(default: ../zener-library/tools/schematic next to this repo, the v0.4.0 release checkout). Passive text: tools/schematic/passive_text.py.
Numbers on the sheets come from notes_rules.py (templates filled from values.json); update_notes.py keeps them current.

Layout of the block sheet (INA229 mirrored in x: SPI pins face the SPI box, supply and sense pins face the sense taps):
the INA229 pins on the right are CS, MOSI, ALERT, MISO, SCLK (top to bottom). The fixed bus order is CS, SCLK, MOSI,
MISO, so SCLK breaks its run with a flagged stub at the IC and re-enters the SPI box as its own wire; ALERT_N is a
labelled stub at the IC and has its own status box. pcb apply deletes a local label whose text differs from the board
net's name (SPI.*, BUS_*), so those nets use hierarchical-label flags; ALERT_N, V3V3 and GND keep local labels.
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ZL = os.environ.get("ZL_TOOLS", str(REPO.parent / "zener-library" / "tools" / "schematic"))
sys.path.insert(0, ZL)
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "tools" / "schematic"))
import notes_rules  # noqa: E402
import overlap  # noqa: E402
import passive_text as pt  # noqa: E402
import place  # noqa: E402
from place import Sheet  # noqa: E402

NS = "circuit-blocks/INA229HighSideMonitor"
DATE, REV, COMPANY, AUTHOR = "2026-11-17", "A", "Bryce Salmi Consulting", "Bryce Salmi"
G = 1.27
PITCH = pt.PITCH


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
        self.ref = pt.refs_by_name(self.sh)
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

    def wire(self, *pts):
        self.sh.wire([tuple(p) for p in pts], self.key("w"))

    def junction(self, x, y):
        self.sh.junction(x, y, self.key("j"))

    def label(self, text, x, y, direction):
        self.sh.label(text, x, y, direction, self.key("l"))

    def hlabel(self, text, x, y, direction):
        self.sh.hlabel(text, x, y, direction, self.key("h"))

    def note(self, text, x, y, justify="left top"):
        self.sh.text(text, x, y, self.key("t"), justify)

    def box(self, x1, y1, x2, y2, title):
        self.sh.group_box(x1, y1, x2, y2, title, self.key("b"))

    def clear(self):
        self.sh.clear(kinds=place.SIDE_KINDS + ("text", "rectangle"))

    def drop_power_symbols(self):
        for s in self.sh.symbols():
            if self.sh.lib_symbol(s.lib_id).get("power") is not None:
                self.sh.remove_symbol(s.ref)

    def fan(self, pin, side, jog, shift, x_end):
        """Wire from IC pin `pin`: out `jog` mm, vertical `shift`, on to x_end. Returns the row y. side -1 = left of
        the IC, +1 = right."""
        px, py = self.pt("U", pin)
        yy = round(py + shift, 4)
        if shift == 0:
            self.wire((px, py), (x_end, py))
        else:
            xj = round(px + side * jog, 4)
            self.wire((px, py), (xj, py), (xj, yy), (x_end, yy))
        return yy


def title_block(sh, title, desc, subdir):
    sh.set_paper(place.PAPER_TABLOID)
    sh.set_title_block(
        title=title,
        rev=REV,
        date=DATE,
        company=COMPANY,
        comments={1: place.repo_comment(str(REPO), subdir), 2: desc, 3: "Author: " + AUTHOR},
    )


def _expected(p):
    """Analysis inputs (params.json) each decoded passive must equal; C_VS (100 nF) is set only in the .zen."""
    return {"SHUNT": p["rshunt_ohm"], "R_MISO": p["r_miso_ohm"], "R_ALERT": p["r_alert_pullup_ohm"], "C_VS": None}


def stack(sk, name, x, ymid, expected):
    """Passive text stack of part `name` (see passive_text.stack); returns its lines."""
    return pt.stack(sk.sh, sk.sh.symbol(sk.ref[name]), x, ymid, expected[name], sk.note)


# ---------------------------------------------------------------------------------------------------------------
# Block (child) sheet
# ---------------------------------------------------------------------------------------------------------------
def child(path, v, f):
    sk = Sketch(path)
    sh = sk.sh
    sk.drop_power_symbols()
    sk.clear()
    title_block(sh, "INA229HighSideMonitor", "Reference sheet: INA229 high-side monitor, on-block shunt, SPI, ALERT_N",
                "blocks/INA229HighSideMonitor")
    rf = sk.ref
    fl = notes_rules.fields(v, f)
    bn = {k: t.format(**fl) for k, t in notes_rules.BLOCK_NOTES.items()}
    expected = _expected(json.load(open(HERE / "params.json")))
    lab = lambda n: "%s (%s)" % (rf[n], n)  # "R3 (SHUNT)": designator first, Zener name second
    st = lambda name, x, ymid: stack(sk, name, x, ymid, expected)

    # ---- geometry ------------------------------------------------------------------------------------------------
    XL = g(12)  # left edge of the boxes
    YP = g(30)  # power path height
    T2 = g(53)  # top of the second row of boxes
    px_l = g(55)  # INA229 left pin column
    px_r = round(px_l + 40.64, 4)  # right pin column
    UX, UY = round((px_l + px_r) / 2, 4), g(78)
    x_endL = g(25)  # free ends of the left stubs
    x_a = place.snap(px_r + 45.72)  # right edge of the INA229 box
    spi_l = round(x_a + 5.08, 4)

    # ================= POWER PATH / CURRENT SENSE =============================================================
    x_bin, x_sh = g(24), g(60)
    sk.hlabel("BUS_IN", x_bin, YP, 180)
    sk.put("SHUNT", "4", x_sh - 12.7, YP, 0, "y")  # I1 left, E pins below
    sk.wire((x_bin, YP), sk.pt("SHUNT", "4"))
    notes_p = [bn["shunt_kelvin"], bn["shunt"]]
    x_n = x_sh + 20.32
    # widths decide the right edge shared by the power path and the SPI boxes
    spi_notes = [bn["rmiso"], bn["spi"]]
    w_spi = max(tw(l) for l in spi_notes) + 7.62
    w_pp = x_n + max(tw(l) for l in notes_p + [bn["path"]]) + 6.0 + 20.32 - spi_l
    spi_r = place.snap(spi_l + max(w_spi, w_pp, 85.0))
    pw_r = spi_r
    x_bout = round(pw_r - 20.32, 4)
    sk.wire(sk.pt("SHUNT", "1"), (x_bout, YP))
    sk.hlabel("BUS_OUT", x_bout, YP, 0)
    for pin, net in (("3", "INA.SENSE_P"), ("2", "INA.SENSE_N")):
        x, y = sk.pt("SHUNT", pin)
        sk.wire((x, y), (x, y + 17.78))  # one straight leg: pcb apply drops a leg of a bent stub at these pins
        sk.label(net, x, y + 17.78, 90)
    st("SHUNT", x_sh - 6.35, YP - 11.05)
    sk.field("SHUNT", "Value", x_sh, YP - 6.0)  # hidden by the stack writer; keeps the field clear of the text
    sk.note("\n".join(notes_p), x_n, YP - 2.8, "left bottom")
    sk.note(bn["path"], x_n, YP + 3.2)
    pw_top, pw_bot = YP - 20.0, YP + 2.54 + 17.78 + 3.81
    sk.box(XL, pw_top, pw_r, pw_bot, "POWER PATH / CURRENT SENSE")

    # ================= INA229 MONITOR ========================================================================
    sk.put("U", "1", px_r, UY - 5.08, 0, "y")  # mirrored in x: SPI on the right, sense and supply on the left
    # left side, top to bottom: IN+, IN-, VBUS, GND, VS
    y = sk.fan("10", -1, 2.54, -7.62, x_endL)
    sk.label("INA.SENSE_P", x_endL, y, 0)
    y = sk.fan("9", -1, 5.08, -5.08, x_endL)
    sk.label("INA.SENSE_N", x_endL, y, 0)
    y = sk.fan("8", -1, 0, 0, x_endL)
    sk.hlabel("BUS_OUT", x_endL, y, 180)  # second BUS_OUT flag: the wire to the power path would cross the sense rows
    y_g = sk.fan("7", -1, 5.08, 5.08, x_endL)
    sk.hlabel("GND", x_endL, y_g, 180)
    y_v = sk.fan("6", -1, 2.54, 17.78, x_endL)
    sk.hlabel("V3V3", x_endL, y_v, 180)
    x_c = round(px_l - 17.78, 4)  # decoupling capacitor on the VS and GND rows, wired to the pin's own wires
    sk.put("C_VS", "1", x_c, y_v - 5.08, 90)
    sk.wire((x_c, y_v), (x_c, y_v - 5.08))
    sk.junction(x_c, y_v)
    x2, y2 = sk.pt("C_VS", "2")
    sk.wire((x2, y2), (x2, y_g))
    sk.junction(x2, y_g)
    st("C_VS", x_c + 3.2, (y_g + y_v) / 2)
    sk.field("U", "Reference", UX, UY - 12.6)
    sk.field("U", "Value", UX, UY - 10.0)
    # right side: CS, MOSI, ALERT, MISO (R2), SCLK
    x_sl = round(spi_l + 20.32, 4)  # left flag of the SCLK row in the SPI box
    x_p = round(spi_r - 15.24, 4)  # port flags at the right edge of the SPI box
    r_cs, r_sclk, r_mosi = UY - 22.86, UY - 15.24, UY - 7.62
    r_miso = UY + 2.54
    y = sk.fan("1", 1, 2.54, -17.78, x_p)
    sk.hlabel("SPI.CS", x_p, y, 0)
    y = sk.fan("2", 1, 5.08, -5.08, x_p)
    sk.hlabel("SPI.MOSI", x_p, y, 0)
    ax, ay = sk.pt("U", "3")  # ALERT_N: labelled stub, its status group has the pull-up and the port
    sk.wire((ax, ay), (ax + 25.4, ay))  # label lies wholly on the wire, clear of the INA.MISO_SRC label below
    sk.label("ALERT_N", ax + 25.4, ay, 180)
    mx, my = sk.pt("U", "4")  # MISO: R2 next to the pin
    x_r2 = round(mx + 20.32, 4)
    sk.put("R_MISO", "1", x_r2, my, 180)
    sk.wire((mx, my), (x_r2, my))
    sk.label("INA.MISO_SRC", mx + 16.51, my, 180)  # keeps the internal net's name; text lies along the wire
    x2, y2 = sk.pt("R_MISO", "2")
    sk.wire((x2, y2), (x_p, y2))
    sk.hlabel("SPI.MISO", x_p, y2, 0)
    st("R_MISO", x_r2 + 2.0, my + 9.0)
    sx, sy = sk.pt("U", "5")  # SCLK: flagged stub, re-enters the SPI box between CS and MOSI
    ys = round(sy + 15.24, 4)
    sk.wire((sx, sy), (sx + 2.54, sy), (sx + 2.54, ys), (sx + 12.7, ys))
    sk.hlabel("SPI.CLK", sx + 12.7, ys, 0)
    sk.wire((x_sl, r_sclk), (x_p, r_sclk))  # flags at both ends: no dangling wire end
    sk.hlabel("SPI.CLK", x_sl, r_sclk, 180)
    sk.hlabel("SPI.CLK", x_p, r_sclk, 0)
    # ---- notes and boxes ----------------------------------------------------------------------------------------
    y_n = y_v + 6.0
    ina_notes = [bn["cvs"], bn["vbus"], bn["vs"]]
    sk.note("\n".join(ina_notes), XL + 3.81, y_n)
    spi_b = place.snap(my + 5.5 + 2 * PITCH + 3.5 + 1.0)
    sk.note("\n".join(spi_notes), spi_l + 3.81, my + 5.5)
    sk.box(spi_l, T2, spi_r, spi_b, "SPI")
    # ALERT_N status group: pull-up hangs from the row (one wire group, junction), single port flag
    al_t = round(spi_b + 5.08, 4)
    r_al = place.snap(al_t + 28.5)
    x_al, x_r1 = round(spi_l + 6.35, 4), round(spi_l + 31.75, 4)
    sk.wire((x_al, r_al), (x_p, r_al))
    sk.label("ALERT_N", x_al, r_al, 0)
    sk.junction(x_r1, r_al)
    sk.hlabel("ALERT_N", x_p, r_al, 0)
    sk.put("R_ALERT", "1", x_r1, r_al, 270)
    x2, y2 = sk.pt("R_ALERT", "2")
    sk.wire((x2, y2), (x2, y2 - 7.62))
    sk.label("V3V3", x2, y2 - 7.62, 270)
    st("R_ALERT", x_r1 + 3.2, (r_al + y2) / 2)
    sk.note(bn["ralert"] + "\n" + bn["alert"], spi_l + 3.81, r_al + 5.5)
    al_b = place.snap(r_al + 5.5 + 2 * PITCH + 3.5 + 1.0)
    sk.box(spi_l, al_t, spi_r, al_b, "ALERT_N")
    sk.box(XL, T2, x_a, al_b, "INA229 MONITOR")
    sk.sh.save()
    return {"ina": (XL, T2, x_a, al_b), "spi": (spi_l, T2, spi_r, spi_b), "alert": (spi_l, al_t, spi_r, al_b),
            "power": (XL, pw_top, pw_r, pw_bot)}


# ---------------------------------------------------------------------------------------------------------------
# Root (bench) sheet: block diagram of one block instance
# ---------------------------------------------------------------------------------------------------------------
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
    title_block(sh, "INA229HighSideMonitor bench", notes_rules.BENCH_DESC.format(**fl), "benches/INA229HighSideMonitor")
    pw = {}
    for s_ in sh.symbols():
        if sh.lib_symbol(s_.lib_id).get("power") is not None:
            pw[s_.value] = s_.ref
    sh.clear(kinds=place.SIDE_KINDS + ("text", "rectangle"))
    sheet = sh.root.get("sheet")
    x0, y0, w, h = g(140), g(66), g(50), g(52)  # same x0, y0 and BUS_IN / BUS_OUT height as the TPS bench sheet symbol
    sheet.get("at").set_nums(x0, y0)
    sheet.get("size").set_nums(w, h)
    yp = y0 + 12.7  # power path: 12.7 mm below the top edge
    xmid = x0 + 38.1
    set_sheet_pin(sheet, "BUS_IN", x0, yp, 180)
    set_sheet_pin(sheet, "BUS_OUT", x0 + w, yp, 0)
    y_alert = yp + 17.78
    y_spi = [yp + 30.48 + 5.08 * i for i in range(4)]
    set_sheet_pin(sheet, "ALERT_N", x0 + w, y_alert, 0)
    for name, y in zip(("SPI.CS", "SPI.CLK", "SPI.MOSI", "SPI.MISO"), y_spi):
        set_sheet_pin(sheet, name, x0 + w, y, 0)
    xv = x0 + 25.4  # V3V3 powers the IC: top edge
    set_sheet_pin(sheet, "V3V3", xv, y0, 90)
    set_sheet_pin(sheet, "GND", xmid, y0 + h, 270)
    for prop, (px, py, just) in {"Sheetname": (x0, y0 - 1.9, "left bottom"), "Sheetfile": (x0, y0 + h + 1.9, "left top")}.items():
        pr = [p for p in sheet.nodes("property") if p.args()[0] == prop][0]
        pr.get("at").set_nums(px, py, 0)
    # rails: power symbols on the parent (up / down)
    sh.place_symbol(pw["V3V3"], xv, y0 - 10.16)
    sh.wire([(xv, y0 - 10.16), (xv, y0)], "w.v3v3")
    sh.place_symbol(pw["GND"], xmid, y0 + h + 10.16)
    sh.wire([(xmid, y0 + h), (xmid, y0 + h + 10.16)], "w.gnd")
    # power in (left)
    xin = x0 - 76.2
    sk.wire((xin, yp), (x0, yp))
    sk.label("VBUS_IN", xin, yp, 0)
    nin = notes_rules.lines_text(notes_rules.ROOT_NOTES["in"], v, f)
    sk.note("\n".join(nin[:2]), xin - 2.0, yp - 5.5, "left bottom")
    sk.note("\n".join(nin[2:]), xin - 2.0, yp + 3.2)
    wi = max(tw(l) for l in nin) + 4.0
    sk.box(xin - 5.08, yp - 5.5 - 2 * 2.03 - 7.5, xin - 5.08 + wi + 4.0, yp + 3.2 + 2 * 2.03 + 5.0, "POWER IN")
    # right: power out, ALERT_N, SPI
    xo = x0 + w
    xe = round(xo + 45.72, 4)
    nout = notes_rules.lines_text(notes_rules.ROOT_NOTES["out"], v, f)
    nal = notes_rules.lines_text(notes_rules.ROOT_NOTES["alert"], v, f)
    nspi = notes_rules.lines_text(notes_rules.ROOT_NOTES["spi"], v, f)
    wmax = max(tw(l) for l in nout + nal + nspi)
    right = xe + 5.08 + wmax + 4.0
    sk.wire((xo, yp), (xe, yp))
    sk.label("VBUS_LOAD", xe, yp, 180)
    sk.note("\n".join(nout), xe + 5.08, yp - 3.0)
    sk.box(xo + 7.62, yp - 9.0, right, yp + 6.0, "POWER OUT")
    sk.wire((xo, y_alert), (xe, y_alert))
    sk.label("ALERT_N", xe, y_alert, 180)
    sk.note("\n".join(nal), xe + 5.08, y_alert - 1.0)
    sk.box(xo + 7.62, yp + 10.7, right, y_alert + 3.5, "ALERT_N")
    for y, name in zip(y_spi, ("SPI_CS", "SPI_CLK", "SPI_MOSI", "SPI_MISO")):
        sk.wire((xo, y), (xe, y))
        sk.label(name, xe, y, 180)
    sk.note("\n".join(nspi), xe + 5.08, y_spi[0] + 3.0)
    sk.box(xo + 7.62, y_alert + 6.2, right, y_spi[-1] + 5.5, "SPI")
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
    rt = x - 8.0 + 3.81
    for xc, c in zip(xs, cols):
        sk.note("\n".join(c), xc, ytop + 6.8)
    nrows = max(len(c) for c in cols)
    sk.box(left, ytop, rt, ytop + 6.8 + nrows * 2.03 + 3.5, notes_rules.DV_TITLE.format(**fl))
    sh.save()


def check_stacks(path):
    return pt.check_stacks(path, NS + "/" + Path(path).stem, _expected(json.load(open(HERE / "params.json"))))


if __name__ == "__main__":
    eda = HERE / "eda"
    if "--eda" in sys.argv:
        eda = Path(sys.argv[sys.argv.index("--eda") + 1])
    if "--check" in sys.argv:
        problems = check_stacks(str(eda / "INA.kicad_sch"))
        for p_ in problems:
            print("passive text drift:", p_)
        print("passive text: %s" % ("DRIFT" if problems else "in sync"))
        sys.exit(1 if problems else 0)
    v = json.load(open(HERE / "docs/design-analysis/values.json"))
    f = lambda x, n: f"{x:.{n}f}"
    info = child(str(eda / "INA.kicad_sch"), v, f)
    root(str(eda / "INA229HighSideMonitor_bench.kicad_sch"), v, f)
    print(info)
