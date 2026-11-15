"""Schematic note rules for the INA229HighSideMonitor bench (read by tools/schematic/update_notes.py).

Each rule: (regex that must match exactly once, replacement built from values.json). Paths are relative to this
bench folder. The reference block sheet (eda/INA.kicad_sch) carries no instance numbers, so it has no rules: every
instance number lives on the parent sheet next to the sheet symbol.
"""


def rules(v, f, NUM):
    I = f"{v['i_fs_a']:g} A"
    return {
        "eda/INA229HighSideMonitor_bench.kicad_sch": [
            (rf"(?<!DC, ){NUM} A FS, bidirectional", f"{I} FS, bidirectional"),
            (rf"Bus 12-40 V DC, {NUM} A FS bidirectional", f"Bus 12-40 V DC, {I} FS bidirectional"),
            (rf"Rshunt {NUM} mohm \+/-{NUM} % \(WSK[0-9A-Z]+\), Kelvin",
             f"Rshunt {v['rshunt_mohm']:g} mohm +/-{v['tol_pct']:g} % ({v['mpn']}), Kelvin"),
            (rf"{NUM} A FS: Vshunt {NUM} mV max of {NUM} mV \(ADCRANGE 0\)",
             f"{I} FS: Vshunt {f(v['vsense_round_up_mv'], 1)} mV max of {v['adc_range_mv']:g} mV (ADCRANGE 0)"),
            (rf"Scaling {NUM} A/V of Vshunt \(=1/{NUM} mohm\), {NUM} uA/count",
             f"Scaling {v['a_per_v']:g} A/V of Vshunt (=1/{v['rshunt_mohm']:g} mohm), {f(v['current_lsb_ua'], 2)} uA/count"),
            (r"SHUNT_CAL [0-9]+", f"SHUNT_CAL {v['shunt_cal']}"),
            (rf"I error \+/-{NUM} % cal, \+/-{NUM} % uncal at {NUM} A \(BOL worst case\)",
             f"I error +/-{f(v['err_fs_cal_pct'], 2)} % cal, +/-{f(v['err_fs_uncal_pct'], 2)} % uncal at {I} (BOL worst case)"),
            (rf"Requirement \+/-{NUM} % at {NUM} A, 1-point cal \(ref \+/-{NUM} %\): [A-Z]+",
             f"Requirement +/-{v['req_pct']:g} % at {I}, 1-point cal (ref +/-{v['cal_ref_pct']:g} %): {v['req_status']}"),
            (rf"Shunt {NUM} W max of {NUM} W; element ~{NUM} C \(estimate\)",
             f"Shunt {f(v['p_shunt_w'], 2)} W max of {f(v['p_rating_w'], 1)} W; element ~{v['t_element_c']:.0f} C (estimate)"),
            (rf"VBUS error \+/-{NUM} mV at 40 V, \+/-{NUM} mV at 12 V",
             f"VBUS error +/-{f(v['bus_err_40v_mv'], 1)} mV at 40 V, +/-{f(v['bus_err_12v_mv'], 1)} mV at 12 V"),
            (rf"Bench: 12-40 V, {NUM} A high-side", f"Bench: 12-40 V, {I} high-side"),
        ],
    }
