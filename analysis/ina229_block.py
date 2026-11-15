"""INA229 + WSK2512 high-side current-sense block: worst-case analysis.

Scope: 5 A bidirectional full scale, bus 12-40 V (high side), TA 0-70 degC, VS 3.3 V +/-3 %, ADCRANGE 0.
Run from the repo root:   python3 -m analysis.ina229_block
Writes analysis/out/ina229_block.md and analysis/out/ina229_current_error.png.

Every datasheet number comes from the component library (part.param, part.inputs, part.tolerance, part.tcr,
part.mpn, part.calc). Design choices are the constants in the DESIGN CHOICES block. Worst case = lib.wca over every
min/max corner. Where a function is not multilinear in its inputs, wca also samples interior points (SAMPLES).

Shunt selection (section 1): every WSK2512 value and tolerance code whose worst-case sense voltage at I_MAX fits the
ADC range (ADCRANGE 0 and 1) is a candidate. The candidates are ranked by worst-case full-scale error, using only
parts with stock in the STOCK_EXACT snapshot (a recorded `pcb bom` sourcing result, not a datasheet value).
"""

import math
from pathlib import Path

from analysis import _zl  # noqa: F401  (side effect: puts zener-library on sys.path)
from lib import Param, load, wca

import matplotlib.pyplot as plt  # imported after lib, which selects the Agg backend

OUT = Path(__file__).resolve().parent / "out"

# ---- DESIGN CHOICES (not datasheet values) ----
I_MAX = 5.0  # A, full-scale current, bidirectional
I_LOW = 0.1  # A, low-current point for the error table (section 1 and 5)
VBUS_LO, VBUS_HI = 12.0, 40.0  # V, high-side bus range
VBUS_TEST = [12.0, 18.0, 28.0, 36.0, 40.0]  # V, bus voltages for the bus-error table (task spec)
TA = Param(min=0.0, typ=25.0, max=70.0, unit="degC")  # ambient 0-70 degC; typ = 25 degC reference
T_REF = 25.0  # degC, reference temperature for the shunt TCR (WSK2512 DS states none; assumed 25 degC)
VS_NOM = 3.3  # V, INA229 supply
VS_TOL = 0.03  # fraction, +/-3 % (shared 3.3 V rail, spec.md)
ADCRANGE = 0  # INA229 CONFIG ADCRANGE: 0 = +/-163.84 mV full scale
DERATE = 1.0  # shunt rating derate
SAMPLES = 2  # interior points per input for the full-scale error (checked against samples = 0 below)
N_PLOT = 40  # log-spaced current points for the plot
I_PLOT_LO = 0.1  # A, plot starts here
TOP_N = 6  # largest fitting values per (ADCRANGE, tolerance code) listed in the candidate table
HEADROOM_MIN = 0.02  # fraction of the ADC range: preferred minimum headroom (ranking tie-break, section 1)
STOCK_DATE = "2026-11-15"
STOCK_QUERIED = 169  # ADCRANGE 0 fitting (value, code) pairs queried on STOCK_DATE

# Stock snapshot: sourcing result, not a datasheet value. `pcb bom -f json` on 2026-11-15, run in a scratch copy of
# this board with rshunt / rshunt_tol / vsense_max set to each candidate. It covers every WSK2512 value and code that
# fits the ADCRANGE 0 sense limit at I_MAX (169 pairs). A pair listed here returned MATCH_EXACT; every other fitting
# pair returned MATCH_FAILED. The number is the total stock units over the offers the BOM returned (None: MATCH_EXACT
# but no offer returned, so stock is unknown). Raw record: analysis/out/shunt_stock_2026-11-15.json.
# Snapshot only: stock changes, so re-run the stock check before ordering.
STOCK_EXACT = {
    "B": {20: 0, 10: 0},
    "D": {25: 4549, 22: None, 20: None, 18: None, 15: None, 12: None, 10: 292, 9: None, 8: None, 7.5: None, 7: None,
          6: None, 5: 0},
    "F": {25: 8881, 23: None, 22: None, 21: None, 20: 21561, 18: None, 16: None, 15: None, 13: None, 12: None,
          11: None, 10: 25667, 9: None, 8: None, 7: 2669, 6.5: None, 6.2: None, 6.1: None, 6: 49, 5.5: None,
          5.3: None, 5: 211},
}

assert ADCRANGE in (0, 1), "ADCRANGE must be 0 or 1"

ina = load("TexasInstruments/INA229AIDGS")
shunt = load("Vishay/WSK2512")

VDIFF = ina.param("vdiff_adcr0_full")  # shunt voltage input range, ADCRANGE 0 (signed, symmetric)
VDIFF1 = ina.param("vdiff_adcr1_full")  # shunt voltage input range, ADCRANGE 1 (signed, symmetric)
ADC_LIM = {0: VDIFF.max, 1: VDIFF1.max}  # V, full scale per ADCRANGE
VDIFF_LIM = ADC_LIM[0]  # V, 163.84 mV (ADCRANGE 0)
CURRENT_LSB = ina.calc.current_lsb(I_MAX)  # DS p.30 Equation 3
LSB = CURRENT_LSB  # 1 LSB term, A
RATED_RAW = shunt.calc.rated_power(ta_c=TA.max, **shunt.inputs("rated_power"))  # p70.max, knee, zero (DS p.1, p.3)
RATED = RATED_RAW * DERATE
VOS_KEYS = ("vos_vcm48_25c", "vos_vcm0_25c")  # both DS p.5 rows, +/-1 uV max at 25 degC

BASE_IN = ina.inputs("current_error")  # {vos25, vos_drift, gserr, gs_drift} from the INPUTS map (vos25 replaced below)
GSERR = BASE_IN["gserr"]  # gserr_25c: DS p.5 GSERR, +/-0.05 %
GS_DRIFT = BASE_IN["gs_drift"].with_typ(0.0)  # gs_drift_25c: DS p.5 GS_DRFT, +/-20 ppm/degC, no typ
VOS_DRIFT = BASE_IN["vos_drift"]  # vos_drift_full: DS p.5 dVos/dT, +/-10 nV/degC


def mv(x):
    return f"{x * 1e3:.4g}"


def pct(x):
    """Fraction -> percent text."""
    return f"{x * 100:.4g}"


