"""Schematic note rules for the INA229HighSideMonitor bench (read by tools/schematic/update_notes.py and by
arrange_sheet.py, which writes the first copy of every line from the same templates).

Every line that carries an instance number or a part designator is a template: `{name}` fields are filled from
values.json, params.json or the block sheet, the rest is fixed text. update_notes.py needs one (regex, replacement)
pair per line; the regex is the template with each field replaced by a number pattern (text fields: the exact
value), and it must match exactly once in the sheet, or nothing is written. Paths are relative to this bench folder.

The block sheet (eda/INA.kicad_sch) carries the shunt and termination numbers of this instance (filled per instance
when the sheet is stamped); the parent sheet carries the DESIGN VALUES. Every number is a values.json or params.json
key (analysis.py output and its inputs); fixed numbers in the text are INA229 datasheet limits quoted by spec.md.
"""
import json
import re
from pathlib import Path

HERE = Path(__file__).parent


def child_refs(path=None):
    """{Zener name: reference designator} read from the block sheet (by each part's Path property), so notes never
    hard-code designators. Falls back to the name itself if the sheet is missing."""
    path = Path(path) if path else HERE / "eda" / "INA.kicad_sch"
    out = {}
    if path.exists():
        for blk in path.read_text().split("\n\t(symbol\n")[1:]:
            m = re.search(r'\(property "Reference" "([A-Z]+[0-9]+)"', blk)
            q = re.search(r'\(property "Path" "[^".]+\.([A-Za-z_0-9]+)\.', blk)
            if m and q:
                out[q.group(1)] = m.group(1)
    return out


def pin_number(name, path=None):
    """Pin number of the INA229 pin called `name`, read from the symbol embedded in the block sheet."""
    path = Path(path) if path else HERE / "eda" / "INA.kicad_sch"
    txt = path.read_text()
    i = txt.index('(symbol "INA229AIDGS"')
    hits = set()
    for blk in re.split(r"\(pin ", txt[i:i + 200000])[1:]:
        n, k = re.search(r'\(name "([^"]*)"', blk), re.search(r'\(number "([^"]*)"', blk)
        if n and k and n.group(1) == name:
            hits.add(k.group(1))
    if len(hits) != 1:
        raise SystemExit("INA229 pin %r: expected one number, found %r" % (name, hits))
    return hits.pop()


def fields(v, f):
    """Formatted numbers and designators for the templates (f(x, n) = fixed decimals)."""
    p = json.load(open(HERE / "params.json"))  # analysis inputs (basis in params.json)
    refs = child_refs()
    rf = lambda n: refs.get(n, n)
    return {
        "ref_u": rf("U"),
        "ref_shunt": rf("SHUNT"),
        "ref_cvs": rf("C_VS"),
        "ref_rmiso": rf("R_MISO"),
        "ref_ralert": rf("R_ALERT"),
        "pin_vs": pin_number("VS"),
        "pin_vbus": pin_number("VBUS"),
        "i_fs": f"{v['i_fs_a']:g}",
        "rshunt_mohm": f"{v['rshunt_mohm']:g}",
        "shunt_tol": f"{v['tol_pct']:g}",
        "mpn_shunt": v["mpn"],
        "vsense": f(v["vsense_round_up_mv"], 1),
        "adc_range": f"{v['adc_range_mv']:g}",
        "a_per_v": f"{v['a_per_v']:g}",
        "lsb_ua": f(v["current_lsb_ua"], 2),
        "shunt_cal": f"{v['shunt_cal']}",
        "err_cal": f(v["err_fs_cal_pct"], 3),
        "err_uncal": f(v["err_fs_uncal_pct"], 3),
        "req": f"{v['req_pct']:g}",
        "cal_ref": f"{v['cal_ref_pct']:g}",
        "txt_status": v["req_status"],
        "shunt_w": f(v["p_shunt_w"], 2),
        "shunt_rating_w": f(v["p_rating_w"], 1),
        "t_elem": f"{v['t_element_c']:.0f}",
        "bus_err_hi": f(v["bus_err_40v_mv"], 1),
        "bus_err_lo": f(v["bus_err_12v_mv"], 1),
        "vbus_lsb": f(v["vbus_lsb_uv"], 1),
        "vbus_lo": f"{p['vbus_lo_v']:g}",
        "vbus_hi": f"{p['vbus_hi_v']:g}",
        "ta_min": f"{p['ta_min_c']:g}",
        "ta_max": f"{p['ta_max_c']:g}",
        "v3v3": f"{p['vs_nom_v']:g}",
        "v3v3_tol": f"{p['vs_tol_frac'] * 100:g}",
        "r_miso": f"{p['r_miso_ohm']:g}",
        "r_alert_k": f"{p['r_alert_pullup_ohm'] / 1e3:g}",
    }


