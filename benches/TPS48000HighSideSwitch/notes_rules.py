"""Schematic note rules for the TPS48000HighSideSwitch bench (read by tools/schematic/update_notes.py and by
arrange_sheet.py, which writes the first copy of every line from the same templates).

Every line that carries an instance number is a template: `{name}` fields are numbers filled from values.json, the
rest is fixed text. update_notes.py needs one (regex, replacement) pair per line; the regex is the template with each
field replaced by a number pattern, and it must match exactly once in the sheet, or nothing is written. Paths are
relative to this bench folder. The block sheet (eda/TPS.kicad_sch) carries only the LOAD TURN-ON RATING numbers (spec.md
Schematic deliverable); every other instance number lives in the DESIGN VALUES box on the parent sheet.

Sources: every number is a values.json key (analysis.py output; spec.md quotes it). Fixed numbers in templates come
from spec.md: the 12-40 V rated bus and the 200 uF reference load capacitance (Operating conditions, Inrush), the
100 V VS/CS+ abs max (DS) is quoted by the analysis row turnoff_transient_no_tvs.
"""
import json
import re
from pathlib import Path

NUM = r"[0-9.]+"


def child_refs(path=None):
    """{Zener name: reference designator} read from the block sheet (by each part's Path property), so notes
    never hard-code designators. Falls back to the name itself if the sheet is missing."""
    path = Path(path) if path else Path(__file__).parent / "eda" / "TPS.kicad_sch"
    out = {}
    if path.exists():
        for blk in path.read_text().split("\n\t(symbol\n")[1:]:
            m = re.search(r'\(property "Reference" "([A-Z]+[0-9]+)"', blk)
            q = re.search(r'\(property "Path" "[^".]+\.([A-Za-z_0-9]+)\.', blk)
            if m and q:
                out[q.group(1)] = m.group(1)
    return out


def pin_number(name, path=None):
    """Pin number of the controller pin called `name`, read from the symbol embedded in the block sheet."""
    path = Path(path) if path else Path(__file__).parent / "eda" / "TPS.kicad_sch"
    txt = path.read_text()
    i = txt.index('(symbol "TPS48000QDGXQ1')
    hits = set()
    for blk in re.split(r"\(pin ", txt[i:i + 200000])[1:]:
        n, k = re.search(r'\(name "([^"]*)"', blk), re.search(r'\(number "([^"]*)"', blk)
        if n and k and n.group(1) == name:
            hits.add(k.group(1))
    if len(hits) != 1:
        raise SystemExit("controller pin %r: expected one number, found %r" % (name, hits))
    return hits.pop()


def ref_range(refs):
    """'C1-C3' for contiguous designators of one letter, else 'C1/C4/C7'."""
    n = sorted(int(re.sub("[A-Z]+", "", r)) for r in refs)
    pre = re.sub("[0-9]+", "", refs[0])
    if n == list(range(n[0], n[-1] + 1)) and len(n) > 1:
        return "%s%d-%s%d" % (pre, n[0], pre, n[-1])
    return "/".join("%s%d" % (pre, i) for i in n)