def mdtable(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


def status(margin, eps=1e-12):
    if margin < -eps:
        return "FAIL"
    if abs(margin) <= eps:
        return "PASS, ZERO MARGIN (OPEN ITEM)"
    return "PASS"


md = []
md += [
    "# INA229 + WSK2512 shunt block: worst-case analysis",
    "",
    "GENERATED by `python3 -m analysis.ina229_block`; do not edit.",
    "",
    f"High-side current sense: {I_MAX:g} A bidirectional full scale, bus {VBUS_LO:g} to {VBUS_HI:g} V, "
    f"TA {TA.min:g} to {TA.max:g} degC, VS {VS_NOM:g} V +/-{VS_TOL * 100:g} %, INA229 ADCRANGE {ADCRANGE}.",
    "",
    "## Design choices (not datasheet values)",
    "",
    f"- I_MAX = {I_MAX:g} A, full scale, bidirectional.",
    f"- Bus {VBUS_LO:g} to {VBUS_HI:g} V, high-side (shunt sits between bus and load).",
    f"- TA {TA.min:g} to {TA.max:g} degC; nominal (typ) reference 25 degC.",
    f"- VS = {VS_NOM:g} V +/-{VS_TOL * 100:g} %.",
    f"- ADCRANGE = {ADCRANGE} (+/-163.84 mV full scale). Derate = {DERATE:g}.",
    f"- Shunt TCR reference temperature {T_REF:g} degC (the WSK2512 DS states none).",
    f"- Full-scale error uses {SAMPLES} interior samples per input (the function is not multilinear); the same "
    "total is also computed with corners only, and the difference is printed in section 4.",
    f"- Shunt candidates: the {TOP_N} largest fitting values per (ADCRANGE, code), plus every value with stock "
    f"(snapshot {STOCK_DATE}). Stock is a `pcb bom` sourcing snapshot, not a guarantee.",
    "",
]


# ======================================================================================================================
# 1. Shunt selection
# ======================================================================================================================
def vsense_worst(i, r0, tol, tcr, t_c):
    """Not a DS equation: worst-case sense voltage magnitude, V = I x R_worst. R_worst = resistance_worst(r0, tol,
    tcr, t_c, 25 degC) from the WSK2512 calc (tolerance DS p.1, TCR DS p.2). i: A. r0: ohm. tol: fraction. tcr: 1/degC.
    t_c: degC. Returns: V (magnitude)."""
    return abs(i) * shunt.calc.resistance_worst(r0, tol, tcr, t_c, T_REF)


def power_worst(i, r0, tol, tcr, t_c):
    """Not a DS equation: dissipation P = I^2 x R_worst at ambient t_c, R_worst from resistance_worst (25 degC
    reference). Uses WSK2512 calc.dissipation (DS p.1 and p.3 rate power only). i: A. r0: ohm. Returns: W."""
    return shunt.calc.dissipation(i, shunt.calc.resistance_worst(r0, tol, tcr, t_c, T_REF))


def rworst(r0, tol, tcr, t_c):
    """Not a DS equation: worst-case shunt resistance, ohm (WSK2512 calc.resistance_worst, 25 degC reference)."""
    return shunt.calc.resistance_worst(r0, tol, tcr, t_c, T_REF)


def fs_error(i_true, r_nom, r_tol, r_tcr, t_c, gserr, gs_drift, vos25, vos_drift, cal_gain):
    """Not a DS equation: measured minus true current (A), with the real shunt and an INA229 calibrated for r_nom.

    Derivation: R_act = resistance_worst(r_nom, r_tol, r_tcr, t_c) (shunt tolerance and TCR). The shunt carries i_true
    and develops V = i_true x R_act, so i_seen = i_true x R_act / r_nom is the current the r_nom calibration would
    report for that voltage. SHUNT_CAL is an integer, so the register scale is off by cal_gain = SHUNT_CAL / raw
    (1.0 when raw is an integer); i_seen is scaled by it. The INA229 reading is then the INA229 calc.current_error (DS
    gain and offset terms, SPEC recipe step 2) evaluated at i_seen.
    Error = (i_seen - i_true) + current_error(i_seen, r_nom, vos25, vos_drift, gserr, gs_drift, t_c).
    i_true: A. r_nom: ohm. r_tol: fraction. r_tcr: 1/degC. t_c: degC. gserr: fraction. gs_drift: 1/degC. vos25: V.
    vos_drift: V/degC. cal_gain: unitless. Returns: A (signed; positive = reads high)."""
    r_act = shunt.calc.resistance_worst(r_nom, r_tol, r_tcr, t_c, T_REF)
    i_seen = i_true * r_act / r_nom * cal_gain
    return (i_seen - i_true) + ina.calc.current_error(i_seen, r_nom, vos25, vos_drift, gserr, gs_drift, t_c)


def fs_for(r_nom, code, cal_gain, vos_key, samples, i=I_MAX):
    """wca of fs_error for a shunt of value r_nom and tolerance code, with one Vos row. Bare error (A), no LSB."""
    return wca(fs_error, unit="A", samples=samples, i_true=i, r_nom=r_nom,
               r_tol=shunt.tolerance(code).with_typ(0.0), r_tcr=shunt.tcr(r_nom).with_typ(0.0), t_c=TA,
               gserr=GSERR, gs_drift=GS_DRIFT, vos25=ina.param(vos_key), vos_drift=VOS_DRIFT, cal_gain=cal_gain)


def worst_fs(r_nom, code, cal_gain, i=I_MAX):
    """Bare worst-case current error (Param, A) over both Vos rows: the larger one is kept, as in section 4."""
    res = {k: fs_for(r_nom, code, cal_gain, k, SAMPLES, i) for k in VOS_KEYS}
    return max(res.items(), key=lambda kv: max(abs(kv[1].min), abs(kv[1].max)))


def fs_mag(p):
    """Worst-case magnitude (A) of a bare error Param plus the 1 LSB term."""
    return max(abs(p.min - LSB), abs(p.max + LSB))


def stock_status(code, value):
    """(match, stock units) from STOCK_EXACT: MATCH_FAILED unless the (code, value) pair is listed."""
    for mohm, stock in STOCK_EXACT[code].items():
        if abs(value * 1e3 - mohm) <= 1e-6 * mohm:
            return "MATCH_EXACT", stock
    return "MATCH_FAILED", None


def stock_label(match, stock):
    if match == "MATCH_FAILED":
        return "MATCH_FAILED"
    if stock is None:
        return "MATCH_EXACT, no offer returned (unknown)"
    if stock == 0:
        return "MATCH_EXACT, stock 0"
    return f"MATCH_EXACT, stock {stock:,}"


# Fitting values for every (ADCRANGE, code): full scan of the library values, worst-case Vsense at I_MAX.
FITTING = {}
for adc in (0, 1):
    for code_ in shunt.tolerances():
        tol_ = shunt.tolerance(code_)
        fits = []
        for v in sorted(shunt.values(tol=code_), reverse=True):
            vs_ = wca(vsense_worst, unit="V", i=I_MAX, r0=v, tol=tol_, tcr=shunt.tcr(v), t_c=TA)
            if vs_.max <= ADC_LIM[adc]:
                fits.append((v, vs_))
        FITTING[(adc, code_)] = fits

# The snapshot must match the ADCRANGE 0 fitting pairs it was queried on: every listed pair must fit, and the number of
# queried pairs (STOCK_QUERIED) must equal the number of fitting pairs now. A changed fit set means re-run the stock check.
for code_, pairs in STOCK_EXACT.items():
    fit0 = {round(v * 1e3, 6) for v, _ in FITTING[(0, code_)]}
    for mohm in pairs:
        assert round(mohm, 6) in fit0, f"stock snapshot lists {code_} {mohm} mohm, which does not fit ADCRANGE 0"
assert sum(len(FITTING[(0, c_)]) for c_ in shunt.tolerances()) == STOCK_QUERIED, \
    "the fitting set changed since the stock snapshot; re-run the stock check"


def evaluate(adc, code_, value, vs_):
    """One candidate row: sense, stock, power, MWV, full-scale error at I_MAX and I_LOW, SHUNT_CAL."""
    tol_ = shunt.tolerance(code_)
    tcr_ = shunt.tcr(value)
    raw = ina.calc.shunt_cal(CURRENT_LSB, value, adcrange=adc)
    cal_gain = round(raw) / raw
    match, stock = stock_status(code_, value)
    _, fs_bare = worst_fs(value, code_, cal_gain, I_MAX)
    _, fs_low = worst_fs(value, code_, cal_gain, I_LOW)
    pw = wca(power_worst, unit="W", i=I_MAX, r0=value, tol=tol_, tcr=tcr_, t_c=TA)
    r_w = wca(rworst, unit="ohm", r0=value, tol=tol_, tcr=tcr_, t_c=TA).max
    mwv = shunt.calc.max_working_voltage(RATED, r_w)
    margin = ADC_LIM[adc] - vs_.max
    row = dict(
        adc=adc, code=code_, value=value, mpn=shunt.mpn(value, tol=code_), match=match, stock=stock,
        stock_text=stock_label(match, stock), vs=vs_, vs_margin=margin, headroom=margin / ADC_LIM[adc],
        fs_bare=fs_bare, fs_mag=fs_mag(fs_bare), fs_pct=fs_mag(fs_bare) / I_MAX,
        fs_low_mag=fs_mag(fs_low), fs_low_pct=fs_mag(fs_low) / I_LOW,
        pw_max=pw.max, pw_margin=RATED - pw.max, mwv=mwv, raw=raw, cal=round(raw), cal_gain=cal_gain,
    )
    row["stocked"] = stock is not None and stock > 0
    row["eligible"] = (row["stocked"] and margin >= 0 and row["pw_margin"] >= 0 and mwv >= vs_.max)
    return row


# Candidate rows: the TOP_N largest fitting values per (ADCRANGE, code), plus every value with a MATCH_EXACT.
ROWS = []
for (adc, code_), fits in FITTING.items():
    for rank, (value, vs_) in enumerate(fits):
        match, _ = stock_status(code_, value)
        if rank < TOP_N or match == "MATCH_EXACT":
            ROWS.append(dict(evaluate(adc, code_, value, vs_), top=rank < TOP_N))

ranked = sorted([r for r in ROWS if r["eligible"]],
                key=lambda r: (r["headroom"] < HEADROOM_MIN, r["fs_mag"], -r["headroom"]))
if not ranked:
    raise SystemExit("no stocked WSK2512 candidate passes the sense, power and MWV checks")
SEL = ranked[0]
if SEL["adc"] != ADCRANGE:
    raise SystemExit(f"selected shunt needs ADCRANGE {SEL['adc']}; this script is set to ADCRANGE {ADCRANGE}")

# Selected design values: every later section uses these names.
code = SEL["code"]  # tolerance code
R_NOM = SEL["value"]  # ohm, nominal value
TOL = shunt.tolerance(code)  # Param: fraction, min and max of the code's tolerance row
TCR = shunt.tcr(R_NOM)  # Param: TCR row (1/degC)
TOL_W = TOL.with_typ(0.0)  # bound-only row: nominal part at R_NOM (caller assumption, as in lib examples)
TCR_W = TCR.with_typ(0.0)  # bound-only row: nominal TCR 0
MPN = SEL["mpn"]
VS_SEL = SEL["vs"]  # Param, V: worst-case Vsense at I_MAX
VS_MARGIN = VDIFF_LIM - VS_SEL.max
CAL_GAIN = SEL["cal_gain"]  # SHUNT_CAL integer / exact value (1.0 when exact)
RSHUNT_STRICT = ina.calc.rshunt_max(vsense_max=VDIFF_LIM, i_max=I_MAX)  # DS p.35 Equation 8, strict upper bound


def cand_row_text(r):
    return [
        r["adc"], r["code"], f"+/-{pct(shunt.tolerance(r['code']).max)} %", f"{r['value'] * 1e3:.4g}", r["mpn"],
        r["stock_text"], mv(r["vs"].max), mv(r["vs_margin"]), pct(r["headroom"]),
        f"{r['fs_mag']:.4g}", pct(r["fs_pct"]), pct(r["fs_low_pct"]),
    ]


md += ["## 1. Shunt selection", ""]
md += [
    f"Sense limit: `vdiff_adcr0_full` max = {mv(VDIFF_LIM)} mV (ADCRANGE 0, DS p.5); `vdiff_adcr1_full` max = "
    f"{mv(ADC_LIM[1])} mV (ADCRANGE 1). Worst-case sense voltage = I_MAX x R_worst, R_worst from `resistance_worst` over "
    f"the code's tolerance row and the value's TCR row, across TA {TA.min:g} to {TA.max:g} degC. Full-scale error (FS) is "
    f"the worst magnitude at I_MAX over all corners plus 1 LSB, for the shunt with its own SHUNT_CAL; 100 mA error is the "
    f"same at {I_LOW:g} A.",
    "",
    "**Candidate table.** The largest fitting values per (ADCRANGE, code) (step 1), plus any value with stock in the "
    f"snapshot. Stock: `pcb bom -f json` sourcing on {STOCK_DATE}, MATCH_EXACT plus offer stock units. "
    "Not a guarantee; re-check before ordering.",
    "",
]
cand_rows = sorted([r for r in ROWS], key=lambda r: (r["adc"], r["code"], -r["value"]))
md += mdtable(["ADCRANGE", "Code", "Tolerance", "Value (mohm)", "MPN", "Stock (snapshot)", "Vsense worst (mV)",
               "Margin (mV)", "Headroom (% of range)", "FS error at 5 A (A)", "FS error at 5 A (%)",
               "Error at 100 mA (%)"],
              [cand_row_text(r) for r in cand_rows])
md += [
    "",
    "**Ranking, stocked candidates only.** Eligible = stock > 0, Vsense inside the ADC range, shunt power inside the 70 degC "
    "rating, and (P x R)^1/2 at or above the worst Vsense. Order: headroom at or above "
    f"{HEADROOM_MIN * 100:g} % first, then lowest worst-case FS error, then larger headroom. BOM reuse: not applicable "
    "(single shunt on the board).",
    "",
]
rank_rows = []
for n, r in enumerate(ranked, start=1):
    rank_rows.append([n] + cand_row_text(r)[:5] + [f"{r['stock']:,}", mv(r["vs"].max), pct(r["headroom"]),
                                                    f"{r['fs_mag']:.4g}", pct(r["fs_pct"]), pct(r["fs_low_pct"]),
                                                    f"{r['raw']:.4f} -> {r['cal']}"])
rank_hdr = ["Rank", "ADCRANGE", "Code", "Tolerance", "Value (mohm)", "MPN", "Stock units", "Vsense worst (mV)",
            "Headroom (%)", "FS error at 5 A (A)", "FS error at 5 A (%)", "Error at 100 mA (%)", "SHUNT_CAL raw -> reg"]
md += mdtable(rank_hdr, rank_rows)
md += [
    "",
    f"**Selected:** {R_NOM * 1e3:g} mohm, code {code} (+/-{pct(TOL.max)} %), MPN **`{MPN}`** (`calc.mpn`, WSK2512 decode "
    f"rule DS p.1). Stock snapshot: {SEL['stock_text']}. Lowest worst-case FS error of the stocked, eligible candidates; "
    f"headroom {pct(SEL['headroom'])} % of the range.",
    "",
    f"Sense margin: worst-case Vsense {mv(VS_SEL.max)} mV vs {mv(VDIFF_LIM)} mV limit = **{mv(VS_MARGIN)} mV margin "
    f"({pct(VS_MARGIN / VDIFF_LIM)} % of the limit)**.",
    "",
    f"Cross-check, INA229 `rshunt_max` (`vdiff_adcr0_full` max / I_MAX, DS p.35 Equation 8, nominal): "
    f"{RSHUNT_STRICT * 1e3:.4g} mohm. Selected value {R_NOM * 1e3:g} mohm is below it: "
    f"{'PASS' if R_NOM < RSHUNT_STRICT else 'FAIL'}.",
    "",
]
best_unstocked = min([r for r in ROWS if r["adc"] == 0 and not r["stocked"]], key=lambda r: r["fs_mag"])
md += [
    f"Best candidate ignoring stock (ADCRANGE 0, lowest FS error): {best_unstocked['value'] * 1e3:g} mohm code "
    f"{best_unstocked['code']} (`{best_unstocked['mpn']}`), {best_unstocked['stock_text']}, FS error "
    f"{best_unstocked['fs_mag']:.4g} A ({pct(best_unstocked['fs_pct'])} %). Not orderable from the snapshot.",
    "",
    "ADCRANGE 1 candidates are compared with the same Vos rows (the library has no ADCRANGE-1 offset row). With a "
    "smaller shunt the offset term Vos / R is larger, so they rank lower for that reason alone.",
    "",
]


# ======================================================================================================================
# 2. Shunt power
# ======================================================================================================================
PW = wca(power_worst, unit="W", i=I_MAX, r0=R_NOM, tol=TOL, tcr=TCR, t_c=TA)
PW_MARGIN = RATED - PW.max
R_WORST = wca(rworst, unit="ohm", r0=R_NOM, tol=TOL, tcr=TCR, t_c=TA).max
MWV = shunt.calc.max_working_voltage(RATED, R_WORST)  # DS p.2, (P x R)^1/2 at the rated power

md += ["## 2. Shunt power", ""]
md += [
    f"Worst-case dissipation at I_MAX = {I_MAX:g} A and R_worst (TA {TA.max:g} degC is the worst corner): "
    f"**{PW.max:.4g} W**. Rating: `p70` max = {shunt.param('p70').max:g} W at 70 degC ambient (DS p.1, P70 degC), "
    f"`rated_power` at TA = {TA.max:g} degC (derating knee `derate_knee` = "
    f"{shunt.param('derate_knee').typ:g} degC and zero `derate_zero` = {shunt.param('derate_zero').typ:g} degC are "
    f"read off the DS p.3 graph) x derate {DERATE:g} = {RATED:.4g} W.",
    "",
]
md += mdtable(["Check", "Worst-case (W)", "Rated (W)", "Margin (W)", "Margin (% of rating)", "Result"], [[
    "Shunt dissipation at 5 A, 70 degC", f"{PW.max:.4g}", f"{RATED:.4g}", f"{PW_MARGIN:.4g}",
    pct(PW_MARGIN / RATED), status(PW_MARGIN),
]])
md += [
    "",
    f"Rated working voltage (`max_working_voltage`, DS p.2 (P x R)^1/2): {MWV:.4g} V at R_worst "
    f"{R_WORST * 1e3:.4g} mohm, vs worst Vsense {mv(VS_SEL.max)} mV: {status(MWV - VS_SEL.max)}.",
    "",
    "Self-heating of the shunt is not modelled (the DS rates against ambient and gives no thermal resistance for the "
    "element); see Limits.",
    "",
]


# ======================================================================================================================
# 3. Firmware constants
# ======================================================================================================================
SHUNT_CAL_RAW = ina.calc.shunt_cal(CURRENT_LSB, R_NOM, adcrange=ADCRANGE)  # DS p.30 Equation 2
SHUNT_CAL = round(SHUNT_CAL_RAW)
assert abs(SHUNT_CAL / SHUNT_CAL_RAW - CAL_GAIN) < 1e-12, "candidate and design SHUNT_CAL disagree"
CAL_ERR = SHUNT_CAL / SHUNT_CAL_RAW - 1.0  # fraction, reading gain error from the integer register
CAL_EXACT = abs(SHUNT_CAL_RAW - SHUNT_CAL) < 1e-9

md += ["## 3. Firmware constants", ""]
md += mdtable(["Constant", "Value", "Equation / cite"], [
    ["CURRENT_LSB", f"{CURRENT_LSB:.10g} A/LSB ({CURRENT_LSB * 1e6:.6g} uA)", "I_MAX / 2^19, DS p.30 Equation 3 (`calc.current_lsb`)"],
    ["SHUNT_CAL (raw)", f"{SHUNT_CAL_RAW:.6f}", "13107.2e6 x CURRENT_LSB x R_NOM, DS p.30 Equation 2 (`calc.shunt_cal`, ADCRANGE 0)"],
    ["SHUNT_CAL (register)", f"**{SHUNT_CAL}**", "rounded integer written to the register"],
])
md += [
    "",
    f"R_NOM used in SHUNT_CAL = {R_NOM * 1e3:g} mohm ({MPN}). "
    + ("SHUNT_CAL is an exact integer, so the rounding term is zero." if CAL_EXACT else
       f"SHUNT_CAL rounding error = {CAL_ERR * 100:+.4g} % of reading. It is a gain term in section 4 (`cal_gain`)."),
    "",
]


# ======================================================================================================================
# 4. Full-scale current error
# ======================================================================================================================
def fs_wca(vos_key, samples, i=I_MAX):
    """wca of fs_error at the selected shunt over the full input set; vos_key chooses the Vos row."""
    return fs_for(R_NOM, code, CAL_GAIN, vos_key, samples, i)


vos_cmp = {k: fs_wca(k, SAMPLES) for k in VOS_KEYS}
vos_mag = {k: max(abs(w.min), abs(w.max)) for k, w in vos_cmp.items()}
VOS_KEY = "vos_vcm48_25c" if vos_mag["vos_vcm48_25c"] >= vos_mag["vos_vcm0_25c"] else "vos_vcm0_25c"
FS = vos_cmp[VOS_KEY]
FS_CORNERS = fs_wca(VOS_KEY, 0)
FS_MIN = FS.min - LSB
FS_MAX = FS.max + LSB
FS_TYP = FS.typ
assert abs(fs_mag(FS) - SEL["fs_mag"]) < 1e-12, "section 4 and the candidate ranking disagree on the FS error"

ZERO = dict(r_nom=R_NOM, r_tol=0.0, r_tcr=0.0, t_c=25.0, gserr=0.0, gs_drift=0.0, vos25=0.0, vos_drift=0.0,
            cal_gain=1.0)
term_specs = [
    ("Shunt tolerance (code " + code + ")", dict(r_tol=TOL_W)),
    ("Shunt TCR (TA 0-70 degC)", dict(r_tcr=TCR_W, t_c=TA)),
    ("VOS + drift", dict(vos25=ina.param(VOS_KEY), vos_drift=VOS_DRIFT, t_c=TA)),
    ("Gain error + drift", dict(gserr=GSERR, gs_drift=GS_DRIFT, t_c=TA)),
    ("SHUNT_CAL integer rounding", dict(cal_gain=CAL_GAIN)),
]
term_rows = []
term_max_sum = 0.0
for name, kw in term_specs:
    w = wca(fs_error, unit="A", samples=0, i_true=I_MAX, **{**ZERO, **kw})
    term_rows.append((name, w))
    term_max_sum += max(abs(w.min), abs(w.max))
LSB_P = Param(min=-LSB, typ=0.0, max=LSB, unit="A")

md += ["## 4. Full-scale current error at 5 A (TA 0 to 70 degC)", ""]
md += [
    f"Selected shunt: {R_NOM * 1e3:g} mohm, code {code}, `{MPN}`. Inputs (INA229 INPUTS map for `current_error`, plus "
    "the shunt):",
    "",
]
md += mdtable(["Term", "Min", "Max", "Source"], [
    ["Shunt tolerance", f"-{pct(TOL.max)} %", f"+{pct(TOL.max)} %", f"`tol_{code.lower()}`, DS p.1"],
    ["Shunt TCR", f"{TCR.min * 1e6:g} ppm/degC", f"{TCR.max * 1e6:g} ppm/degC", "`tcr_5m_200m`, DS p.2"],
    ["Vos (VCM choice)", f"{ina.param(VOS_KEY).min * 1e6:g} uV", f"{ina.param(VOS_KEY).max * 1e6:g} uV",
     f"`{VOS_KEY}`, DS p.5"],
    ["dVos/dT", f"{VOS_DRIFT.min * 1e9:g} nV/degC", f"{VOS_DRIFT.max * 1e9:g} nV/degC", "`vos_drift_full`, DS p.5"],
    ["Gain error GSERR", pct(GSERR.min) + " %", pct(GSERR.max) + " %", "`gserr_25c`, DS p.5"],
    ["Gain drift GS_DRFT", f"{GS_DRIFT.min * 1e6:g} ppm/degC", f"{GS_DRIFT.max * 1e6:g} ppm/degC", "`gs_drift_25c`, DS p.5 (no typ)"],
    ["SHUNT_CAL rounding", f"{CAL_ERR * 100:+.4g} %", f"{CAL_ERR * 100:+.4g} %", f"`SHUNT_CAL` {SHUNT_CAL} vs raw {SHUNT_CAL_RAW:.4f}, DS p.30 Eq. 2"],
    ["1 LSB", f"-{LSB * 1e6:.4g} uA", f"+{LSB * 1e6:.4g} uA", "`current_lsb(I_MAX)`, DS p.30 Eq. 3 (added separately)"],
])
md += [
    "",
    f"**Vos row choice.** Both rows give +/-1 uV max at 25 degC (`vos_vcm48_25c` and `vos_vcm0_25c`, DS p.5). "
    f"Worst-case full-scale error magnitude: VCM = 48 V row {vos_mag['vos_vcm48_25c']:.6g} A, "
    f"VCM = 0 V row {vos_mag['vos_vcm0_25c']:.6g} A. "
    f"Chosen: `{VOS_KEY}`, because "
    + ("the two rows give the same worst-case magnitude, so the INPUTS map entry is kept. "
       if abs(vos_mag["vos_vcm48_25c"] - vos_mag["vos_vcm0_25c"]) < 1e-15
       else "it gives the larger worst-case magnitude. ")
    + "Neither row is at the 12-40 V bus voltage; the bus is between the two conditions (open item).",
    "",
    "Error rows are in A, computed by wca over every corner; percent is of the 5 A reading.",
    "",
]
rows = []
for name, w in term_rows:
    rows.append([name, f"{w.min:.4g}", f"{w.max:.4g}", pct(w.min / I_MAX), pct(w.max / I_MAX)])
rows.append(["1 LSB (conservative, +/- one step)", f"{LSB_P.min:.4g}", f"{LSB_P.max:.4g}",
             pct(LSB_P.min / I_MAX), pct(LSB_P.max / I_MAX)])
md += mdtable(["Contributor (this term alone, others at zero)", "Min (A)", "Max (A)", "Min (%)", "Max (%)"], rows)
md += [
    "",
    f"Sum of individual contributor maxima = {term_max_sum:.6g} A; the combined worst case without the LSB is "
    f"{FS.max:.6g} A, which is {FS.max - term_max_sum:.3g} A MORE than that sum. The excess is the cross product of "
    f"the shunt and gain terms (each multiplies the current), so the combined corner, not the sum, is the number to use.",
    "",
    "**Full-scale result (all terms, worst-case corners, plus 1 LSB):**",
    "",
]
md += mdtable(["Quantity", "Min", "Typ", "Max"], [
    ["Error (A)", f"{FS_MIN:.4g}", f"{FS_TYP:.4g}", f"{FS_MAX:.4g}"],
    ["Error (% of 5 A reading)", pct(FS_MIN / I_MAX), pct(FS_TYP / I_MAX), pct(FS_MAX / I_MAX)],
])
md += [
    "",
    f"Typ = all terms at their DS typical value of zero (bound-only rows are centred; typ_pm rows are centred with the "
    f"DS typical spread kept separately: gserr typ spread {pct(GSERR.typ_spread)} %, Vos typ spread "
    f"{ina.param(VOS_KEY).typ_spread * 1e6:g} uV). It is the nominal part at 25 degC, not a statistical typical.",
    "",
    f"Sampling check: corners only (samples = 0) give min {FS_CORNERS.min:.6g} A, max {FS_CORNERS.max:.6g} A; "
    f"with {SAMPLES} interior samples min {FS.min:.6g} A, max {FS.max:.6g} A. Difference: "
    f"{abs(FS.min - FS_CORNERS.min):.2e} A low side, {abs(FS.max - FS_CORNERS.max):.2e} A high side.",
    "",
]


# ======================================================================================================================
# 5. Error vs current plot
# ======================================================================================================================
CURRENTS = [I_PLOT_LO * (I_MAX / I_PLOT_LO) ** (k / (N_PLOT - 1)) for k in range(N_PLOT)]
lo_a, typ_a, hi_a = [], [], []
for i_pt in CURRENTS:
    w = fs_wca(VOS_KEY, SAMPLES, i=i_pt)
    lo_a.append(w.min - LSB)
    typ_a.append(w.typ)
    hi_a.append(w.max + LSB)

lo_pct = [100 * e / i_pt for e, i_pt in zip(lo_a, CURRENTS)]
typ_pct = [100 * e / i_pt for e, i_pt in zip(typ_a, CURRENTS)]
hi_pct = [100 * e / i_pt for e, i_pt in zip(hi_a, CURRENTS)]
BLUE, INK, GRID, MUTED = "#2a78d6", "#0b0b0b", "#e4e4e0", "#52514e"

fig, (ax1, ax2) = plt.subplots(2, 1, sharex=True, figsize=(7.5, 7.2))
for ax, lo, typ, hi, unit_label, scale in (
    (ax1, lo_pct, typ_pct, hi_pct, "Error (% of reading)", 1.0),
    (ax2, [v * 1e3 for v in lo_a], [v * 1e3 for v in typ_a], [v * 1e3 for v in hi_a], "Error (mA)", 1.0),
):
    ax.fill_between(CURRENTS, lo, hi, color=BLUE, alpha=0.12, linewidth=0)
    ax.plot(CURRENTS, lo, color=BLUE, lw=1.3, ls="--", label="min")
    ax.plot(CURRENTS, hi, color=BLUE, lw=1.3, label="max")
    ax.plot(CURRENTS, typ, color=INK, lw=1.0, label="typ (nominal)")
    ax.axhline(0, color=MUTED, lw=0.6)
    ax.set_xscale("log")
    ax.set_ylabel(unit_label, color=MUTED)
    ax.grid(True, color=GRID, lw=0.6)
    ax.tick_params(colors=MUTED)
ax1.set_title(f"INA229 + {R_NOM * 1e3:g} mohm WSK2512 full-scale error vs load current, TA 0-70 degC", color=INK,
              fontsize=11)
ax1.legend(frameon=False, loc="upper right", labelcolor=INK)
ax2.set_xlabel("Load current (A), log scale", color=MUTED)
fig.tight_layout()
PNG = OUT / "ina229_current_error.png"
OUT.mkdir(exist_ok=True)
fig.savefig(PNG, dpi=130)
plt.close(fig)

md += ["## 5. Error spread vs current (100 mA to 5 A)", ""]
md += [
    f"{N_PLOT} log-spaced points. Each point is the same worst-case function as section 4, with "
    f"the 1 LSB term added to min and max. Top: % of reading. Bottom: mA.",
    "",
    f"![INA229 current error](ina229_current_error.png)",
    "",
    f"Example points: at 0.1 A min {lo_pct[0]:.3g} %, max {hi_pct[0]:.3g} %; at 5 A min {lo_pct[-1]:.3g} %, "
    f"max {hi_pct[-1]:.3g} %. The 1 LSB term (+/-{LSB * 1e6:.4g} uA) is {LSB / CURRENTS[0] * 100:.3g} % of reading at "
    f"0.1 A and {LSB / CURRENTS[-1] * 100:.2g} % at 5 A; the shunt and gain terms dominate across the range.",
    "",
]


# ======================================================================================================================
# 6. Bus voltage error
# ======================================================================================================================
def vbus_error(v_true, vos_bus, vos_bus_drift, vos_bus_psrr, dvs, gberr, gb_drift, t_c):
    """Not a DS equation: bus-voltage measurement error, measured minus true (V).

    Derivation: measured = v_true x (1 + gberr + gb_drift x (T - 25)) + vos_bus + vos_bus_drift x (T - 25)
                           + vos_bus_psrr x dVS
    where gberr = GBERR (DS p.5, VBUS voltage gain error), gb_drift = GB_DRFT (DS p.5), vos_bus = Vos_bus at 25 degC
    (DS p.5), vos_bus_drift = dVos/dT for VBUS (DS p.5), vos_bus_psrr = VBUS offset vs VS (DS p.5, mV/V converted to a
    fraction by the loader) and dVS = VS deviation from the PSRR reference, V. The linear-in-T temperature recipe is
    SPEC step 2. The result is multilinear in the wca inputs, so corners are exact.
    v_true: V. vos_bus, vos_bus_drift: V, V/degC. vos_bus_psrr: V/V. dvs: V. gberr, gb_drift: fraction, 1/degC.
    t_c: degC. Returns: V (signed; positive = reads high)."""
    gain = gberr + gb_drift * (t_c - 25.0)
    measured = v_true * (1.0 + gain) + vos_bus + vos_bus_drift * (t_c - 25.0) + vos_bus_psrr * dvs
    return measured - v_true


VOS_BUS = ina.param("vos_bus_25c")  # DS p.5, Vos_bus, +/-2.5 mV max, typ +/-1 mV
VOS_BUS_DRIFT = ina.param("vos_bus_drift_full")  # DS p.5, dVos/dT VBUS, +/-20 uV/degC
PSRR_P = ina.param("vos_bus_psrr_25c")  # DS p.5, VBUS offset vs VS, typ +/-0.25 mV/V, NO min/max
GBERR = ina.param("gberr_25c")  # DS p.5, GBERR +/-0.05 %
GB_DRIFT = ina.param("gb_drift_25c").with_typ(0.0)  # DS p.5, GB_DRFT +/-20 ppm/degC, no typ
LSB_VBUS = ina.param("lsb_vbus_25c").typ  # DS p.5, 1 LSB step size, bus voltage, 195.3125 uV
assert PSRR_P.min is None and PSRR_P.max is None, "PSRR row now has bounds; revisit the typical-only treatment"
DVS = Param(min=-VS_NOM * VS_TOL, typ=0.0, max=VS_NOM * VS_TOL, unit="V")  # VS span about 3.3 V (design choice)

bus_rows = []
for v in VBUS_TEST:
    w = wca(vbus_error, unit="V", samples=0, v_true=v, vos_bus=VOS_BUS, vos_bus_drift=VOS_BUS_DRIFT,
            vos_bus_psrr=0.0, dvs=0.0, gberr=GBERR, gb_drift=GB_DRIFT, t_c=TA)
    psrr = wca(vbus_error, unit="V", samples=0, v_true=v, vos_bus=0.0, vos_bus_drift=0.0,
               vos_bus_psrr=PSRR_P.typ_spread, dvs=DVS, gberr=0.0, gb_drift=0.0, t_c=25.0)
    bus_rows.append([
        f"{v:g}",
        mv(w.min), mv(w.typ), mv(w.max),
        pct(w.min / v), pct(w.max / v),
        mv(w.min - LSB_VBUS), mv(w.max + LSB_VBUS),
        f"+/-{mv(max(abs(psrr.min), abs(psrr.max)))}",
    ])

md += ["## 6. Bus voltage worst-case error", ""]
md += [
    "Terms and cites: `vos_bus_25c` (Vos_bus, DS p.5, min -2.5 mV, max 2.5 mV; PSRR test header 25 degC), "
    "`vos_bus_drift_full` (DS p.5, min -20 uV/degC, max 20 uV/degC), `gberr_25c` (DS p.5, +/-0.05 %), "
    "`gb_drift_25c` (DS p.5, +/-20 ppm/degC, blank condition read as the 25 degC header), "
    "`lsb_vbus_25c` (DS p.5 / Table 7-10, 195.3125 uV).",
    "",
    f"`vos_bus_psrr_25c` (DS p.5, VBUS offset vs VS) prints only a typ, +/-0.25 mV/V in the TYP column and no MIN or MAX. "
    f"It is not a bound and is excluded from min and max (typ-only row). Its typical size is shown in the last column with "
    f"dVS = +/-{mv(DVS.max)} mV (3.3 V +/-3 %). The PSRR row's test condition is VS = 2.7 to 5.5 V, so the dVS reference "
    f"is an assumption (open item).",
    "",
]
md += mdtable(["V_true (V)", "Min err (mV)", "Typ err (mV)", "Max err (mV)", "Min (% of V)", "Max (% of V)",
               "Min incl. 1 LSB (mV)", "Max incl. 1 LSB (mV)", "PSRR typical, not bounded (mV)"], bus_rows)
md += [
    "",
    f"1 LSB = {LSB_VBUS * 1e6:.4f} uV (`lsb_vbus_25c`), added to min and max. Typ = nominal part at 25 degC with all bound "
    "rows centred (their DS typical spread is not added).",
    "",
    "The bus offset (`vos_bus_25c`) is specified only at VBUS = 20 mV, and the bus gain error at the table header "
    "condition. Their use at 12-40 V assumes the errors are flat with bus voltage (open item).",
    "",
]


# ======================================================================================================================
# 7. Ratings
# ======================================================================================================================
def rating_row(name, lo, hi, rating, key, s, u):
    """Margin = min(lo - rating.min, rating.max - hi); a missing rating bound is unlimited."""
    r_lo = -math.inf if rating.min is None else rating.min
    r_hi = math.inf if rating.max is None else rating.max
    margin = min(lo - r_lo, r_hi - hi)
    shown_r = f"{'-inf' if r_lo == -math.inf else f'{r_lo * s:.4g}'} to {'inf' if r_hi == math.inf else f'{r_hi * s:.4g}'} {u}"
    return [name, f"{lo * s:.4g} to {hi * s:.4g} {u}", f"{key}: {shown_r}", f"{margin * s:.4g} {u}", status(margin)]


ratings = [
    rating_row("Bus voltage VBUS", VBUS_LO, VBUS_HI, ina.param("vbus_abs"), "vbus_abs", 1, "V"),
    rating_row("Input common mode VIN+/VIN- (abs)", VBUS_LO, VBUS_HI, ina.param("vcm_abs"), "vcm_abs", 1, "V"),
    rating_row("Input common mode VIN+/VIN- (rec)", VBUS_LO, VBUS_HI, ina.param("vcm_rec"), "vcm_rec", 1, "V"),
    rating_row("Differential, hard load short (IN+ = 40 V, IN- = 0 V)", VBUS_HI, VBUS_HI, ina.param("vin_diff_abs"),
               "vin_diff_abs", 1, "V"),
    rating_row("VS nominal +/-3 % (rec)", VS_NOM * (1 - VS_TOL), VS_NOM * (1 + VS_TOL), ina.param("vs_rec"), "vs_rec", 1, "V"),
    rating_row("VS nominal +/-3 % (abs max)", VS_NOM * (1 - VS_TOL), VS_NOM * (1 + VS_TOL), ina.param("vs_abs"), "vs_abs", 1, "V"),
    rating_row("TA (rec)", TA.min, TA.max, ina.param("ta_rec"), "ta_rec", 1, "degC"),
    rating_row("Shunt sense voltage, worst case", -VS_SEL.max, VS_SEL.max, VDIFF, "vdiff_adcr0_full", 1e3, "mV"),
]
pw_margin_row = ["Shunt dissipation vs rating (70 degC)", f"0 to {PW.max:.4g} W", f"p70 x derate: {RATED:.4g} W",
                 f"{PW_MARGIN:.4g} W", status(PW_MARGIN)]
mwv_row = ["Shunt sense vs (P x R)^1/2", f"{VS_SEL.max * 1e3:.4g} mV", f"(P x R)^1/2 = {MWV * 1e3:.4g} mV",
           f"{(MWV - VS_SEL.max) * 1e3:.4g} mV", status(MWV - VS_SEL.max)]
md += ["## 7. Ratings", ""]
md += mdtable(["Check", "Applied (worst case)", "Rating (key)", "Margin", "Result"], ratings + [pw_margin_row, mwv_row])
md += [
    "",
    "Differential check: the hard-short differential equals the 40 V bus across the shunt (zero drop at the short). "
    "It sits exactly at the `vin_diff_abs` max of 40 V (DS p.3). Zero margin: OPEN ITEM (needs a clamp, a "
    "lower bus maximum, or a DS-approved exception).",
    "",
    "The VS row checks 3.3 V +/-3 % (3.201 to 3.399 V) against `vs_rec` (2.7 to 5.5 V, DS p.4); the "
    "`vs_abs` max is 6 V (DS p.3).",
    "",
]


# ======================================================================================================================
# 8. MISO note
# ======================================================================================================================
VOH = ina.param("voh_25c")
R_OUT_MAX = abs(VOH.min) / 1e-3  # DS p.6, VOH row: 0.4 V drop at the 1 mA test current in the row condition
md += ["## 8. MISO output note", ""]
md += [
    f"`voh_25c` (DS p.6, Logic output level high VOH, condition IOL = 1 mA as printed; min VS - 0.4 V): the DS bounds "
    f"the MISO output drop to 0.4 V at 1 mA, so the source resistance is bounded by R_out <= 0.4 V / 1 mA = "
    f"**{R_OUT_MAX:.0f} ohm** from the datasheet alone. The 33 ohm series resistor in the design is a placeholder "
    "DESIGN CHOICE; it is inside that bound but is not a derived value (OPEN ITEM). The row's condition prints IOL "
    "rather than IOH (DS ambiguity, noted in the library).",
    "",
]


# ======================================================================================================================
# 9. Limits of this analysis
# ======================================================================================================================
md += ["## 9. Limits of this analysis", ""]
md += [
    "- DC steady state only. No transient, no SPI timing, no ADC conversion-time effects.",
    "- 25 degC-only rows are extended by their drift rows (SPEC recipe step 2). Drift rows are used as printed; GS_DRFT and "
    "GB_DRFT are DS-printed under the 25 degC header (DS ambiguity).",
    "- Typical-only rows are not used as bounds: `vos_bus_psrr_25c` is shown separately (section 6), and the "
    "`typ` columns of bound rows are centred at zero, not the DS typical spread.",
    "- The shunt TCR reference temperature (25 degC) and the TCR validity span are not stated in the WSK2512 DS.",
    "- Stock is a `pcb bom` sourcing snapshot (section 1, 2026-11-15), not a guarantee; availability is the offer stock "
    "returned by the BOM tool, not a distributor order confirmation.",
    "- ADCRANGE 1 rows use the ADCRANGE 0 Vos rows (the library has no ADCRANGE-1 offset row). This is an assumption; the "
    "ranking does not depend on it because the ADCRANGE 1 shunts are 8 mohm and below.",
    "- Excluded terms: CMRR (`cmrr_full`), INL, DNL, INA229 internal "
    "self-heating and die-temperature effects, PCB and Kelvin trace resistance, shunt self-heating, the ADC noise, "
    "and the 1 LSB term is conservative (a quantizer is +/-0.5 LSB; 1 LSB was requested). SHUNT_CAL rounding is included "
    "(section 4).",
    "- The gain error row (`gserr_25c`) is specified at VCM = 24 V; the bus is 12-40 V (open item).",
    "- Shunt rating is a DS rating against ambient at 70 degC; no thermal resistance is given for the element.",
    "",
]

md += ["## Judgement calls", ""]
md += [
    f"- Shunt: the selected value {R_NOM * 1e3:g} mohm, code {code}, is the lowest worst-case FS error among stocked, "
    "eligible candidates (section 1). Stock units are a snapshot; the next lowest-error stocked parts are listed in the "
    "ranking.",
    "- Shunt tolerance and TCR are bound-only rows; their typ is set to zero in the FS and bus analyses (caller choice).",
    "- `gs_drift_25c` has no typ in the library and is centred at zero with `with_typ(0.0)`.",
    "- The dVS reference for the PSRR row is assumed to be the 3.3 V nominal (the row prints a VS range, not a "
    "single deviation).",
    "",
]

(OUT / "ina229_block.md").write_text("\n".join(md) + "\n")

# ---- console summary ----
print(f"Shunt: {R_NOM * 1e3:g} mohm, code {code}, MPN {MPN}, stock snapshot {STOCK_DATE}: {SEL['stock_text']}")
print(f"Vsense worst {mv(VS_SEL.max)} mV, margin {mv(VS_MARGIN)} mV ({pct(VS_MARGIN / VDIFF_LIM)} % of limit)")
print(f"Power worst {PW.max:.4g} W, rated {RATED:.4g} W, margin {PW_MARGIN:.4g} W ({pct(PW_MARGIN / RATED)} %)")
print(f"CURRENT_LSB {CURRENT_LSB:.10g} A/LSB, SHUNT_CAL raw {SHUNT_CAL_RAW:.6f} -> {SHUNT_CAL}")
print(f"FS error at {I_MAX:g} A: min {FS_MIN:.4g} A ({pct(FS_MIN / I_MAX)} %), typ {FS_TYP:.4g} A, "
      f"max {FS_MAX:.4g} A ({pct(FS_MAX / I_MAX)} %), VOS row {VOS_KEY}")
print(f"Error at {I_LOW:g} A: {pct(SEL['fs_low_pct'])} % worst magnitude")
print("Bus error table (mV):")
for r in bus_rows:
    print("  ", r)
print(f"Wrote {OUT / 'ina229_block.md'} and {PNG}")