# DESIGN VALUES on the parent sheet: one column, one template per line (Fox-1-MPPT style, setpoint first).
DV_COLUMNS = [
    [
        "Bus {vbus_lo}-{vbus_hi} V DC, {i_fs} A FS bidirectional",
        "Rshunt {rshunt_mohm} mohm +/-{shunt_tol} % ({mpn_shunt}), Kelvin",
        "{i_fs} A FS: Vshunt {vsense} mV max of {adc_range} mV (ADCRANGE 0)",
        "I error +/-{err_cal} % cal, +/-{err_uncal} % uncal at {i_fs} A (BOL worst case)",
        "Requirement +/-{req} % at {i_fs} A, 1-point cal (ref +/-{cal_ref} %): {txt_status}",
        "Scaling {lsb_ua} uA/count ({a_per_v} A/V of Vshunt), SHUNT_CAL {shunt_cal}",
        "Shunt {shunt_w} W max of {shunt_rating_w} W, element ~{t_elem} C (estimate)",
        "VBUS error +/-{bus_err_hi} mV at {vbus_hi} V, +/-{bus_err_lo} mV at {vbus_lo} V; LSB {vbus_lsb} uV",
        "V3V3 {v3v3} V +/-{v3v3_tol} %, shared with host",
    ],
]

BENCH_DESC = "Bench: {vbus_lo}-{vbus_hi} V, {i_fs} A high-side reference instance of INA229HighSideMonitor, SPI"
DV_TITLE = "DESIGN VALUES (TA {ta_min}-{ta_max} C, bench instance)"

# Notes on the parent sheet next to the sheet symbol (instance numbers; the block sheet carries the part-level ones).
ROOT_NOTES = {
    "in": [
        "VBUS_IN: bus {vbus_lo}-{vbus_hi} V DC, high-side",
        "{i_fs} A FS, bidirectional",
        "{vbus_hi} V cap: IN+/IN- diff abs max",
        "(0 V margin at hard short)",
    ],
    "out": [
        "VBUS_LOAD: to load",
        "VBUS sensed here (shunt low side)",
    ],
    "alert": [
        "ALERT_N: open drain, active low",
    ],
    "spi": [
        "SPI: 50 ohm lines; MISO terminated on block",
        "Host terminates CS, SCLK, MOSI",
    ],
}

# Block-sheet notes: the part setpoints of this instance (update_notes.py keeps them current).
BLOCK_NOTES = {
    "shunt_kelvin": "{ref_shunt} (SHUNT): 4-terminal Kelvin, E1 -> IN+, E2 -> IN-",
    "shunt": "{ref_shunt} (SHUNT): {rshunt_mohm} mohm -> {i_fs} A FS, Vshunt {vsense} mV of {adc_range} mV (ADCRANGE 0)",
    "path": "BUS_IN -> BUS_OUT, + current; bus <= {vbus_hi} V (IN+/IN- diff abs max)",
    "cvs": "{ref_cvs} (C_VS): {ref_u} VS pin {pin_vs}, place at pin",
    "vbus": "{ref_u} VBUS pin {pin_vbus} = BUS_OUT, shunt load side (DS Fig 10-1 note 1)",
    "vs": "VS 2.7-5.5 V accepted (INA229 rec-op); logic in <= VS + 0.3 V",
    "rmiso": "{ref_rmiso} (R_MISO): {r_miso} ohm source termination at {ref_u} MISO, placeholder (R_out TBD)",
    "spi": "SPI 50 ohm lines after {ref_rmiso}; host terminates CS, SCLK, MOSI",
    "ralert": "{ref_ralert} (R_ALERT): {r_alert_k} kohm pull-up to V3V3, DNP if the host supplies one",
    "alert": "ALERT_N: open drain, active low",
}

_TEXT_PREFIX = ("ref_", "mpn_", "pin_", "txt_")


def template_regex(t, fl):
    """Regex for a template: fixed text escaped, each {field} a number (text fields: the exact value)."""
    out, pos = "", 0
    txt = {k: re.escape(x) for k, x in fl.items() if k.startswith(_TEXT_PREFIX)}
    for m in re.finditer(r"\{([a-z0-9_]+)\}", t):
        out += re.escape(t[pos:m.start()]) + txt.get(m.group(1), r"[0-9.]+")
        pos = m.end()
    return out + re.escape(t[pos:])


def lines_text(templates, v, f):
    fl = fields(v, f)
    return [t.format(**fl) for t in templates]


def rules(v, f, _num):
    fl = fields(v, f)
    root, blk = "eda/INA229HighSideMonitor_bench.kicad_sch", "eda/INA.kicad_sch"
    out = {root: [], blk: []}
    root_t = [t for col in DV_COLUMNS for t in col] + [t for k in ROOT_NOTES.values() for t in k]
    root_t += [DV_TITLE, BENCH_DESC]
    for t in root_t:
        if "{" in t:
            out[root].append((template_regex(t, fl), t.format(**fl)))
    for t in BLOCK_NOTES.values():
        if "{" in t:
            out[blk].append((template_regex(t, fl), t.format(**fl)))
    return out