def fields(v, f):
    """Formatted numbers for the templates (f(x, n) = fixed decimals)."""
    sm = {s["key"]: s for s in v["summary"]}
    p = json.load(open(Path(__file__).parent / "params.json"))  # analysis inputs (basis in params.json)
    div = re.fullmatch(r"([0-9.]+) to ([0-9.]+) uA", sm["divider_current_ua"]["result"])
    drop = re.fullmatch(r"([0-9.]+) mV", sm["drop_des_mv"]["result"])
    if not (div and drop):
        raise SystemExit("values.json summary divider_current_ua / drop_des_mv have an unexpected format")
    mm = re.fullmatch(r"\+([0-9.]+) V", sm["turnon_max_pass_v"]["margin"])
    if not mm:
        raise SystemExit("values.json summary turnon_max_pass_v margin has an unexpected format")
    pct = sm["trip_min_a"]["margin"]  # e.g. "+0.78 A (+13.0 %)"
    m = re.fullmatch(r"\+([0-9.]+) A \(\+([0-9.]+) %\)", pct)
    if not m:
        raise SystemExit("values.json summary trip_min_a margin has an unexpected format: %r" % pct)
    refs = child_refs()
    # worst FET junction temperature over the analysed cases (values.json keys; the case name is printed with it)
    tj_cases = {
        "6 A steady": v["fet_tj_des_c"],
        "turn-on": v["turnon_tj_c"],
        "soft short": v["soft_fet_tj_c"],
        "%g A fault" % p["i_fault_a"]: v["fault_tj_c"],
    }
    tj_worst = max(tj_cases.items(), key=lambda kv: kv[1])
    rf = lambda n: refs.get(n, n)
    tolcode = {"D": "0.5", "F": "1"}[p["rshunt_tol_code"]]  # WSK2512-LOW tolerance codes (TPS48000HighSideSwitch.zen)
    return {
        "ref_q1": rf("Q1"),
        "ref_shunt": rf("SHUNT"),
        "ref_ctmr": rf("C_TMR"),
        "ref_cbst": rf("C_BST"),
        "pin_bst": pin_number("BST"),
        "pin_src": pin_number("SRC"),
        "ref_riscp": rf("R_ISCP"),
        "ref_rtmr": rf("R_TMR"),
        "ref_rgate": rf("R_GATE"),
        "ref_cgate": rf("C_GATE"),
        "ref_rdamp": rf("R_DAMP"),
        "vscp_lo": f(v["vscp_adj_mv_min"], 1),
        "vscp_hi": f(v["vscp_adj_mv_max"], 1),
        "tdrv_lo": f(v["tdrv_en_min_ms"], 1),
        "tdrv_hi": f(v["tdrv_en_max_ms"], 1),
        "rdamp_ohm": f"{v['r2_ohm']:g}",
        "tjw": f(tj_worst[1], 1),
        "tjw_case": tj_worst[0],
        "rshunt_mohm": f"{v['rshunt_mohm']:g}",
        "shunt_tol": tolcode,
        "mpn_shunt": v["mpn_shunt"],
        "cbst_min": f(v["cbst_min_nf"], 1),
        "t_elem": f"{p['t_elem_limit_c']:g}",
        "i_design": f"{v['i_design_a']:g}",
        "turnon_pass_v": f"{v['turnon_max_pass_v']:g}",
        "trip_lo": f(v["trip_window_a"][0], 2),
        "trip_hi": f(v["trip_window_a"][1], 2),
        "trip_margin_a": m.group(1),
        "trip_margin_pct": m.group(2),
        "soft_shunt_w": f(v["soft_shunt_w"], 3),
        "soft_tj": f(v["soft_fet_tj_c"], 1),
        "tsc_lo": f(v["tsc_us"][0], 1),
        "tsc_hi": f(v["tsc_us"][1], 1),
        "ctmr_nf": f"{v['ctmr_nf']:g}",
        "fault_i": f"{p['i_fault_a']:g}",
        "vbus_lo": f"{p['vbus_lo_v']:g}",
        "vbus_hi": f"{p['vbus_hi_v']:g}",
        "vbus_bench": f"{p['vbus_bench_v']:g}",
        "vbus_min": f"{p['vbus_min_v']:g}",
        "cload_uf": f"{p['cload_f'] * 1e6:g}",
        "ta_max": f"{p['ta_max_c']:g}",
        "ta_min": f"{p['ta_min_c']:g}",
        "v3v3": f"{p['v3v3_nom_v']:g}",
        "v3v3_tol": f"{p['v3v3_tol_frac'] * 100:g}",
        "turnon_margin": mm.group(1),
        "fault_tj": f(v["fault_tj_c"], 1),
        "fault_lim": f"{v['fault_i_limit_a']:.0f}",
        "uvlo_r_lo": f(v["uvlo_rise_v"][0], 2),
        "uvlo_r_hi": f(v["uvlo_rise_v"][1], 2),
        "uvlo_f_lo": f(v["uvlo_fall_v"][0], 2),
        "uvlo_f_hi": f(v["uvlo_fall_v"][1], 2),
        "uvlo_margin": f(v["uvlo_turnon_margin_v"], 3),
        "ov_r_lo": f(v["ov_rise_v"][0], 2),
        "ov_r_hi": f(v["ov_rise_v"][1], 2),
        "ov_f_lo": f(v["ov_fall_v"][0], 2),
        "ov_f_hi": f(v["ov_fall_v"][1], 2),
        "div_lo": div.group(1),
        "div_hi": div.group(2),
        "inrush_lo": f(v["inrush_a"][0], 2),
        "inrush_hi": f(v["inrush_a"][1], 2),
        "ramp12_lo": f(v["ramp_ms_12v"][0], 2),
        "ramp12_hi": f(v["ramp_ms_12v"][1], 2),
        "ramp40_lo": f(v["ramp_ms_40v"][0], 2),
        "ramp40_hi": f(v["ramp_ms_40v"][1], 2),
        "turnon_tj": f(v["turnon_tj_c"], 1),
        "fet_tj": f(v["fet_tj_des_c"], 1),
        "shunt_w": f(v["shunt_p_des_w"], 3),
        "shunt_c": f(v["shunt_elem_des_c"], 1),
        "drop_mv": drop.group(1),
        "clamp_v": f"{v['turnoff_transient_no_tvs']:.0f}",
        "overlap_v": f(v["gate_overlap_v"], 2),
        "bom": f"{v['bom_lines']}",
        "shunt_rating_w": re.fullmatch(r"<= ([0-9.]+) W", sm["soft_shunt_w"]["limit"]).group(1),
        "absmax_v": re.match(r"<= ([0-9.]+) V", sm["turnoff_transient_no_tvs"]["limit"]).group(1),
    }


