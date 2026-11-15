"""Update the schematic engineering notes from docs/design-analysis/values.json (deterministic, no model).

Usage (repo root):  python3 tools/schematic/update_notes.py
Run after `python3 -m analysis.ina229_block`, then gate: `pcb apply schematic --no-open ina229.zen` twice,
netlist diff, `pcb build -Dwarnings ina229.zen`, ERC. Each note line is found by a pattern and must match exactly
once, or the script stops without writing. Keep this table in step with the notes on the sheets.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
v = json.load(open(ROOT / "docs/design-analysis/values.json"))
I = f"{v['i_fs_a']:g} A"
NUM = r"[0-9.]+"


def f(x, n):
    return f"{x:.{n}f}"


RULES = {
    "eda/ina229.kicad_sch": [
        (rf"(?<!DC, ){NUM} A FS, bidirectional", f"{I} FS, bidirectional"),
        (rf"Bus 12-40 V DC, {NUM} A FS bidirectional", f"Bus 12-40 V DC, {I} FS bidirectional"),
        (rf"Rshunt {NUM} mohm \+/-{NUM} % \(WSK[0-9A-Z]+\), Kelvin",
         f"Rshunt {v['rshunt_mohm']:g} mohm +/-{v['tol_pct']:g} % ({v['mpn']}), Kelvin"),
        (rf"{NUM} A FS: Vshunt {NUM} mV max of {NUM} mV \(ADCRANGE 0\)",
         f"{I} FS: Vshunt {f(v['vsense_round_up_mv'], 1)} mV max of {v['adc_range_mv']:g} mV (ADCRANGE 0)"),
        (rf"Scaling {NUM} A/V of Vshunt \(=1/{NUM} mohm\), {NUM} uA/count",
         f"Scaling {v['a_per_v']:g} A/V of Vshunt (=1/{v['rshunt_mohm']:g} mohm), {f(v['current_lsb_ua'], 2)} uA/count"),
        (r"SHUNT_CAL [0-9]+", f"SHUNT_CAL {v['shunt_cal']}"),
        (rf"I error \+/-{NUM} % at {NUM} A, \+/-{NUM} % at 100 mA \(self-heated worst case\)",
         f"I error +/-{f(v['err_fs_pct'], 2)} % at {I}, +/-{f(v['err_low_pct'], 2)} % at 100 mA (self-heated worst case)"),
        (rf"Requirement \+/-{NUM} % at {NUM} A: [A-Z]+", f"Requirement +/-{v['req_pct']:g} % at {I}: {v['req_status']}"),
        (rf"Shunt {NUM} W max of {NUM} W; element ~{NUM} C \(estimate\)",
         f"Shunt {f(v['p_shunt_w'], 2)} W max of {f(v['p_rating_w'], 1)} W; element ~{v['t_element_c']:.0f} C (estimate)"),
        (rf"VBUS error \+/-{NUM} mV at 40 V, \+/-{NUM} mV at 12 V",
         f"VBUS error +/-{f(v['bus_err_40v_mv'], 1)} mV at 40 V, +/-{f(v['bus_err_12v_mv'], 1)} mV at 12 V"),
        (rf"Top level: 12-40 V, {NUM} A high-side", f"Top level: 12-40 V, {I} high-side"),
    ],
    "eda/INA.kicad_sch": [
        (rf"Rshunt {NUM} mohm \+/-{NUM} %(?! \()", f"Rshunt {v['rshunt_mohm']:g} mohm +/-{v['tol_pct']:g} %"),
        (rf"{NUM} A FS: {NUM} mV max", f"{I} FS: {f(v['vsense_round_up_mv'], 1)} mV max"),
    ],
}

out = {}
for rel, rules in RULES.items():
    s = (ROOT / rel).read_text()
    for pat, rep in rules:
        hits = re.findall(pat, s)
        if len(hits) != 1:
            sys.exit(f"{rel}: pattern {pat!r} matched {len(hits)} times; nothing written")
        if hits[0] != rep:
            print(f"{rel}: {hits[0]!r} -> {rep!r}")
        s = re.sub(pat, lambda _m: rep, s)
    out[rel] = s
for rel, s in out.items():
    (ROOT / rel).write_text(s)
print("notes up to date")