# DESIGN VALUES on the parent sheet: one column, one template per line (Fox-1-MPPT style, setpoint first).
DV_COLUMNS = [
    [
        "Rating {vbus_lo}-{vbus_hi} V, {i_design} A, {cload_uf} uF load (bench {vbus_bench} V)",
        "SCP trip {trip_lo}-{trip_hi} A (ESTIMATE), min +{trip_margin_pct} % over {i_design} A",
        "tSC {tsc_lo}-{tsc_hi} us, latch-off",
        "UVLO on {uvlo_r_lo}-{uvlo_r_hi} V, off {uvlo_f_lo}-{uvlo_f_hi} V (min bus {vbus_min} V)",
        "OV trip {ov_r_lo}-{ov_r_hi} V, reset {ov_f_lo}-{ov_f_hi} V",
        "Inrush {inrush_lo}-{inrush_hi} A into {cload_uf} uF",
        "Turn-on delay {tdrv_lo}-{tdrv_hi} ms",
        "Worst {ref_q1} Tj {tjw} C ({tjw_case}, ESTIMATE)",
        "FAIL (open items): no TVS turn-off >= {clamp_v} V vs {absmax_v} V abs max; gate-good overlap {overlap_v} V",
    ],
]

BENCH_DESC = "Bench: {vbus_lo}-{vbus_hi} V, {i_design} A high-side reference instance of TPS48000HighSideSwitch"
DV_TITLE = "DESIGN VALUES (TA {ta_min}-{ta_max} C, bench instance)"

# Notes on the parent sheet next to the sheet symbol (instance numbers; the block sheet carries none of these).
ROOT_NOTES = {
    "in": [
        "VBUS_IN: bus {vbus_lo}-{vbus_hi} V rated (bench {vbus_bench} V), high-side",
        "{i_design} A design current",
        "Prospective fault current at VBUS_IN <= {fault_i} A:",
        "PSU current limit or per-channel fuse",
    ],
    "out": [
        "VBUS_OUT: load side (Q1 source)",
        "Drop {drop_mv} mV at {i_design} A (ESTIMATE)",
        "No reverse blocking, no TVS",
    ],
    "status": [
        "SW_EN: active high, logic at V3V3 or below",
        "SW_PGOOD_N, SW_FAULT_N: open drain, active low,",
        "10 kohm pull-ups to V3V3 on the block",
    ],
    "v3v3": [
        "V3V3 {v3v3} V +/-{v3v3_tol} %:",
        "pull-ups only",
    ],
}

# LOAD TURN-ON RATING on the block sheet: qualitative only (the block sheet carries no instance numbers); the rating
# envelope numbers are in the parent DESIGN VALUES.
RATING_LINES = [
    "Any load at {vbus_lo}-{vbus_hi} V, <= {i_design} A, with {cload_uf} uF: inrush {inrush_lo}-{inrush_hi} A at turn-on",
    "Turn-on ramp {ramp12_lo}-{ramp12_hi} ms at {vbus_lo} V, {ramp40_lo}-{ramp40_hi} ms at {vbus_hi} V; no load reads PGOOD_N",
    "Above {i_design} A at or after turn-on: outside the rating (spec.md)",
]

# Block-sheet notes that carry instance numbers (filled from values.json; update_notes.py keeps them current).
BLOCK_NOTES = {
    "scp": "{ref_riscp} (R_ISCP): V(SCP) = {vscp_lo}-{vscp_hi} mV -> I(SC) = {trip_lo}-{trip_hi} A",
    "tmr": "{ref_ctmr} (C_TMR): tSC = {tsc_lo}-{tsc_hi} us; {ref_rtmr} (R_TMR) fitted = latch-off (EN low->high resets)",
    "gate": "{ref_rgate} (R_GATE), {ref_cgate} (C_GATE) set inrush {inrush_lo}-{inrush_hi} A into {cload_uf} uF (DS eq. 3, Fig 7-4 p.15)",
    "damp": "{ref_rdamp} (R_DAMP) ~{rdamp_ohm} ohm damping (DS p.16)",
    "bst": "{ref_cbst} (C_BST): BST pin {pin_bst} to SRC pin {pin_src}, place at pins; >= {cbst_min} nF (DS eq. 4)",
    "tdrv": "{ref_cbst} (C_BST): turn-on delay {tdrv_lo}-{tdrv_hi} ms (DS eq. 1)",
}


def template_regex(t):
    """Regex for a template: fixed text escaped, each {field} a number (text fields: the exact value)."""
    out, pos = "", 0
    txt = {k: re.escape(v) for k, v in fields_cache.items() if k.startswith(("ref_", "mpn_", "tjw_case", "pin_"))}
    for m in re.finditer(r"\{([a-z0-9_]+)\}", t):
        out += re.escape(t[pos:m.start()]) + txt.get(m.group(1), NUM)
        pos = m.end()
    return out + re.escape(t[pos:])


def lines_text(templates, v, f):
    fl = fields(v, f)
    return [t.format(**fl) for t in templates]


fields_cache = {}


def rules(v, f, _num):
    fl = fields(v, f)
    fields_cache.update(fl)
    out = {"eda/TPS48000HighSideSwitch_bench.kicad_sch": [], "eda/TPS.kicad_sch": []}
    for col in DV_COLUMNS:
        for t in col:
            if "{" in t:
                out["eda/TPS48000HighSideSwitch_bench.kicad_sch"].append((template_regex(t), t.format(**fl)))
    for k in ROOT_NOTES.values():
        for t in k:
            if "{" in t:
                out["eda/TPS48000HighSideSwitch_bench.kicad_sch"].append((template_regex(t), t.format(**fl)))
    out["eda/TPS48000HighSideSwitch_bench.kicad_sch"].append((template_regex(DV_TITLE), DV_TITLE.format(**fl)))
    out["eda/TPS48000HighSideSwitch_bench.kicad_sch"].append((template_regex(BENCH_DESC), BENCH_DESC.format(**fl)))
    for t in RATING_LINES + list(BLOCK_NOTES.values()):
        out["eda/TPS.kicad_sch"].append((template_regex(t), t.format(**fl)))
    return out
