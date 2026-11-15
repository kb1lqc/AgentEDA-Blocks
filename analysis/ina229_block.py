# %% [markdown]
# # INA229 + WSK2512 shunt block: design-analysis generator
#
# This file is the SOURCE of `docs/design-analysis.md` and the PNG charts in `docs/design-analysis/`. The Markdown is
# generated: never edit it by hand. Run from the repo root:
#
#     python3 -m analysis.ina229_block        (exit 0 = everything regenerated)
#
# It is a jupytext "percent" file (`# %%` code cells, `# %% [markdown]` text cells), so it opens as a notebook in
# VS Code / Jupyter and stays a plain runnable module.
#
# Scope: 8 A bidirectional full scale, bus 12-40 V (high side), TA 0-70 degC, VS 3.3 V +/-3 %. Accuracy requirement:
# +/-1 % of reading at 8 A (ERR_LIMIT_PCT), judged on the SELF-HEATED worst case. ADCRANGE is a design variable (section
# 1); the design is ADCRANGE 0 (designer choice 2026-11-15).
# Every datasheet number comes from the component library (`part.param`, `part.inputs`, calc functions). Design choices
# are the constants in the DESIGN CHOICES cell. Worst case = `lib.wca` over every min/max corner. Where a function is not
# multilinear in its inputs (the ambient current error is quadratic in TA), wca also samples interior points.
#
# Thermal results (shunt self-heating, INA229 junction temperature) use a curve-based estimate of the shunt thermal
# resistance and typical-only datasheet values; every such result is labelled ESTIMATE in the report.

# %%
import datetime
import json
import math
from functools import partial
from pathlib import Path

from analysis import _zl  # noqa: F401  (side effect: puts zener-library on sys.path)
from analysis._zl import ZL_VERSION
from lib import Param, load, wca

import matplotlib.pyplot as plt  # imported after lib, which selects the Agg backend
from matplotlib.lines import Line2D

ANALYSIS = Path(__file__).resolve().parent
DATA = ANALYSIS / "data"  # committed inputs
DOCS = ANALYSIS.parent / "docs"
FIG_DIR = DOCS / "design-analysis"  # generated charts (PNG)
MD_PATH = DOCS / "design-analysis.md"  # generated report
STOCK_JSON = DATA / "shunt_stock_2026-11-15_rerank.json"  # second stock check (ADCRANGE re-rank); the first is kept
RUN_DATE = datetime.date.today().isoformat()

# %% [markdown]
# ## Design choices
# Every constant here is a DESIGN CHOICE (not a datasheet value), with the reason beside it.

# %%
# Designer decision 2026-11-15: 8 A full scale (was 5 A). Basis: the balanced rating from the maximum full-scale study
# (MF.0 to MF.9). For the WSK2512 10 mohm code B the study's maximum is 9 A, with thin power and temperature margins.
I_MAX = 8.0  # A, full-scale current, bidirectional
I_LOW = 0.1  # A, low-current point for the error tables
VBUS_LO, VBUS_HI = 12.0, 40.0  # V, high-side bus range
VBUS_TEST = [12.0, 18.0, 28.0, 36.0, 40.0]  # V, bus voltages for the bus-error table (task spec)
TA = Param(min=0.0, typ=25.0, max=70.0, unit="degC")  # ambient 0-70 degC; typ = 25 degC reference
T_REF = 25.0  # degC, reference temperature for the shunt TCR (WSK2512 DS states none; assumed 25 degC)
VS_NOM = 3.3  # V, INA229 supply (shared 3.3 V rail, spec.md)
VS_TOL = 0.03  # fraction, +/-3 % (spec.md)
VS_MAX = VS_NOM * (1 + VS_TOL)  # V, worst supply for supply-side budgets
VS_MIN = VS_NOM * (1 - VS_TOL)  # V, lowest supply (rating checks use it for the VIO high-level limit)
DERATE = 1.0  # shunt rating derate (spec.md: no derating)
SAMPLES = 2  # interior points per input for the ambient full-scale error (checked against samples = 0 in section 5)
N_PLOT = 40  # log-spaced current points for the error chart
I_PLOT_LO = 0.1  # A, error chart starts here
HEADROOM_MIN = 0.02  # fraction of the ADC range: ranking tie-break threshold (section 1)
R_MISO = 33.0  # ohm, MISO series resistor: PLACEHOLDER DESIGN CHOICE (open item, section 12)
R_PULLUP = 10e3  # ohm, ALERT_N pull-up (spec.md block design, DS Fig 8-2); tolerance and TCR come from the library
ERR_LIMIT_PCT = 1.0  # % of reading at I_MAX: designer requirement 2026-11-15 (+/-1 % of reading, 8 A full scale)
T_ELEM_LIMIT = 170.0  # degC, ASSUMPTION: element limit read as the derating curve's zero point (derate_zero). NOT t_oper,
# which the library records as an AMBIENT rating
N_PLOT_PWR = 30  # log-spaced current points for the power-error chart
T_SAMPLES_PWR = 2  # interior ambient points for the joint power error (checked against 9 in section 8)
# THE design shunt: (ADCRANGE, WSK2512 tolerance code, value in mohm). Designer choice 2026-11-15: 10 mohm code B
# (WSK2512R0100BEA) at ADCRANGE 0, the lowest self-heated error at I_MAX of the candidates that pass 1 % (section 1.4; its FAIR
# availability is an open item). Switching the design is a change of this ONE tuple; the board's rshunt / rshunt_tol /
# vsense_max then follow (ina229.zen, spec.md).
SHUNT_SELECTED = (0, "B", 10.0)
ADCRANGE = SHUNT_SELECTED[0]  # INA229 CONFIG ADCRANGE of the design: 0 = +/-163.84 mV, 1 = +/-40.96 mV
code = SHUNT_SELECTED[1]  # WSK2512 tolerance code of the design
R_NOM = SHUNT_SELECTED[2] * 1e-3  # ohm, nominal value of the design

# %% [markdown]
# ## Stock snapshot (committed input)
# `analysis/data/shunt_stock_2026-11-15_rerank.json` is the recorded `pcb bom -f json` sourcing result for every WSK2512
# (value, code) that fits the 5 A full scale (the snapshot's fit set; the I_MAX fit set is a subset of it) with self-heated worst-case Vsense at ADCRANGE 0 (169 pairs; 63 of them also fit
# ADCRANGE 1). It is a sourcing result, not a datasheet value. A MATCH_EXACT row's stock is the sum of its offers' stock
# (None when the BOM returned no offer). Snapshot only: stock changes, so re-run the stock check before ordering. The
# earlier snapshot `analysis/data/shunt_stock_2026-11-15.json` (ambient-only fit) is kept unchanged and is not read here.

# %%
def load_stock(path):
    """Return (raw record, {(code, value_mohm): {match, offers, adc_fit}}) for every queried candidate. Offers are the
    BOM distributor listings (distributor, region, stock), kept even at zero stock."""
    raw = json.loads(path.read_text())
    table = {}
    for row in raw["rows"]:
        offers = (row.get("availability") or {}).get("offers") or []
        table[(row["code"], round(float(row["value_mohm"]), 6))] = dict(
            match=row["bom_match"],
            offers=[(o["distributor"], o["region"], o["stock"]) for o in offers],
            adc_fit=row["adc_fit"],
        )
    return raw, table


STOCK_RAW, STOCK_TABLE = load_stock(STOCK_JSON)
STOCK_DATE = STOCK_RAW["date"]

# %% [markdown]
# ## Library
# Parts are loaded through the zener-library `lib` at the version in `analysis/_zl.py` (ZL_VERSION). Every value below is
# read from a library row, and the printed `cite` of that row is what the tables show.

# %%
ina = load("TexasInstruments/INA229AIDGS")
shunt = load("Vishay/WSK2512")
erj = load("Panasonic/ERJ-3EKF")


def cite(p):
    """The library's cite text for one parameter row."""
    return p.cite


VDIFF = ina.param("vdiff_adcr0_full")  # shunt voltage input range, ADCRANGE 0 (signed, symmetric)
VDIFF1 = ina.param("vdiff_adcr1_full")  # shunt voltage input range, ADCRANGE 1 (signed, symmetric)
ADC_LIM = {0: VDIFF.max, 1: VDIFF1.max}  # V, full scale per ADCRANGE
VDIFF_LIM = ADC_LIM[0]  # V, 163.84 mV (ADCRANGE 0)
LSB_VSH = {0: ina.param("lsb_vshunt_adcr0_25c").typ, 1: ina.param("lsb_vshunt_adcr1_25c").typ}  # V per count
CURRENT_LSB = ina.calc.current_lsb(I_MAX)  # DS p.30 Equation 3 (the same for both ADCRANGE settings)
LSB = CURRENT_LSB  # 1 LSB term, A
P70 = shunt.param("p70").max  # W, rating at 70 degC ambient (DS p.1)
KNEE = shunt.param("derate_knee").typ  # degC, derating knee (DS p.3 graph, curve)
ZERO = shunt.param("derate_zero").typ  # degC, 0 % rated power (DS p.3 graph, curve)
T_OPER_MAX = shunt.param("t_oper").max  # degC, shunt operating range top, an AMBIENT rating (DS p.2), not the element limit
RATED_KW = shunt.inputs("rated_power")  # {p70, knee_c, zero_c} as floats (DS p.1 and p.3)
RATED_RAW = shunt.calc.rated_power(ta_c=TA.max, **RATED_KW)  # p70 at 70 degC (knee = 70 degC)
RATED = RATED_RAW * DERATE
THETA = (ZERO - KNEE) / P70  # degC/W, ESTIMATE (theta_est, section 3.2): curve-based hot-spot thermal resistance

BASE_IN = ina.inputs("current_error")  # {vos25, vos_drift, gserr, gs_drift} from the INPUTS map (vos25 replaced below)
GSERR = BASE_IN["gserr"]  # gserr_25c: DS p.5 GSERR, +/-0.05 %
GS_DRIFT = BASE_IN["gs_drift"].with_typ(0.0)  # gs_drift_25c: DS p.5 GS_DRFT, +/-20 ppm/degC, no typ
VOS_DRIFT = BASE_IN["vos_drift"]  # vos_drift_full: DS p.5 dVos/dT, +/-10 nV/degC
VOS_KEYS = ("vos_vcm48_25c", "vos_vcm0_25c")  # both DS p.5 rows (no ADCRANGE condition), +/-1 uV max at 25 degC

VOS_BUS = ina.param("vos_bus_25c")  # DS p.5, Vos_bus, +/-2.5 mV max, typ +/-1 mV
VOS_BUS_DRIFT = ina.param("vos_bus_drift_full")  # DS p.5, dVos/dT VBUS, +/-20 uV/degC
PSRR_P = ina.param("vos_bus_psrr_25c")  # DS p.5, VBUS offset vs VS, typ +/-0.25 mV/V, NO min/max
GBERR = ina.param("gberr_25c")  # DS p.5, GBERR +/-0.05 %
GB_DRIFT = ina.param("gb_drift_25c").with_typ(0.0)  # DS p.5, GB_DRFT +/-20 ppm/degC, no typ
LSB_VBUS = ina.param("lsb_vbus_25c").typ  # DS p.5, 1 LSB step size, bus voltage, 195.3125 uV
assert PSRR_P.min is None and PSRR_P.max is None, "PSRR row now has bounds; revisit the typical-only treatment"
DVS = Param(min=-VS_NOM * VS_TOL, typ=0.0, max=VS_NOM * VS_TOL, unit="V")  # VS span about 3.3 V (design choice)
VS_RAIL = Param(min=VS_MIN, typ=VS_NOM, max=VS_MAX, unit="V")  # supply rail for the pin-referenced rows (VIO, VOH)

# %% [markdown]
# ## Helpers

# %%
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


def elem_status(margin):
    """Status of the element-temperature margin: ESTIMATE, and a PASS only under the assumed element limit."""
    lim = f"{T_ELEM_LIMIT:g} degC (assumption)"
    return f"ESTIMATE, PASS only if element limit = {lim}" if margin >= 0 else f"ESTIMATE, FAIL at element limit {lim}"


def est_status(margin):
    """Status of an ESTIMATE (curve-based or typical-based) result."""
    return "ESTIMATE, " + ("PASS" if margin >= 0 else "FAIL")


def err_pf(mag_pct):
    """PASS/FAIL of an error magnitude (% of reading) against ERR_LIMIT_PCT."""
    return "PASS" if mag_pct <= ERR_LIMIT_PCT + 1e-12 else "FAIL"


md = []  # body sections; the header and summary are added at the end

# %% [markdown]
# ## Shunt model (self-heating, worst-case resistance)
# The WSK2512 DS gives tolerance (DS p.1) and TCR (DS p.2) but no thermal resistance and no element temperature. The only
# thermal data is the DERATING curve (DS p.3). Self-heating is therefore an ESTIMATE: the element rise is
# x = theta_est x I^2 x R(TA + x), solved as a fixed point with R from `resistance_worst` at the element temperature.

# %%
def rworst(r0, tol, tcr, t_c):
    """Not a DS equation: shunt resistance, ohm, from WSK2512 calc.resistance_worst (25 degC reference)."""
    return shunt.calc.resistance_worst(r0, tol, tcr, t_c, T_REF)


def r_bound(r0, tol, tcr, t_c, side):
    """Not a DS equation: highest ('hi') or lowest ('lo') shunt resistance over the tolerance and TCR rows at t_c (ohm)."""
    w = wca(rworst, unit="ohm", r0=r0, tol=tol, tcr=tcr, t_c=t_c)
    return w.max if side == "hi" else w.min


def rise(i, r0, tol, tcr, ta, side="hi"):
    """Not a DS equation: ESTIMATE of the self-heating rise (degC) of the shunt at ambient ta, self-consistent:
    x = THETA x I^2 x R(ta + x), with R = r_bound(...) at the element temperature (side 'hi' = worst-case R, the
    largest tolerance and TCR; 'lo' = smallest). Fixed-point iteration from x = 0 until the update is below 1e-10 degC.
    Returns (x, residual): residual = |THETA x I^2 x R(ta + x) - x| at the returned x, degC (must be below 1e-3)."""
    x = 0.0
    for _ in range(100):
        x_new = THETA * i * i * r_bound(r0, tol, tcr, ta + x, side)
        if abs(x_new - x) < 1e-10:
            x = x_new
            break
        x = x_new
    else:
        raise RuntimeError("self-heating fixed point did not converge")
    resid = abs(THETA * i * i * r_bound(r0, tol, tcr, ta + x, side) - x)
    assert resid < 1e-3, f"self-heating residual {resid} degC"
    return x, resid


def t_band(i, r0, tol, tcr):
    """ESTIMATE: element temperature band (degC) at current i: min = TA min + lowest-R rise at TA min, max = TA max +
    worst-R rise at TA max. Both ends are self-consistent (rise()); each is an extreme of the rise over the tolerance and
    TCR rows, so the band brackets the element temperature. Linear in the wca sense, so corners are exact."""
    lo = TA.min + rise(i, r0, tol, tcr, TA.min, "lo")[0]
    hi = TA.max + rise(i, r0, tol, tcr, TA.max, "hi")[0]
    return Param(min=lo, max=hi, unit="degC")


def t_hot(i, r0, tol, tcr):
    """ESTIMATE: element temperature (degC) at TA max with the worst-case R (the point value used in tables)."""
    return TA.max + rise(i, r0, tol, tcr, TA.max, "hi")[0]


def power_worst(i, r0, tol, tcr, t_c):
    """Not a DS equation: dissipation P = I^2 x R_worst at ambient t_c (WSK2512 calc.dissipation, DS p.1 and p.3 rate
    power only, against ambient). i: A. r0: ohm. Returns: W."""
    return shunt.calc.dissipation(i, rworst(r0, tol, tcr, t_c))


def fs_error(i_true, r_nom, r_tol, r_tcr, t_c, gserr, gs_drift, vos25, vos_drift, cal_gain):
    """Not a DS equation: measured minus true current (A), ambient only (shunt and INA229 both at t_c).

    Derivation: R_act = resistance_worst(r_nom, r_tol, r_tcr, t_c) (shunt tolerance and TCR). The shunt carries i_true
    and develops V = i_true x R_act, so i_seen = i_true x R_act / r_nom is the current the r_nom calibration would
    report for that voltage. SHUNT_CAL is an integer, so the register scale is off by cal_gain = SHUNT_CAL / raw
    (1.0 when raw is an integer); i_seen is scaled by it. The INA229 reading is then the INA229 calc.current_error (DS
    gain and offset terms, SPEC recipe step 2) evaluated at i_seen.
    Error = (i_seen - i_true) + current_error(i_seen, r_nom, vos25, vos_drift, gserr, gs_drift, t_c).
    Multilinearity: R_act is linear in t_c and current_error is linear in t_c, so the error is QUADRATIC in t_c: wca
    corners alone are not exact, and the caller samples interior points (SAMPLES).
    i_true: A. r_nom: ohm. r_tol: fraction. r_tcr: 1/degC. t_c: degC. gserr: fraction. gs_drift: 1/degC. vos25: V.
    vos_drift: V/degC. cal_gain: unitless. Returns: A (signed; positive = reads high)."""
    r_act = shunt.calc.resistance_worst(r_nom, r_tol, r_tcr, t_c, T_REF)
    i_seen = i_true * r_act / r_nom * cal_gain
    return (i_seen - i_true) + ina.calc.current_error(i_seen, r_nom, vos25, vos_drift, gserr, gs_drift, t_c)


def fs_error_sh(i_true, r_nom, r_tol, r_tcr, t_sh, t_amb, gserr, gs_drift, vos25, vos_drift, cal_gain):
    """Not a DS equation: fs_error with self-heating (ESTIMATE). The shunt resistance is evaluated at the shunt element
    temperature t_sh, and the INA229 gain and offset drift at the ambient t_amb. Same derivation as fs_error otherwise.
    Multilinearity: t_sh enters R_act linearly and t_amb enters the INA229 gain and offset linearly, and no variable
    appears squared. The function is therefore MULTILINEAR in the two separate temperatures (t_sh, t_amb), so wca
    corners are exact (checked with interior samples in section 5).
    i_true: A. r_nom: ohm. r_tol: fraction. r_tcr: 1/degC. t_sh, t_amb: degC. Returns: A (signed; positive = reads high)."""
    r_act = shunt.calc.resistance_worst(r_nom, r_tol, r_tcr, t_sh, T_REF)
    i_seen = i_true * r_act / r_nom * cal_gain
    return (i_seen - i_true) + ina.calc.current_error(i_seen, r_nom, vos25, vos_drift, gserr, gs_drift, t_amb)


def fs_for(r_nom, code_, cal_gain, vos_key, samples, i=None):
    """wca of the ambient fs_error for a shunt of value r_nom and tolerance code, with one Vos row. Bare error (A)."""
    i = I_MAX if i is None else i
    return wca(fs_error, unit="A", samples=samples, i_true=i, r_nom=r_nom,
               r_tol=shunt.tolerance(code_).with_typ(0.0), r_tcr=shunt.tcr(r_nom).with_typ(0.0), t_c=TA,
               gserr=GSERR, gs_drift=GS_DRIFT, vos25=ina.param(vos_key), vos_drift=VOS_DRIFT, cal_gain=cal_gain)


def worst_fs(r_nom, code_, cal_gain, i=None):
    """Ambient worst-case bare current error (Param, A) over both Vos rows: the larger one is kept."""
    res = {k: fs_for(r_nom, code_, cal_gain, k, SAMPLES, i) for k in VOS_KEYS}
    return max(res.items(), key=lambda kv: max(abs(kv[1].min), abs(kv[1].max)))


def fs_sh_for(r_nom, tol_row, tcr_row, cal_gain, i, vos_key, samples=0):
    """ESTIMATE: wca of fs_error_sh at current i for a shunt of value r_nom. tol_row and tcr_row are bound-only Params
    (typ 0). The element temperature band is t_band(i, ...), so the shunt is evaluated over its own heating band and
    the INA229 over TA 0-70 degC. Bare error (A), no LSB."""
    tb = t_band(i, r_nom, tol_row, tcr_row)
    return wca(fs_error_sh, unit="A", samples=samples, i_true=i, r_nom=r_nom, r_tol=tol_row, r_tcr=tcr_row,
               t_sh=tb, t_amb=TA, gserr=GSERR, gs_drift=GS_DRIFT, vos25=ina.param(vos_key), vos_drift=VOS_DRIFT,
               cal_gain=cal_gain)


def fs_sh_worst(r_nom, tol_row, tcr_row, cal_gain, i):
    """ESTIMATE: self-heated worst-case bare error (Param, A) over both Vos rows; the larger one is kept."""
    res = {k: fs_sh_for(r_nom, tol_row, tcr_row, cal_gain, i, k) for k in VOS_KEYS}
    key = max(res, key=lambda k: max(abs(res[k].min), abs(res[k].max)))
    return key, res[key]


def fs_mag(p):
    """Worst-case magnitude (A) of a bare error Param plus the 1 LSB term."""
    return max(abs(p.min - LSB), abs(p.max + LSB))


def offer_info(code_, value):
    """Availability record of a (code, value) pair from the snapshot. n_dist counts distinct distributors that list the
    part, whatever the stock (a listing at zero stock still counts). stock is today's total, INFORMATIONAL only."""
    rec = STOCK_TABLE[(code_, round(value * 1e3, 6))]
    offers = rec["offers"]
    dists = sorted({d for d, _, _ in offers})
    regions = sorted({g for _, g, _ in offers})
    total = sum(s for _, _, s in offers) if offers else None
    return dict(match=rec["match"], n_dist=len(dists), dists=dists, regions=regions, stock=total)


def standard_value(code_, value):
    """True when the value is a standard value of the library's series for that tolerance code (library value table)."""
    return any(abs(value - v) <= 1e-9 * v for v in shunt.values(tol=code_))


def avail_rating(info, standard=True):
    """Long-term availability rating (proxy, see the report). GOOD: recognized (MATCH_EXACT), listed by at least 2
    distributors, standard value. FAIR: recognized, listed by 1 distributor, or a non-standard value. POOR/UNKNOWN:
    not recognized by the Diode BOM sources (MATCH_FAILED) or recognized with no distributor listing."""
    if info["match"] != "MATCH_EXACT" or info["n_dist"] == 0:
        return "POOR/UNKNOWN"
    if info["n_dist"] >= 2 and standard:
        return "GOOD"
    return "FAIR"


def stock_label(info):
    """Informational stock text (snapshot): not used for ranking."""
    if info["match"] != "MATCH_EXACT":
        return "not found in Diode BOM sources"
    if info["stock"] is None:
        return "no offer returned"
    return f"{info['stock']:,} units (snapshot)"


# Fitting values: every WSK2512 (value, code) whose self-heated worst-case Vsense at I_MAX fits ADCRANGE 0; the subset
# that also fits ADCRANGE 1 is marked. Full scan of the library values.
FIT = {0: {}, 1: {}}
for code_ in shunt.tolerances():
    tol_ = shunt.tolerance(code_)
    for v in shunt.values(tol=code_):
        tcr_ = shunt.tcr(v)
        x_, _ = rise(I_MAX, v, tol_, tcr_, TA.max, "hi")
        vs_ = I_MAX * r_bound(v, tol_, tcr_, TA.max + x_, "hi")
        for adc in (0, 1):
            if vs_ <= ADC_LIM[adc]:
                FIT[adc][(code_, round(v * 1e3, 6))] = vs_

# The stock snapshot (taken at 5 A) must cover the ADCRANGE 0 fit set at I_MAX, with its ADCRANGE 1 marks. Vsense rises with
# I, so the fit set at I_MAX is a subset of the 5 A set. A set that is not covered means
# the stock check must be run again (analysis/data snapshot).
assert set(FIT[0]) <= set(STOCK_TABLE), "fit set at I_MAX is not covered by the stock snapshot; re-run the stock check"
assert set(FIT[1]) <= {k for k, v in STOCK_TABLE.items() if 1 in v["adc_fit"]}, "ADCRANGE 1 fit set at I_MAX not covered; re-run the stock check"
CAND_N = len(FIT[0])
CAND_N_ADC1 = len(FIT[1])
ADC1_MAX_MOHM = max(k[1] for k in FIT[1])  # largest ADCRANGE 1 candidate, mohm (series values only)


def evaluate(adc, code_, value):
    """One candidate row at ADCRANGE adc. Self-heated worst case at I_MAX and I_LOW (ESTIMATE, the judged result), the
    ambient-only full-scale error (reference), Vsense with the self-heated R, headroom, dissipation, element temperature,
    SHUNT_CAL and its rounding, the number of ADC counts at I_MAX, and the stock status from the snapshot."""
    tol_p = shunt.tolerance(code_)  # Param (min, max)
    tcr_p = shunt.tcr(value)  # Param (min, max)
    tol_b = tol_p.with_typ(0.0)  # bound-only, nominal part at value (caller assumption)
    tcr_b = tcr_p.with_typ(0.0)  # bound-only, nominal TCR 0
    raw = ina.calc.shunt_cal(CURRENT_LSB, value, adcrange=adc)  # DS p.30 Equation 2, x4 for ADCRANGE 1
    cal = round(raw)
    gain = cal / raw
    x5, res5 = rise(I_MAX, value, tol_b, tcr_b, TA.max, "hi")
    t_el = TA.max + x5
    r_el = r_bound(value, tol_p, tcr_p, t_el, "hi")
    vs_sh = I_MAX * r_el
    margin = ADC_LIM[adc] - vs_sh
    _, fs5 = fs_sh_worst(value, tol_b, tcr_b, gain, I_MAX)
    _, fs1 = fs_sh_worst(value, tol_b, tcr_b, gain, I_LOW)
    _, amb5 = worst_fs(value, code_, gain, I_MAX)
    pw = wca(power_worst, unit="W", i=I_MAX, r0=value, tol=tol_p, tcr=tcr_p, t_c=TA)
    r_amb = r_bound(value, tol_p, tcr_p, TA.max, "hi")
    mwv = shunt.calc.max_working_voltage(RATED, r_amb)
    info = offer_info(code_, value)
    match = info["match"]
    row = dict(
        adc=adc, code=code_, value=value, mpn=shunt.mpn(value, tol=code_), match=match,
        avail=avail_rating(info, standard_value(code_, value)), n_dist=info["n_dist"], dists=info["dists"],
        regions=info["regions"], stock=info["stock"], stock_text=stock_label(info),
        standard=standard_value(code_, value),
        vs_sh=vs_sh, vs_margin=margin, headroom=margin / ADC_LIM[adc], vs_lsb_counts=vs_sh / LSB_VSH[adc],
        x5=x5, res5=res5, t_el=t_el, p_el=I_MAX ** 2 * r_el, pw_max=pw.max, pw_margin=RATED - pw.max,
        mwv=mwv, raw=raw, cal=cal, cal_gain=gain, cal_round_pct=(cal / raw - 1.0) * 100,
        fs5=fs5, fs5_mag=fs_mag(fs5), fs5_pct=fs_mag(fs5) / I_MAX * 100,
        fs1_pct=fs_mag(fs1) / I_LOW * 100, amb5_pct=fs_mag(amb5) / I_MAX * 100, tol_b=tol_b, tcr_b=tcr_b,
    )
    row["fs5_pass"] = row["fs5_pct"] <= ERR_LIMIT_PCT + 1e-12
    # Ranked = recognized by the BOM sources (MATCH_EXACT), passes the 1 % check, fits the ADC range, is inside the rating
    # and the (P x R)^1/2 limit. Today's stock is NOT a condition.
    row["ranked"] = (match == "MATCH_EXACT" and row["fs5_pass"] and margin >= 0 and row["pw_margin"] >= 0
                     and mwv >= vs_sh)
    return row


# Candidate rows: every fitting (code, value) at ADCRANGE 0, and at ADCRANGE 1 where it also fits.
ROWS = []
for (code_, mohm) in sorted(FIT[0], key=lambda k: (k[0], k[1])):
    for adc in (0, 1):
        if (code_, mohm) in FIT[adc]:
            ROWS.append(evaluate(adc, code_, mohm / 1e3))

DESIGN = [r for r in ROWS if r["adc"] == ADCRANGE and r["code"] == code and abs(r["value"] - R_NOM) < 1e-12]
assert len(DESIGN) == 1, "the design shunt is not in the candidate set"
DESIGN = DESIGN[0]

RECOGNIZED_ROWS = [r for r in ROWS if r["match"] == "MATCH_EXACT"]
AVAIL_ORDER = {"GOOD": 0, "FAIR": 1, "POOR/UNKNOWN": 2}
# Ranking order: must pass 1 % (self-heated) -> availability (GOOD before FAIR) -> lowest self-heated error ->
# headroom at or above HEADROOM_MIN -> lower dissipation.
RANKED = sorted([r for r in ROWS if r["ranked"]],
                key=lambda r: (AVAIL_ORDER[r["avail"]], round(r["fs5_pct"], 2), r["headroom"] < HEADROOM_MIN,
                               r["pw_max"]))
REC = RANKED[0] if RANKED else None
TOP3 = RANKED[:3]
NOT_RANKED_PASS = sorted([r for r in ROWS if r["fs5_pass"] and not r["ranked"]], key=lambda r: r["fs5_pct"])


def sk(r):
    """Informational stock text for a row (snapshot), never used for ranking."""
    return "no offer returned" if r["stock"] is None else f"{r['stock']:,}"

# %% [markdown]
# ## Design shunt (current design, from SHUNT_SELECTED)
# Every later section uses these names. The design shunt is 10 mohm code B at ADCRANGE 0 (designer choice 2026-11-15).

# %%
TOL = shunt.tolerance(code)  # Param: fraction, min and max of the code's tolerance row
TCR = shunt.tcr(R_NOM)  # Param: TCR row (1/degC)
TOL_W = TOL.with_typ(0.0)  # bound-only row: nominal part at R_NOM (caller assumption, as in lib examples)
TCR_W = TCR.with_typ(0.0)  # bound-only row: nominal TCR 0
MPN = DESIGN["mpn"]
CAL_GAIN = DESIGN["cal_gain"]  # SHUNT_CAL integer / exact value (1.0 when exact)
RSHUNT_STRICT = ina.calc.rshunt_max(vsense_max=ADC_LIM[ADCRANGE], i_max=I_MAX)  # DS p.35 Equation 8, strict upper bound
VS_SH = DESIGN["vs_sh"]  # Param-free float, V: self-heated worst-case Vsense at I_MAX
VS_MARGIN = ADC_LIM[ADCRANGE] - VS_SH
PW = wca(power_worst, unit="W", i=I_MAX, r0=R_NOM, tol=TOL, tcr=TCR, t_c=TA)  # ambient worst dissipation (rating basis)
PW_MARGIN = RATED - PW.max
R_WORST = r_bound(R_NOM, TOL, TCR, TA.max, "hi")  # largest shunt resistance at TA 70 degC (ambient)
MWV = shunt.calc.max_working_voltage(RATED, R_WORST)  # DS p.2, (P x R)^1/2 at the rated power

# %% [markdown]
# ## Chart styling
# Palette: the dataviz reference categorical palette (`references/palette.md`). Blue, orange and green carry the error
# charts. The power chart uses blue, green and red (slots 1, 6 and 8), which keep 3:1 contrast on white, so every line is
# also labelled directly at its end. Aqua, yellow and magenta (below 3:1 on the light surface) are not used.

# %%
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e4e0"
BLUE, ORANGE, GREEN, RED = "#2a78d6", "#eb6834", "#008300", "#e34948"  # categorical slots 1, 2, 6, 8 (light mode)
BLUE_RAMP = ["#a9cdf2", "#6fa6e8", "#2a78d6", "#1b4f92"]  # sequential, light -> dark (ordered ambient)


def style_axes(ax, xlabel, ylabel):
    ax.set_xlabel(xlabel, color=MUTED)
    ax.set_ylabel(ylabel, color=MUTED)
    ax.grid(True, color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=9)


def save(fig, name):
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    path = FIG_DIR / name
    fig.savefig(path, dpi=130, facecolor="white")
    plt.close(fig)
    return path


# %% [markdown]
# ## 1. Shunt selection and re-rank (ADCRANGE as a design variable)
#
# Sense limits: ADCRANGE 0 = +/-163.84 mV (`vdiff_adcr0_full`), ADCRANGE 1 = +/-40.96 mV (`vdiff_adcr1_full`). The
# candidate is judged on its SELF-HEATED worst case: the element temperature is TA max plus the self-consistent rise
# (section 3), and the shunt resistance is the worst tolerance and TCR at that temperature. The ranking uses the same
# numbers as section 5.

# %%
md += ["## 1. Shunt selection and re-rank", ""]
md += [
    f"Sense limits: `vdiff_adcr0_full` max = {mv(ADC_LIM[0])} mV (ADCRANGE 0, DS p.5, {cite(VDIFF)}); "
    f"`vdiff_adcr1_full` max = {mv(ADC_LIM[1])} mV (ADCRANGE 1, DS p.5). Vsense (worst) = I_MAX x R_element, with "
    f"R_element = `resistance_worst` at the self-heated element temperature (TA {TA.max:g} degC + rise, rise from "
    f"section 3, theta_est {THETA:g} degC/W), worst tolerance and TCR.",
    "",
    f"**Candidate set.** Every WSK2512 (value, code) in the library whose self-heated worst Vsense fits ADCRANGE 0: "
    f"{CAND_N} pairs (B, D and F codes). {CAND_N_ADC1} of them also fit ADCRANGE 1, i.e. R_element <= "
    f"{mv(ADC_LIM[1] / I_MAX)} mohm at {I_MAX:g} A, which is the series values up to {ADC1_MAX_MOHM:g} mohm. ADCRANGE 1 is "
    "therefore a design choice for the small shunts only. Each candidate is evaluated at the ADCRANGE it fits "
    "(ADCRANGE 0 always; ADCRANGE 1 where it fits).",
    "",
    f"**Availability sources.** `pcb bom -f json` re-run on {STOCK_DATE} for every candidate (scratch copy of the board, "
    "shunt `rshunt`/`rshunt_tol`/`vsense_max` set per candidate): `analysis/data/shunt_stock_2026-11-15_rerank.json`. "
    "Per candidate: whether the Diode BOM sources recognize the part (MATCH_EXACT or MATCH_FAILED), the distributors "
    "and regions listing it (offer count, kept even at zero stock), and today's total stock (informational only).",
    "",
    "**Availability rating (proxy, not a lifecycle check).** GOOD = recognized, listed by at least 2 distributors, "
    "standard series value. FAIR = recognized, listed by 1 distributor (or a non-standard value). POOR/UNKNOWN = not "
    "found in the Diode BOM sources (MATCH_FAILED), or recognized with no distributor listing. Lifecycle status is "
    "**not checked**: the WSK2512 library directory carries no lifecycle field and the snapshot has none.",
    "",
    "Stock today is a snapshot; long-term availability (lifecycle, multi-distributor listing, standard values) decides. "
    "Verify lifecycle/PCN with the manufacturer before release (open item).",
    "",
    f"**Judgement.** Ranked = recognized (MATCH_EXACT), self-heated {I_MAX:g} A error within {ERR_LIMIT_PCT:g} % of reading (the "
    "requirement), Vsense inside the ADC range, shunt rating at 70 degC (p70 x derate) met, and (P x R)^1/2 at or above "
    f"the worst Vsense. Order: must pass 1 %, then availability rating (GOOD before FAIR), then lowest self-heated {I_MAX:g} A error "
    f"(compared at 0.01 %), then headroom at or above {HEADROOM_MIN * 100:g} % of the range, then lower dissipation. "
    "The designer's selection (2026-11-15) uses accuracy first among the 1 % passers (section 1.4). Today's stock never "
    "decides the ranking. POOR/UNKNOWN rows are shown but sit below every rated row. BOM reuse: not "
    "applicable (one shunt).",
    "",
]

RANK_HDR = ["Rank", "ADCRANGE", "Code", "Value (mohm)", "MPN", "Availability", "Distributors (regions)",
            "Stock today (informational)", "Full-scale self-heated error (%)", "1 % check (self-heated)",
            "100 mA self-heated (%)", "Full-scale ambient, ref. (%)", "Headroom (%)", "Dissipation at full scale, 70 degC (W)",
            "Element T at full scale, 70 degC (degC, ESTIMATE)", "SHUNT_CAL raw -> reg", "Note"]


def rank_row(n, r):
    if r["match"] != "MATCH_EXACT":
        note = "not found in Diode BOM sources (may still be orderable by quote)"
    elif not r["fs5_pass"]:
        note = "fails 1 % (not ranked)"
    elif not r["ranked"]:
        note = "fails a rating or ADC check (not ranked)"
    else:
        note = ""
    dist = f"{r['n_dist']} ({', '.join(r['regions']) or 'none'})" if r["n_dist"] else "0"
    return [n, r["adc"], r["code"], f"{r['value'] * 1e3:g}", r["mpn"], r["avail"], dist, sk(r),
            f"{r['fs5_pct']:.3f}", "PASS" if r["fs5_pass"] else "FAIL", f"{r['fs1_pct']:.3f}",
            f"{r['amb5_pct']:.3f}", pct(r["headroom"]), f"{r['pw_max']:.3f}", f"{r['t_el']:.1f}",
            f"{r['raw']:.3f} -> {r['cal']}", note]


order = RANKED + sorted([r for r in RECOGNIZED_ROWS if not r["ranked"]], key=lambda r: r["fs5_pct"]) + \
    sorted([r for r in ROWS if r["match"] != "MATCH_EXACT"], key=lambda r: r["fs5_pct"])
rank_rows = []
for n, r in enumerate(order, start=1):
    label = str(RANKED.index(r) + 1) if r in RANKED else "-"
    rank_rows.append(rank_row(label, r))
md += ["**Table 1.1. All candidates (ADCRANGE 0 fit set, plus ADCRANGE 1 where it fits), ranked. Unranked rows are shown "
       "for reference.**", ""]
md += mdtable(RANK_HDR, rank_rows)
md += [
    "",
    "Ranked rows are recognized (MATCH_EXACT), pass the 1 % check, and sit in rank order (availability first, then "
    "error). POOR/UNKNOWN rows (no distributor listing) are ranked below every FAIR and GOOD row only if they pass; the "
    "'Stock today' column is informational and never orders a row.",
    "",
]

DETAIL_HDR = ["ADCRANGE", "Code", "Value (mohm)", "Vsense worst, self-heated (mV)", "Headroom (mV)",
              "ADC counts at full scale (x 1e3)", "Shunt-voltage LSB (nV)", "CURRENT_LSB (uA)",
              "SHUNT_CAL rounding (% of reading)", "MWV (V)", "Rated P at 70 degC (W)"]
md += ["**Table 1.2. Stocked candidates: sense, resolution and calibration.**", ""]
md += mdtable(DETAIL_HDR, [[
    r["adc"], r["code"], f"{r['value'] * 1e3:g}", mv(r["vs_sh"]), mv(r["vs_margin"]),
    f"{r['vs_lsb_counts'] / 1e3:.1f}", f"{LSB_VSH[r['adc']] * 1e9:.4g}", f"{CURRENT_LSB * 1e6:.6g}",
    f"{r['cal_round_pct']:+.4f}", f"{r['mwv']:.3g}", f"{RATED:.3g}",
] for r in order])
md += [
    "",
    "The current register LSB (CURRENT_LSB, DS p.30 Equation 3) is set by I_MAX and is the same for both ADCRANGE "
    "settings. ADCRANGE changes the shunt-voltage LSB (`lsb_vshunt_adcr0/1`, DS p.5), the ADC counts used at full scale, and "
    "SHUNT_CAL (x4 for ADCRANGE 1, DS p.30), so the SHUNT_CAL rounding term is ADCRANGE-dependent.",
    "",
]

md += ["### 1.3 Top three (recognized, passing the 1 % check, ranked by availability first)", ""]
if TOP3:
    for n, r in enumerate(TOP3, start=1):
        md += [
            f"{n}. **{r['value'] * 1e3:g} mohm, code {r['code']}, ADCRANGE {r['adc']}, `{r['mpn']}`**: {I_MAX:g} A self-heated "
            f"error {r['fs5_pct']:.3f} % (ambient reference {r['amb5_pct']:.3f} %), 100 mA self-heated "
            f"{r['fs1_pct']:.3f} %, headroom {pct(r['headroom'])} %, dissipation {r['pw_max']:.3f} W at 70 degC "
            f"(element {r['t_el']:.1f} degC, ESTIMATE), availability {r['avail']} ({r['n_dist']} distributor(s), "
            f"{', '.join(r['dists']) or 'none'}), stock today {sk(r)} (informational). Vsense (self-heated) "
            f"{mv(r['vs_sh'])} mV.",
        ]
else:
    md += ["No recognized candidate passes the 1 % check."]
md += [""]

# %% [markdown]
# ### 1.4 Selected design shunt and selection basis
# `SHUNT_SELECTED` is the designer's choice of 2026-11-15. This cell states its basis and the fallback, and checks the
# basis against the candidate rows. It does not change the design.

# %%
PASSING = [r for r in ROWS if r["fs5_pass"]]
BEST_PASS = min(PASSING, key=lambda r: r["fs5_pct"])
FALLBACK = next((r for r in ROWS if r["adc"] == ADCRANGE and r["code"] == "D"
                 and abs(r["value"] - R_NOM) < 1e-12), None)
VSENSE_MAX_MV = math.ceil(round(VS_SH * 1e4, 6)) / 10  # mV: self-heated worst Vsense rounded UP to 0.1 mV

md += ["### 1.4 Selected design shunt and selection basis", ""]
md += [
    f"**Designer choice, 2026-11-15:** `{DESIGN['mpn']}`, {DESIGN['value'] * 1e3:g} mohm, tolerance code {code} "
    f"(+/-{pct(TOL.max)} %), ADCRANGE {ADCRANGE} (`SHUNT_SELECTED`). Basis: best accuracy among the parts that pass "
    f"+/-{ERR_LIMIT_PCT:g} % of reading at {I_MAX:g} A (self-heated worst case); availability FAIR, carried as an open item.",
    "",
    (f"**Basis check.** Self-heated {I_MAX:g} A error {DESIGN['fs5_pct']:.3f} % ({err_pf(DESIGN['fs5_pct'])} against "
     f"{ERR_LIMIT_PCT:g} %). "
     + (f"It is the lowest of the {len(PASSING)} candidates that pass, so the accuracy basis holds."
        if BEST_PASS is DESIGN else
        f"It is NOT the lowest: `{BEST_PASS['mpn']}` ({BEST_PASS['value'] * 1e3:g} mohm, code {BEST_PASS['code']}) is "
        f"{BEST_PASS['fs5_pct']:.3f} % and passes; the designer must confirm the basis.")),
    "",
    f"**Availability (proxy, snapshot {STOCK_DATE}): {DESIGN['avail']}.** Listed by {DESIGN['n_dist']} distributor(s): "
    f"{', '.join(DESIGN['dists']) or 'none'}; stock today {sk(DESIGN)} (informational only; zero stock is accepted for this "
    "choice). Lifecycle, PCN and lead time are not checked. **OPEN ITEM:** confirm long-term availability and lead time "
    "with Vishay or the distributors before release.",
    "",
]
if FALLBACK is not None:
    md += [
        f"**Fallback (same value and footprint):** `{FALLBACK['mpn']}`, {FALLBACK['value'] * 1e3:g} mohm, tolerance code "
        f"{FALLBACK['code']} (+/-{pct(shunt.tolerance(FALLBACK['code']).max)} %): availability {FALLBACK['avail']} "
        f"({FALLBACK['n_dist']} distributor(s): {', '.join(FALLBACK['dists'])}; stock today {sk(FALLBACK)}, informational). "
        f"Self-heated {I_MAX:g} A error {FALLBACK['fs5_pct']:.3f} % ({err_pf(FALLBACK['fs5_pct'])} against {ERR_LIMIT_PCT:g} %). "
        f"Vsense (self-heated) {mv(FALLBACK['vs_sh'])} mV, headroom {pct(FALLBACK['headroom'])} %.",
        "",
    ]
if REC is not None and REC is not DESIGN:
    md += [
        f"Table 1.1 ranks availability first, so rank 1 is `{REC['mpn']}` ({REC['avail']}, self-heated "
        f"{REC['fs5_pct']:.3f} %) and the design is rank {RANKED.index(DESIGN) + 1 if DESIGN in RANKED else '-'}. The "
        "designer's basis is accuracy first; that trade-off is the designer's, recorded here.",
        "",
    ]
md += [
    f"**Board values (ina229.zen):** `rshunt=\"{R_NOM * 1e3:g}mohm\"`, `rshunt_tol=\"{code}\"`, "
    f"`vsense_max=\"{VSENSE_MAX_MV:.1f}mV\"`. vsense_max is the self-heated worst Vsense {mv(VS_SH)} mV rounded up to "
    f"0.1 mV; headroom to the {mv(ADC_LIM[ADCRANGE])} mV ADCRANGE {ADCRANGE} limit is {mv(VS_MARGIN)} mV "
    f"({pct(VS_MARGIN / ADC_LIM[ADCRANGE])} %). SHUNT_CAL raw {DESIGN['raw']:.6g}, register {DESIGN['cal']}. To change the "
    "design, change `SHUNT_SELECTED` first; the board values follow.",
    "",
]
md += [
    "Passing the 1 % check but not ranked (for information): "
    + ("; ".join(f"{r['value'] * 1e3:g} mohm {r['code']} ADCRANGE {r['adc']} ({r['fs5_pct']:.3f} %, "
                 f"{r['avail']}, {r['match']})" for r in NOT_RANKED_PASS[:4]) or "none") + ".",
    "",
]

# %% [markdown]
# ### 1.5 ADCRANGE, offset and gain rows: what the datasheet says per ADCRANGE
# The DS p.5 electrical table (rendered and read) has ADCRANGE in the test condition only for the VDIFF range row.
# The offset rows (VCM = 48 V and VCM = 0 V), dVos/dT, GSERR and GS_DRFT carry no ADCRANGE condition, so they are the
# same for ADCRANGE 1. No ADCRANGE-1 offset row is missing, so no open item and no larger-magnitude substitution is needed.

# %%
md += ["### 1.5 ADCRANGE-dependent datasheet rows", ""]
md += mdtable(["DS row (library key)", "ADCRANGE 0", "ADCRANGE 1", "Used in this analysis"], [
    ["Shunt voltage input range, VDIFF (`vdiff_adcr0_full` / `vdiff_adcr1_full`, DS p.5)",
     "+/-163.84 mV", "+/-40.96 mV", "Sense limit for the candidate fit (section 1)"],
    ["1 LSB, shunt voltage (`lsb_vshunt_adcr0_25c` / `lsb_vshunt_adcr1_25c`, DS p.5)",
     f"{LSB_VSH[0] * 1e9:.4g} nV", f"{LSB_VSH[1] * 1e9:.4g} nV", "Counts at full scale (Table 1.2); not in the error budget"],
    ["SHUNT_CAL (DS p.30 Equation 2 and its bullet: x4 for ADCRANGE 1)",
     "13107.2e6 x CURRENT_LSB x R", "x 4", "Integer register and its rounding error (`calc.shunt_cal`, `adcrange`)"],
    ["CURRENT_LSB (DS p.30 Equation 3)", f"{CURRENT_LSB * 1e6:.6g} uA", "same", "1 LSB term and the current resolution"],
    ["Offset Vos, VCM = 48 V / 0 V, dVos/dT (`vos_vcm48_25c`, `vos_vcm0_25c`, `vos_drift_full`, DS p.5)",
     "+/-1 uV", "same (no ADCRANGE condition)", "Both rows used; larger magnitude kept"],
    ["Gain error GSERR and GS_DRFT (`gserr_25c`, `gs_drift_25c`, DS p.5)", "+/-0.05 %, +/-20 ppm/degC",
     "same (no ADCRANGE condition)", "Gain term (section 5)"],
], )
md += [
    "",
    "The ADCRANGE-1 effect on the error comes from the smaller shunt it forces (Vos / R grows) and from the SHUNT_CAL "
    "rounding (x4 larger integer). It does not come from a different offset.",
    "",
]

# %% [markdown]
# ### 1.6 Why INA229 SHUNT_TEMPCO does not reduce the bound
# (Analysis, DS p.16, section 7.3.5, and DS p.23, Table 7-8.)

# %%
md += ["### 1.6 SHUNT_TEMPCO", ""]
md += [
    "The INA229 SHUNT_TEMPCO register (DS p.16, Equation 1; register DS p.23, Table 7-8, 1 ppm/degC per LSB) rescales "
    "the shunt resistance in the device by RADJ = RNOM x (1 + TEMPCO x (DIETEMP - 25)). It compensates a KNOWN shunt "
    "tempco, using the die temperature as the measure of the shunt temperature. The WSK2512 TCR is only bounded here "
    "(+/-35 ppm/degC, either sign; WSK2512 DS p.2 and `tcr_5m_200m`), so no single TEMPCO setting makes the error smaller "
    "for every unit: a setting that helps one sign of TCR hurts the other. The die also does not track the shunt element "
    "temperature: the shunt is at its element temperature (the element rise at full scale, section 3), while "
    "the die sits near ambient plus its own dissipation (section 9). Both the TCR sign and the die-to-element offset are "
    "unknown, so SHUNT_TEMPCO cannot reduce the worst-case bound in section 5. It can only remove a known, matched TCR "
    "after the part is measured. Analysis, not a datasheet bound; the element-to-die offset is an open item.",
    "",
]

# %% [markdown]
# ## Availability of the design shunt

# %%
md += ["**Design shunt availability (snapshot).** "
       f"{DESIGN['value'] * 1e3:g} mohm code {DESIGN['code']}: {DESIGN['avail']} ({DESIGN['n_dist']} distributor(s)), "
       f"stock today {sk(DESIGN)} (informational), `{DESIGN['mpn']}`. The snapshot is the sourcing record for section 1.",
       ""]

# %% [markdown]
# ## 2. Shunt power and rating (ambient, the DS rating basis)

# %%
def max_current_sh(rated, hi=20.0):
    """ESTIMATE: largest continuous current (A) with I^2 x R_element = rated at TA max (bisection). The element rise is
    rise() (theta_est, section 3). hi = 20 A is below the runaway current of the fixed point (about 53 A)."""
    lo = 0.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        t_el = TA.max + rise(mid, R_NOM, TOL_W, TCR_W, TA.max, "hi")[0]
        if mid * mid * r_bound(R_NOM, TOL, TCR, t_el, "hi") > rated:
            hi = mid
        else:
            lo = mid
    return lo


MAXI_AMB = shunt.calc.max_current(RATED, R_WORST)  # A, ambient-resistance rating basis (DS p.2 (P x R)^1/2)
MAXI_SH = max_current_sh(RATED)  # A, ESTIMATE: self-heated element resistance

md += ["## 2. Shunt power and rating", ""]
md += [
    f"Worst-case dissipation at I_MAX = {I_MAX:g} A and R_worst (TA {TA.max:g} degC, ambient resistance; the worst "
    f"corner): **{PW.max:.4g} W**. Rating: `p70` max = {P70:g} W at 70 degC ambient (DS p.1, {cite(shunt.param('p70'))}), "
    f"`rated_power` at TA = {TA.max:g} degC (derating knee `derate_knee` = {KNEE:g} degC and zero `derate_zero` = "
    f"{ZERO:g} degC are read off the DS p.3 graph, curve) x derate {DERATE:g} = {RATED:.4g} W.",
    "",
]
md += mdtable(["Check", "Worst-case (W)", "Rated (W)", "Margin (W)", "Margin (% of rating)", "Result", "Cite"], [[
    f"Shunt dissipation at {I_MAX:g} A, 70 degC (ambient rating)", f"{PW.max:.4g}", f"{RATED:.4g}", f"{PW_MARGIN:.4g}",
    pct(PW_MARGIN / RATED), status(PW_MARGIN), cite(shunt.param("p70")),
], [
    "Continuous current at 70 degC, rating basis (ambient R, `calc.max_current`), A", f"{MAXI_AMB:.4g}", f"{I_MAX:g}",
    f"{MAXI_AMB - I_MAX:+.3g}", pct((MAXI_AMB - I_MAX) / MAXI_AMB), status(MAXI_AMB - I_MAX), "DS p.1 P70, DS p.2 (P x R)^1/2",
], [
    "Continuous current at 70 degC, self-heated (element R, bisection), A", f"{MAXI_SH:.4g}", f"{I_MAX:g}",
    f"{MAXI_SH - I_MAX:+.3g}", pct((MAXI_SH - I_MAX) / MAXI_SH), est_status(MAXI_SH - I_MAX), "section 3.2 theta_est (ESTIMATE)",
]])
md += [
    "",
    f"Rated working voltage (`max_working_voltage`, DS p.2 (P x R)^1/2): {MWV:.4g} V at R_worst "
    f"{R_WORST * 1e3:.4g} mohm, vs worst Vsense {mv(VS_SH)} mV (self-heated): {status(MWV - VS_SH)}.",
    "",
    "The rating is against ambient. The element temperature is estimated in section 3, not rated by the DS.",
    "",
]

# %% [markdown]
# ## Shunt self-heating (section 3, ESTIMATE)
# The WSK2512 DS gives no thermal resistance and no element temperature. Its only thermal information is the DERATING
# curve (DS p.3, curve). The estimate below is read from that curve. Every use is labelled ESTIMATE.

# %%
def rated_at(ta):
    """Rated power (W) at ambient ta: DERATING curve (curve-based above the knee), x DERATE."""
    return shunt.calc.rated_power(ta_c=ta, **RATED_KW) * DERATE


def p_at(i, ta):
    """Worst-case dissipation (W) at current i and ambient ta, ambient resistance (rating basis)."""
    return wca(power_worst, unit="W", i=i, r0=R_NOM, tol=TOL, tcr=TCR, t_c=ta).max


def t_shunt(i, ta):
    """ESTIMATE: shunt element temperature (degC) at current i and ambient ta, self-consistent (worst-case R)."""
    return ta + rise(i, R_NOM, TOL_W, TCR_W, ta, "hi")[0]


def rise_at(i, ta):
    return rise(i, R_NOM, TOL_W, TCR_W, ta, "hi")[0]


T_HOT_5 = t_shunt(I_MAX, TA.max)  # degC, ESTIMATE
T_MARGIN_5 = T_ELEM_LIMIT - T_HOT_5
T_COLD_5 = t_shunt(I_MAX, TA.min)  # degC, ESTIMATE
T_RISE_5 = rise_at(I_MAX, TA.max)  # degC, self-consistent
T_RISE_LOW = rise_at(I_LOW, TA.max)  # degC
_, T_RESID_5 = rise(I_MAX, R_NOM, TOL_W, TCR_W, TA.max, "hi")  # degC, fixed-point residual at I_MAX

md += ["## 3. Shunt self-heating (ESTIMATE)", "", "### 3.1 Rated power vs ambient (derating curve)", ""]
md += [
    f"Rated power = `p70` ({P70:g} W) up to the knee {KNEE:g} degC, then a straight line to 0 W at {ZERO:g} degC "
    "(`calc.rated_power`, DS p.3 graph, curve). Above the knee the curve is an estimate, not a bound.",
    "",
    "![Shunt rated power vs ambient](design-analysis/shunt_rated_power.png)",
    "",
    f"Figure 1. Derating curve with the {I_MAX:g} A, 70 degC operating point (dissipation at worst R, from section 2).",
    "",
]

md += ["### 3.2 Hot-spot thermal resistance and the self-heating equation (estimate)", ""]
md += [
    f"**theta_est = (derate_zero - derate_knee) / p70 = ({ZERO:g} - {KNEE:g}) / {P70:g} = {THETA:g} degC/W (ESTIMATE).** "
    "The DS prints no thermal resistance; this is read off the derating curve (derivation in `theta_est`, section "
    "notes in the script). It is not a datasheet value; the real element-to-ambient resistance depends on the copper "
    "and the layout (open item).",
    "",
    "The self-heating rise is solved self-consistently: x = theta_est x I^2 x R(TA + x), where R is the worst-case "
    "`resistance_worst` (largest tolerance and TCR) at the element temperature TA + x. Fixed-point iteration from x = 0 "
    f"converges; at {I_MAX:g} A and TA {TA.max:g} degC the residual is {T_RESID_5:.2e} degC (required below 0.001 degC).",
    "",
]

md += ["### 3.3 Shunt temperature vs current (estimate)", ""]
t_rows = []
for i_pt in [0.1] + [float(k) for k in range(1, int(I_MAX) + 1)]:
    t70 = t_shunt(i_pt, TA.max)
    t0 = t_shunt(i_pt, TA.min)
    t_rows.append([
        f"{i_pt:g}", f"{p_at(i_pt, TA.min):.4g}", f"{p_at(i_pt, TA.max):.4g}",
        f"{t0 - TA.min:.4g}", f"{t70 - TA.max:.4g}", f"{t0:.4g}", f"{t70:.4g}", f"{T_ELEM_LIMIT - t70:.4g}",
    ])
md += mdtable(["Current (A)", "Worst P at TA 0 (W)", "Worst P at TA 70 (W)", "Rise at TA 0 (degC)",
               "Rise at TA 70 (degC)", "T_shunt at TA 0 (estimate, degC)", "T_shunt at TA 70 (estimate, degC)",
               f"Margin to {T_ELEM_LIMIT:g} degC at TA 70 (degC, assumed limit)"], t_rows)
md += [
    "",
    "Reproduce a row: T_shunt = TA + rise, rise = theta_est x I^2 x R_worst(T_shunt); the 'Worst P' columns are at "
    "ambient resistance (rating basis) and are below the element-resistance power that sets the rise.",
    "",
    f"Limits: the element limit used here is **{T_ELEM_LIMIT:g} degC, an ASSUMPTION** (the derating curve's zero point, "
    f"`derate_zero`, read as the element limit). It is NOT `t_oper`: the library records `t_oper` (max {T_OPER_MAX:g} degC, "
    f"DS p.2) as an AMBIENT operating range, so it is not cited as the element limit. At {I_MAX:g} A and TA 70 degC: "
    f"T_shunt = {T_HOT_5:.4g} degC (ESTIMATE), margin {T_MARGIN_5:.4g} degC: **{elem_status(T_MARGIN_5)}**. The "
    "estimate depends on theta_est; the copper, the layout and the true element limit are open items.",
    "",
    "![Shunt temperature vs current](design-analysis/shunt_temperature.png)",
    "",
    "Figure 2. Estimated shunt element temperature vs current at TA 0 and 70 degC (self-consistent, worst-case R), against "
    f"the assumed {T_ELEM_LIMIT:g} degC element limit.",
    "",
]

md += ["### 3.4 Dissipation vs current and the derated limit at each ambient", ""]
d_rows = []
for ta in (0.0, 25.0, 50.0, 70.0):
    p5 = p_at(I_MAX, ta)
    d_rows.append([f"{ta:g}", f"{rated_at(ta):.4g}", f"{p5:.4g}", f"{rated_at(ta) - p5:.4g}",
                   pct((rated_at(ta) - p5) / rated_at(ta)), status(rated_at(ta) - p5)])
md += mdtable(["TA (degC)", "Derated limit (W)", "Worst dissipation at full scale (W)", "Margin (W)", "Margin (%)", "Result"],
              d_rows)
md += [
    "",
    "The limit is flat at p70 x derate below the knee (70 degC), so the 70 degC row is the tightest. Dissipation is "
    "I^2 x R_worst (ambient resistance, rating against ambient, as the DS states).",
    "",
    "![Shunt dissipation vs current](design-analysis/shunt_dissipation.png)",
    "",
    "Figure 3. Worst-case dissipation vs current at TA 0, 25, 50 and 70 degC, with the derated limit at each ambient. The "
    "four dissipation curves overlap: the shunt TCR changes R by under 0.3 % across 0-70 degC, far below the scale of the "
    "chart.",
    "",
]

# %% [markdown]
# ### Charts: shunt rated power, dissipation and element temperature (section 3)

# %%
# Figure 1: rated power vs ambient, with the I_MAX / 70 degC operating point.
AMB_GRID = [ZERO * k / 170 for k in range(171)]
fig, ax = plt.subplots(figsize=(7.5, 4.4))
ax.plot(AMB_GRID, [rated_at(ta) for ta in AMB_GRID], color=BLUE, lw=1.4,
        label="derated rating (DS p.3 curve; above 70 degC a curve-based estimate)")
ax.axvline(KNEE, color=MUTED, lw=0.8, ls=":")
ax.axvline(ZERO, color=MUTED, lw=0.8, ls=":")
ax.text(KNEE + 2, P70 * 0.04, f"knee {KNEE:g} degC", color=MUTED, fontsize=8)
ax.text(ZERO - 2, P70 * 0.04, f"0 W at {ZERO:g} degC", color=MUTED, fontsize=8, ha="right")
ax.scatter([TA.max], [rated_at(TA.max)], color=INK, s=22, zorder=5,
           label=f"rating at 70 degC: {rated_at(TA.max):.3g} W")
ax.scatter([TA.max], [p_at(I_MAX, TA.max)], color=ORANGE, s=34, zorder=6,
           label=f"operating point: {I_MAX:g} A dissipation at 70 degC, {p_at(I_MAX, TA.max):.3g} W")
style_axes(ax, "Ambient temperature (degC)", "Rated power (W)")
ax.set_xlim(0, ZERO + 5)
ax.set_ylim(0, P70 * 1.2)
ax.set_title("WSK2512 rated power vs ambient (derate 1.0)", color=INK, fontsize=10.5)
ax.legend(frameon=False, loc="upper right", fontsize=8, labelcolor=INK)
fig.tight_layout()
save(fig, "shunt_rated_power.png")

# %%
# Figure 2: element temperature vs current at TA min and max (self-consistent), with the assumed element limit.
I_GRID = [I_MAX * k / 50 for k in range(51)]
fig, ax = plt.subplots(figsize=(7.5, 4.6))
ax.plot(I_GRID, [t_shunt(i, TA.min) for i in I_GRID], color=BLUE, lw=1.4,
        label=f"element temperature, TA {TA.min:g} degC (ESTIMATE)")
ax.plot(I_GRID, [t_shunt(i, TA.max) for i in I_GRID], color=BLUE_RAMP[3], lw=1.4,
        label=f"element temperature, TA {TA.max:g} degC (ESTIMATE)")
ax.axhline(T_ELEM_LIMIT, color=INK, lw=1.0, ls="--",
           label=f"assumed element limit {T_ELEM_LIMIT:g} degC (assumption, not t_oper)")
ax.scatter([I_MAX], [T_HOT_5], color=ORANGE, s=34, zorder=6)
ax.annotate(f"{I_MAX:g} A, 70 degC: {T_HOT_5:.1f} degC", xy=(I_MAX, T_HOT_5), xytext=(-10, 10),
            textcoords="offset points", ha="right", fontsize=8, color=INK)
style_axes(ax, "Load current (A)", "Shunt element temperature (degC)")
ax.set_xlim(0, I_MAX)
ax.set_ylim(0, T_ELEM_LIMIT * 1.08)
ax.set_title(f"Shunt temperature vs current (ESTIMATE: theta_est = {THETA:g} degC/W, self-consistent)", color=INK,
             fontsize=10.5)
ax.legend(frameon=False, loc="center left", bbox_to_anchor=(0.01, 0.62), fontsize=8, labelcolor=INK)
fig.tight_layout()
save(fig, "shunt_temperature.png")

# %%
# Figure 3: worst-case dissipation vs current at four ambients, each with its derated limit.
fig, ax = plt.subplots(figsize=(7.5, 4.6))
for j, ta in enumerate((0.0, 25.0, 50.0, 70.0)):
    col = BLUE_RAMP[j]
    ax.plot(I_GRID, [p_at(i, ta) for i in I_GRID], color=col, lw=1.4, label=f"dissipation, TA {ta:g} degC")
    ax.axhline(rated_at(ta), color=col, lw=1.0, ls="--")
ax.text(0.1, rated_at(TA.max) * 1.03,
        "derated limits at TA 0, 25, 50, 70 degC: all 1.00 W (dashed, flat below the 70 degC knee)",
        color=INK, fontsize=8)
ax.scatter([I_MAX], [p_at(I_MAX, TA.max)], color=ORANGE, s=34, zorder=6)
ax.annotate(f"{I_MAX:g} A, 70 degC: {p_at(I_MAX, TA.max):.3f} W, margin {rated_at(TA.max) - p_at(I_MAX, TA.max):.3f} W",
            xy=(I_MAX, p_at(I_MAX, TA.max)), xytext=(-10, 44), textcoords="offset points", ha="right", fontsize=8,
            color=INK)
style_axes(ax, "Load current (A)", "Power (W)")
ax.set_xlim(0, I_MAX)
ax.set_ylim(0, rated_at(TA.max) * 1.25)
ax.set_title("WSK2512 dissipation vs current with derated limits, ambient only", color=INK, fontsize=10.5)
ax.legend(frameon=False, loc="center left", bbox_to_anchor=(0.01, 0.55), fontsize=8, labelcolor=INK)
fig.tight_layout()
save(fig, "shunt_dissipation.png")

# %% [markdown]
# ## Firmware constants (section 4)
# `SHUNT_CAL` and `CURRENT_LSB` are the register values the firmware writes. They come from the calc equations, not from
# retyped numbers.

# %%
SHUNT_CAL_RAW = ina.calc.shunt_cal(CURRENT_LSB, R_NOM, adcrange=ADCRANGE)  # DS p.30 Equation 2
SHUNT_CAL = round(SHUNT_CAL_RAW)
assert abs(SHUNT_CAL / SHUNT_CAL_RAW - CAL_GAIN) < 1e-12, "design and candidate SHUNT_CAL disagree"
CAL_ERR = SHUNT_CAL / SHUNT_CAL_RAW - 1.0  # fraction, reading gain error from the integer register
CAL_EXACT = abs(SHUNT_CAL_RAW - SHUNT_CAL) < 1e-9
SHUNT_CAL_OTHER = ina.calc.shunt_cal(CURRENT_LSB, R_NOM, adcrange=1 - ADCRANGE)

md += ["## 4. Firmware constants", ""]
md += mdtable(["Constant", "Value", "Equation / cite"], [
    ["CURRENT_LSB", f"{CURRENT_LSB:.10g} A/LSB ({CURRENT_LSB * 1e6:.6g} uA)",
     f"I_MAX / 2^19, {cite(ina.param('lsb_vbus_25c'))[:0]}DS p.30 Equation 3 (`calc.current_lsb`); same for both ADCRANGE"],
    ["SHUNT_CAL (raw)", f"{SHUNT_CAL_RAW:.6f}", f"13107.2e6 x CURRENT_LSB x R_NOM, DS p.30 Equation 2 (`calc.shunt_cal`, "
                                                f"ADCRANGE {ADCRANGE})"],
    ["SHUNT_CAL (register)", f"**{SHUNT_CAL}**", "rounded integer written to the register"],
    [f"SHUNT_CAL if ADCRANGE {1 - ADCRANGE} were used (not selected)", f"{SHUNT_CAL_OTHER:.6f}",
     "x4 for ADCRANGE 1 (DS p.30 bullet)"],
])
md += [
    "",
    f"R_NOM used in SHUNT_CAL = {R_NOM * 1e3:g} mohm ({MPN}). "
    + ("SHUNT_CAL is an exact integer, so the rounding term is zero." if CAL_EXACT else
       f"SHUNT_CAL rounding error = {CAL_ERR * 100:+.4g} % of reading. It is a gain term in section 5 (`cal_gain`)."),
    "",
]

# %% [markdown]
# ## Full-scale current error at I_MAX (section 5)
# Two results: the ambient-only result (INA229 and shunt both at TA) for reference, and the self-heated ESTIMATE (shunt
# at its element temperature), which is the one judged against the 1 % requirement.

# %%
VOS_KEY_AMB, FS = worst_fs(R_NOM, code, CAL_GAIN, I_MAX), None
VOS_KEY_AMB = worst_fs(R_NOM, code, CAL_GAIN, I_MAX)[0]
FS = fs_for(R_NOM, code, CAL_GAIN, VOS_KEY_AMB, SAMPLES)
FS_CORNERS = fs_for(R_NOM, code, CAL_GAIN, VOS_KEY_AMB, 0)
FS_MIN = FS.min - LSB
FS_MAX = FS.max + LSB
FS_TYP = FS.typ
VOS_KEY, FS_SH = fs_sh_worst(R_NOM, TOL_W, TCR_W, CAL_GAIN, I_MAX)  # ESTIMATE, the judged band
FS_SH_MIN = FS_SH.min - LSB
FS_SH_MAX = FS_SH.max + LSB
FS_SH_CHK = fs_sh_for(R_NOM, TOL_W, TCR_W, CAL_GAIN, I_MAX, VOS_KEY, samples=2)
FS_SH_CORNERS = fs_sh_for(R_NOM, TOL_W, TCR_W, CAL_GAIN, I_MAX, VOS_KEY, samples=0)
FS_SH_LO01 = fs_sh_for(R_NOM, TOL_W, TCR_W, CAL_GAIN, I_LOW, VOS_KEY)
FS_SH_LO01_MIN = FS_SH_LO01.min - LSB
FS_SH_LO01_MAX = FS_SH_LO01.max + LSB
FS_AMB_LO01 = fs_for(R_NOM, code, CAL_GAIN, VOS_KEY_AMB, SAMPLES, I_LOW)
FS_AMB_LO01_MIN = FS_AMB_LO01.min - LSB
FS_AMB_LO01_MAX = FS_AMB_LO01.max + LSB
FS_SH_PCT = max(abs(FS_SH_MIN), abs(FS_SH_MAX)) / I_MAX * 100
FS_AMB_PCT = max(abs(FS_MIN), abs(FS_MAX)) / I_MAX * 100
FS_SH_LO01_PCT = max(abs(FS_SH_LO01_MIN), abs(FS_SH_LO01_MAX)) / I_LOW * 100
FS_AMB_LO01_PCT = max(abs(FS_AMB_LO01_MIN), abs(FS_AMB_LO01_MAX)) / I_LOW * 100
assert abs(fs_mag(FS_SH) - DESIGN["fs5_mag"]) < 1e-12, "section 5 and the candidate row disagree"

ZERO_KW = dict(r_nom=R_NOM, r_tol=0.0, r_tcr=0.0, t_c=25.0, gserr=0.0, gs_drift=0.0, vos25=0.0, vos_drift=0.0,
               cal_gain=1.0)
term_specs = [
    ("Shunt tolerance (code " + code + ")", dict(r_tol=TOL_W)),
    ("Shunt TCR (TA 0-70 degC)", dict(r_tcr=TCR_W, t_c=TA)),
    ("VOS + drift", dict(vos25=ina.param(VOS_KEY_AMB), vos_drift=VOS_DRIFT, t_c=TA)),
    ("Gain error + drift", dict(gserr=GSERR, gs_drift=GS_DRIFT, t_c=TA)),
    ("SHUNT_CAL integer rounding", dict(cal_gain=CAL_GAIN)),
]
term_rows = []
term_max_sum = 0.0
for name, kw in term_specs:
    w = wca(fs_error, unit="A", samples=0, i_true=I_MAX, **{**ZERO_KW, **kw})
    term_rows.append((name, w))
    term_max_sum += max(abs(w.min), abs(w.max))
LSB_P = Param(min=-LSB, typ=0.0, max=LSB, unit="A")

# The excess of the combined corner over the sum of the individual maxima: cross products of the shunt and gain terms.
CROSS_TOL_TCR = I_MAX * TOL_W.max * TCR_W.max * (TA.max - T_REF)  # tolerance x TCR, A
CROSS_GAIN = I_MAX * (TOL_W.max + TCR_W.max * (TA.max - T_REF)) * (GSERR.max + GS_DRIFT.max * (TA.max - T_REF))
EXCESS = FS.max - term_max_sum

md += [f"## 5. Full-scale current error at {I_MAX:g} A (TA 0 to 70 degC)", ""]
md += [
    f"Design shunt: {R_NOM * 1e3:g} mohm, code {code}, `{MPN}`, ADCRANGE {ADCRANGE}. Inputs (INA229 INPUTS map for "
    "`current_error`, plus the shunt):",
    "",
]
md += mdtable(["Term", "Min", "Max", "Source"], [
    ["Shunt tolerance", f"-{pct(TOL.max)} %", f"+{pct(TOL.max)} %", cite(TOL)],
    ["Shunt TCR", f"{TCR.min * 1e6:g} ppm/degC", f"{TCR.max * 1e6:g} ppm/degC", cite(TCR)],
    ["Vos (VCM choice)", f"{ina.param(VOS_KEY_AMB).min * 1e6:g} uV", f"{ina.param(VOS_KEY_AMB).max * 1e6:g} uV",
     cite(ina.param(VOS_KEY_AMB))],
    ["dVos/dT", f"{VOS_DRIFT.min * 1e9:g} nV/degC", f"{VOS_DRIFT.max * 1e9:g} nV/degC", cite(VOS_DRIFT)],
    ["Gain error GSERR", pct(GSERR.min) + " %", pct(GSERR.max) + " %", cite(GSERR)],
    ["Gain drift GS_DRFT", f"{GS_DRIFT.min * 1e6:g} ppm/degC", f"{GS_DRIFT.max * 1e6:g} ppm/degC", cite(GS_DRIFT)],
    ["SHUNT_CAL rounding", f"{CAL_ERR * 100:+.4g} %", f"{CAL_ERR * 100:+.4g} %",
     f"`SHUNT_CAL` {SHUNT_CAL} vs raw {SHUNT_CAL_RAW:.4f}, DS p.30 Eq. 2"],
    ["1 LSB", f"-{LSB * 1e6:.4g} uA", f"+{LSB * 1e6:.4g} uA", "`current_lsb(I_MAX)`, DS p.30 Eq. 3 (added separately)"],
])
md += [
    "",
    f"**Vos row choice.** Both rows give +/-1 uV max at 25 degC (`vos_vcm48_25c` and `vos_vcm0_25c`). "
    f"Worst-case full-scale error magnitude: VCM = 48 V row {fs_mag(fs_for(R_NOM, code, CAL_GAIN, 'vos_vcm48_25c', SAMPLES)):.6g} A, "
    f"VCM = 0 V row {fs_mag(fs_for(R_NOM, code, CAL_GAIN, 'vos_vcm0_25c', SAMPLES)):.6g} A. Chosen: `{VOS_KEY_AMB}` "
    "(the two rows give the same worst-case magnitude, so the INPUTS map entry is kept). Neither row is at the 12-40 V "
    "bus voltage; the offset is specified at VCM 48 V and 0 V only (open item). The offset rows carry no ADCRANGE "
    "condition (section 1.5).",
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
    f"Sum of individual contributor maxima = {term_max_sum:.6g} A; the combined ambient worst case without the LSB is "
    f"{FS.max:.6g} A, which is {EXCESS:.3g} A MORE than that sum. The excess is the cross products of the terms that each "
    f"multiply the current: tolerance x TCR = {CROSS_TOL_TCR:.3g} A and (tolerance + TCR) x gain = {CROSS_GAIN:.3g} A "
    f"(sum {CROSS_TOL_TCR + CROSS_GAIN:.3g} A; the remaining {EXCESS - CROSS_TOL_TCR - CROSS_GAIN:.2g} A is the "
    "higher-order and Vos terms). The combined corner, not the sum, is the number to use.",
    "",
    "**Ambient-only result (reference; all terms, worst-case corners, plus 1 LSB):**",
    "",
]
md += mdtable(["Quantity", "Min", "Typ", "Max"], [
    ["Error (A)", f"{FS_MIN:.4g}", f"{FS_TYP:.4g}", f"{FS_MAX:.4g}"],
    ["Error (% of full-scale reading)", pct(FS_MIN / I_MAX), pct(FS_TYP / I_MAX), pct(FS_MAX / I_MAX)],
])
md += [
    "",
    f"Typ = all terms at their DS typical value of zero (bound-only rows are centred; typ_pm rows are centred with the "
    f"DS typical spread kept separately: gserr typ spread {pct(GSERR.typ_spread)} %, Vos typ spread "
    f"{ina.param(VOS_KEY_AMB).typ_spread * 1e6:g} uV). It is the nominal part at 25 degC, not a statistical typical.",
    "",
    f"Sampling check (ambient): the function is quadratic in TA (the shunt TCR times the gain drift), so interior samples "
    f"are needed. Corners only (samples = 0) give min {FS_CORNERS.min:.6g} A, max {FS_CORNERS.max:.6g} A; with {SAMPLES} "
    f"interior samples min {FS.min:.6g} A, max {FS.max:.6g} A. Difference: {abs(FS.min - FS_CORNERS.min):.2e} A low side, "
    f"{abs(FS.max - FS_CORNERS.max):.2e} A high side.",
    "",
    f"**Self-heated ESTIMATE (the judged result).** The shunt element runs from TA 0 degC plus its lowest-R rise to TA 70 "
    f"degC plus its worst-R rise (self-consistent, section 3). At {I_MAX:g} A the element reaches {T_HOT_5:.4g} degC at TA 70 degC "
    f"(rise {T_RISE_5:.4g} degC), so the shunt TCR is evaluated up to {T_HOT_5:.4g} degC. The INA229 drift stays at the "
    "ambient 0 to 70 degC.",
    "",
]
md += mdtable([f"Case ({I_MAX:g} A)", "Min error (A)", "Max error (A)", "Min (% of reading)", "Max (% of reading)",
               "Max magnitude (% of reading)", "1 % check"], [
    ["Ambient only (TA 0-70 degC), reference", f"{FS_MIN:.4g}", f"{FS_MAX:.4g}", pct(FS_MIN / I_MAX),
     pct(FS_MAX / I_MAX), f"{FS_AMB_PCT:.4g}", "REFERENCE, not judged"],
    ["With self-heating (ESTIMATE), judged", f"{FS_SH_MIN:.4g}", f"{FS_SH_MAX:.4g}", pct(FS_SH_MIN / I_MAX),
     pct(FS_SH_MAX / I_MAX), f"{FS_SH_PCT:.4g}",
     f"{err_pf(FS_SH_PCT)} vs {ERR_LIMIT_PCT:g} % (margin {ERR_LIMIT_PCT - FS_SH_PCT:+.3f} %), ESTIMATE"],
])
md += [
    "",
    f"Self-heating check: corners only give min {FS_SH_CORNERS.min:.6g} A, max {FS_SH_CORNERS.max:.6g} A; with 2 interior "
    f"samples min {FS_SH_CHK.min:.6g} A, max {FS_SH_CHK.max:.6g} A. The function is MULTILINEAR in the shunt and the "
    "ambient temperature (separate inputs), so corners are exact, and the interior check agrees.",
    "",
    f"Result: the self-heated worst case at {I_MAX:g} A is **{FS_SH_PCT:.3f} %** of reading "
    f"(ambient reference {FS_AMB_PCT:.3f} %). Requirement {ERR_LIMIT_PCT:g} %: **{err_pf(FS_SH_PCT)}** for the current "
    "design. The shunt TCR and the self-heating rise are the terms that move from the ambient to the self-heated case.",
    "",
]

# %% [markdown]
# ## Error spread vs current (section 6)

# %%
CURRENTS = [I_PLOT_LO * (I_MAX / I_PLOT_LO) ** (k / (N_PLOT - 1)) for k in range(N_PLOT)]
lo_a, typ_a, hi_a = [], [], []
lo_s, hi_s = [], []
for i_pt in CURRENTS:
    w = fs_for(R_NOM, code, CAL_GAIN, VOS_KEY_AMB, SAMPLES, i_pt)
    lo_a.append(w.min - LSB)
    typ_a.append(w.typ)
    hi_a.append(w.max + LSB)
    ws = fs_sh_for(R_NOM, TOL_W, TCR_W, CAL_GAIN, i_pt, VOS_KEY)
    lo_s.append(ws.min - LSB)
    hi_s.append(ws.max + LSB)

lo_pct = [100 * e / i_pt for e, i_pt in zip(lo_a, CURRENTS)]
typ_pct = [100 * e / i_pt for e, i_pt in zip(typ_a, CURRENTS)]
hi_pct = [100 * e / i_pt for e, i_pt in zip(hi_a, CURRENTS)]
lo_s_pct = [100 * e / i_pt for e, i_pt in zip(lo_s, CURRENTS)]
hi_s_pct = [100 * e / i_pt for e, i_pt in zip(hi_s, CURRENTS)]

# %% [markdown]
# ### Chart: current error, ambient and self-heated

# %%
fig, (ax1, ax2) = plt.subplots(2, 1, sharex=True, figsize=(9.4, 7.6))
for ax, sc, unit_lbl in ((ax1, 1.0, "Error (% of reading)"), (ax2, 1e3, "Error (mA)")):
    ax.fill_between(CURRENTS, [v * sc for v in lo_pct] if sc == 1.0 else [v * sc for v in lo_a],
                    [v * sc for v in hi_pct] if sc == 1.0 else [v * sc for v in hi_a],
                    color=BLUE, alpha=0.12, linewidth=0)
    ax.fill_between(CURRENTS, [v * sc for v in lo_s_pct] if sc == 1.0 else [v * sc for v in lo_s],
                    [v * sc for v in hi_s_pct] if sc == 1.0 else [v * sc for v in hi_s],
                    color=ORANGE, alpha=0.12, linewidth=0)
    ax.plot(CURRENTS, lo_pct if sc == 1.0 else [v * sc for v in lo_a], color=BLUE, lw=1.3, ls="--")
    ax.plot(CURRENTS, hi_pct if sc == 1.0 else [v * sc for v in hi_a], color=BLUE, lw=1.3)
    ax.plot(CURRENTS, lo_s_pct if sc == 1.0 else [v * sc for v in lo_s], color=ORANGE, lw=1.3, ls="--")
    ax.plot(CURRENTS, hi_s_pct if sc == 1.0 else [v * sc for v in hi_s], color=ORANGE, lw=1.3)
    ax.plot(CURRENTS, typ_pct if sc == 1.0 else [v * sc for v in typ_a], color=INK, lw=1.0)
    ax.axhline(0, color=MUTED, lw=0.6)
    if sc == 1.0:
        ax.axhline(ERR_LIMIT_PCT, color=INK, lw=0.9, ls=(0, (1, 2)))
        ax.axhline(-ERR_LIMIT_PCT, color=INK, lw=0.9, ls=(0, (1, 2)))
    ax.set_xscale("log")
    style_axes(ax, "Load current (A), log scale", unit_lbl)
ax1.set_title(f"INA229 + {R_NOM * 1e3:g} mohm WSK2512: full-scale error vs load current, TA 0-70 degC", color=INK,
              fontsize=10.5)
handles = [
    Line2D([], [], color=BLUE, lw=1.3, label="ambient only: max (min dashed)"),
    Line2D([], [], color=ORANGE, lw=1.3, label="with self-heating, ESTIMATE: max (min dashed)"),
    Line2D([], [], color=INK, lw=1.0, label="typ (ambient, nominal)"),
    Line2D([], [], color=INK, lw=0.9, ls=(0, (1, 2)), label=f"+/-{ERR_LIMIT_PCT:g} % requirement"),
]
ax1.legend(handles=handles, frameon=False, loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=8.5,
           labelcolor=INK)
ax1.text(0.01, 0.99, f"at {I_MAX:g} A, max: {FS_AMB_PCT:.3f} % ambient, {FS_SH_PCT:.3f} % self-heated (est.)",
         transform=ax1.transAxes, fontsize=8, color=INK, va="top")
ax2.set_xlabel("Load current (A), log scale", color=MUTED)
fig.tight_layout()
save(fig, "current_error.png")

md += [f"## 6. Error spread vs current (100 mA to {I_MAX:g} A)", ""]
md += [
    f"{N_PLOT} log-spaced points. Each point is the worst-case function of section 5, with the 1 LSB term added to min and "
    "max. Blue: ambient only (TA 0-70 degC). Orange: with self-heating (ESTIMATE), where the shunt is at its element "
    "temperature band. Top: % of reading, with the +/-1 % requirement dotted. Bottom: mA.",
    "",
    "![INA229 current error](design-analysis/current_error.png)",
    "",
    "Figure 4. Full-scale current error vs load current, ambient only and with the self-heating estimate.",
    "",
    f"Ambient only: at 0.1 A min {lo_pct[0]:.3g} %, max {hi_pct[0]:.3g} %; at {I_MAX:g} A min {lo_pct[-1]:.3g} %, "
    f"max {hi_pct[-1]:.3g} %. With self-heating (ESTIMATE): at 0.1 A min {lo_s_pct[0]:.3g} %, max {hi_s_pct[0]:.3g} %; "
    f"at {I_MAX:g} A min {lo_s_pct[-1]:.3g} %, max {hi_s_pct[-1]:.3g} %. The self-heated band differs only at the high end "
    f"(the shunt rises {T_RISE_5:.3g} degC at {I_MAX:g} A and {T_RISE_LOW:.2g} degC at 0.1 A).",
    "",
    f"Worst case, self-heated (ESTIMATE): **{I_MAX:g} A: {FS_SH_PCT:.3f} % of reading (judged vs {ERR_LIMIT_PCT:g} %: "
    f"{err_pf(FS_SH_PCT)}); 100 mA: {FS_SH_LO01_PCT:.3f} % (REPORT ONLY, no limit set at 100 mA)** (ambient only: {I_MAX:g} A "
    f"{FS_AMB_PCT:.3f} %, 100 mA {FS_AMB_LO01_PCT:.3f} %).",
    "",
]

# %% [markdown]
# ## Bus-voltage error (section 7)

# %%
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
V_SWEEP = [VBUS_LO + k * (VBUS_HI - VBUS_LO) / 28 for k in range(29)]
bus_lo, bus_hi = [], []
for v in V_SWEEP:
    w = wca(vbus_error, unit="V", samples=0, v_true=v, vos_bus=VOS_BUS, vos_bus_drift=VOS_BUS_DRIFT,
            vos_bus_psrr=0.0, dvs=0.0, gberr=GBERR, gb_drift=GB_DRIFT, t_c=TA)
    bus_lo.append(w.min - LSB_VBUS)
    bus_hi.append(w.max + LSB_VBUS)
BUS_12 = bus_hi[0]  # V, worst max at 12 V incl LSB (for summary)
BUS_40 = bus_hi[-1]

# %%
fig, ax = plt.subplots(figsize=(7.5, 4.6))
ax.fill_between(V_SWEEP, [v * 1e3 for v in bus_lo], [v * 1e3 for v in bus_hi], color=BLUE, alpha=0.14, linewidth=0)
ax.plot(V_SWEEP, [v * 1e3 for v in bus_hi], color=BLUE, lw=1.3, label="max incl. 1 LSB")
ax.plot(V_SWEEP, [v * 1e3 for v in bus_lo], color=BLUE, lw=1.3, ls="--", label="min incl. 1 LSB")
ax.axhline(0, color=INK, lw=1.0, label="typ (nominal): 0")
ax.set_xlim(VBUS_LO, VBUS_HI)
style_axes(ax, "Bus voltage, VBUS (V)", "Bus-voltage error (mV)")
ax.set_title("INA229 bus-voltage error vs bus voltage, TA 0-70 degC", color=INK, fontsize=10.5)
ax.legend(frameon=False, loc="upper left", fontsize=8.5, labelcolor=INK)
ax.text(VBUS_HI, bus_hi[-1] * 1e3, f" {bus_hi[-1] * 1e3:.3g} mV ({pct(bus_hi[-1] / VBUS_HI)} %)", color=INK,
        fontsize=8, va="bottom", ha="right")
fig.tight_layout()
save(fig, "bus_error.png")

md += ["## 7. Bus-voltage worst-case error", ""]
md += [
    "Terms and cites: " + f"`vos_bus_25c` ({cite(VOS_BUS)}), `vos_bus_drift_full` ({cite(VOS_BUS_DRIFT)}), "
    f"`gberr_25c` ({cite(GBERR)}), `gb_drift_25c` ({cite(GB_DRIFT)}), `lsb_vbus_25c` ({cite(ina.param('lsb_vbus_25c'))}).",
    "",
    f"`vos_bus_psrr_25c` ({cite(PSRR_P)}) prints only a typ, +/-0.25 mV/V in the TYP column and no MIN or MAX. "
    f"It is not a bound and is excluded from min and max (typ-only row). Its typical size is shown in the last column with "
    f"dVS = +/-{mv(DVS.max)} mV (3.3 V +/-3 %). The PSRR row's test condition is VS = 2.7 to 5.5 V, so the dVS reference "
    f"is an assumption (open item).",
    "",
]
md += mdtable(["V_true (V)", "Min err (mV)", "Typ err (mV)", "Max err (mV)", "Min (% of V)", "Max (% of V)",
               "Min incl. 1 LSB (mV)", "Max incl. 1 LSB (mV)", "PSRR typical, not bounded (mV)"], bus_rows)
md += [
    "",
    "![Bus-voltage error vs bus voltage](design-analysis/bus_error.png)",
    "",
    "Figure 5. Bus-voltage error vs bus voltage 12 to 40 V (band: min to max incl. 1 LSB; the typ-only PSRR is not in the band).",
    "",
    f"1 LSB = {LSB_VBUS * 1e6:.4f} uV (`lsb_vbus_25c`), added to min and max. Typ = nominal part at 25 degC with all bound "
    "rows centred (their DS typical spread is not added).",
    "",
    "The bus offset (`vos_bus_25c`) is specified only at VBUS = 20 mV, and the bus gain error at the table header "
    "condition. Their use at 12-40 V assumes the errors are flat with bus voltage (open item).",
    "",
]

# %% [markdown]
# ## Power measurement error (section 8)
# The INA229 power result is V x I, so its error combines the current error and the bus-voltage error. The combination
# below is a joint wca over all corners of both error models. The temperature is shared, so it is enumerated (the
# products of drift and temperature make the function quadratic in T, not multilinear).

# %%
LSB_I_P = Param(min=-LSB, max=LSB, unit="A")  # +/- 1 LSB of current (conservative, as section 5)
LSB_V_P = Param(min=-LSB_VBUS, max=LSB_VBUS, unit="V")  # +/- 1 LSB of bus voltage (as section 7)
T_POWER = [TA.min + k * (TA.max - TA.min) / (T_SAMPLES_PWR + 1) for k in range(T_SAMPLES_PWR + 2)]  # min, interior, max


def power_error(i_true, v_true, r_tol, r_tcr, gserr, gs_drift, vos25, vos_drift, cal_gain, lsb_i,
                vos_bus, vos_bus_drift, gberr, gb_drift, lsb_v, t_c):
    """Not a DS equation: fractional error of the INA229 power reading P = V x I, from the current error and the bus-
    voltage error combined at one shared ambient t_c.

    Derivation: the current reading is i_true + e_I, with e_I = fs_error (shunt tolerance and TCR, gain, offset, SHUNT_CAL
    scale; SPEC recipe step 2) + lsb_i. The bus reading is v_true + e_V, with e_V = vbus_error (gain, offset, drift;
    PSRR term excluded, typical only) + lsb_v. The power reading is (i_true + e_I) x (v_true + e_V), so the fractional
    error is (1 + e_I / i_true)(1 + e_V / v_true) - 1. Both errors are quadratic in t_c (the TCR and drift products), so
    the caller takes the min and max over the T_POWER points as well as the corners.
    i_true: A. v_true: V. t_c: degC. Returns: fraction (signed; positive = reads high)."""
    e_i = fs_error(i_true, R_NOM, r_tol, r_tcr, t_c, gserr, gs_drift, vos25, vos_drift, cal_gain) + lsb_i
    e_v = vbus_error(v_true, vos_bus, vos_bus_drift, 0.0, 0.0, gberr, gb_drift, t_c) + lsb_v
    return (i_true + e_i) * (v_true + e_v) / (i_true * v_true) - 1.0


def power_band(i, v, t_points=None):
    """Worst-case (min, max) fractional power error at current i and bus voltage v over all corners and t_points."""
    t_points = T_POWER if t_points is None else t_points
    lo, hi = None, None
    for t in t_points:
        w = wca(power_error, unit="1", samples=0, i_true=i, v_true=v, r_tol=TOL_W, r_tcr=TCR_W, gserr=GSERR,
                gs_drift=GS_DRIFT, vos25=ina.param(VOS_KEY_AMB), vos_drift=VOS_DRIFT, cal_gain=CAL_GAIN, lsb_i=LSB_I_P,
                vos_bus=VOS_BUS, vos_bus_drift=VOS_BUS_DRIFT, gberr=GBERR, gb_drift=GB_DRIFT, lsb_v=LSB_V_P, t_c=t)
        lo = w.min if lo is None else min(lo, w.min)
        hi = w.max if hi is None else max(hi, w.max)
    return lo, hi


P_VOLTS = (12.0, 24.0, 40.0)
PW_CURRENTS = [I_PLOT_LO * (I_MAX / I_PLOT_LO) ** (k / (N_PLOT_PWR - 1)) for k in range(N_PLOT_PWR)]
PW_BAND = {v: [power_band(i_pt, v) for i_pt in PW_CURRENTS] for v in P_VOLTS}

# sampling check: finer ambient grid at the two summary points
PW_CHK = {}
T_FINE = [TA.min + k * (TA.max - TA.min) / 8 for k in range(9)]
for v in (12.0, 40.0):
    lo_c, hi_c = power_band(I_MAX, v)
    lo_f, hi_f = power_band(I_MAX, v, T_FINE)
    PW_CHK[v] = (abs(lo_f - lo_c), abs(hi_f - hi_c))

PTME = ina.param("ptme_full")  # DS p.5, power total measurement error, +/-0.5 % at full scale (device, ideal shunt)
PW_5 = {v: power_band(I_MAX, v) for v in P_VOLTS}

md += ["## 8. Power measurement error (V x I)", ""]
md += [
    "Worst case over all corners of the current error (section 5, ambient) and the bus-voltage error (section 7), combined "
    "as (1 + e_I / I)(1 + e_V / V) - 1 (`power_error`). The temperature is shared and enumerated at "
    f"{len(T_POWER)} points (min, interior and max); 1 LSB is added to each of current and bus voltage. Ambient only "
    "(the self-heated current error is in section 5 and is not combined here). No requirement is set for power error: "
    "REPORT ONLY.",
    "",
]
pw_rows = []
for i_pt in (I_LOW, I_MAX):
    row = [f"{i_pt:g}"]
    for v in P_VOLTS:
        lo_v, hi_v = power_band(i_pt, v)
        row += [pct(lo_v), pct(hi_v)]
    pw_rows.append(row)
md += mdtable(["Current (A)", "12 V min (%)", "12 V max (%)", "24 V min (%)", "24 V max (%)", "40 V min (%)",
               "40 V max (%)"], pw_rows)
md += [
    "",
    f"Sampling check (9 ambient points vs the {len(T_POWER)} used, at {I_MAX:g} A): difference at 12 V "
    f"{PW_CHK[12.0][0]:.2e} low, {PW_CHK[12.0][1]:.2e} high; at 40 V {PW_CHK[40.0][0]:.2e} low, "
    f"{PW_CHK[40.0][1]:.2e} high (fraction).",
    "",
    f"Cross-check: the INA229 device spec `ptme_full` ({cite(PTME)}) is {PTME.min * 100:g} to +{PTME.max * 100:g} % at full "
    "scale over -40 to 125 degC. It is a device figure with an ideal shunt and does not include the external shunt's "
    "tolerance and TCR, which this result does. The power error here is therefore larger, as expected.",
    "",
    "![Power error vs current](design-analysis/power_error.png)",
    "",
    "Figure 6. Worst-case power measurement error vs current at 12, 24 and 40 V (min and max, ambient only).",
    "",
]

# %%
fig, ax = plt.subplots(figsize=(9.4, 4.8))
pw_cols = {12.0: BLUE, 24.0: GREEN, 40.0: RED}
ends = []
for v in P_VOLTS:
    lo_p = [100 * b[0] for b in PW_BAND[v]]
    hi_p = [100 * b[1] for b in PW_BAND[v]]
    col = pw_cols[v]
    ax.fill_between(PW_CURRENTS, lo_p, hi_p, color=col, alpha=0.10, linewidth=0)
    ax.plot(PW_CURRENTS, hi_p, color=col, lw=1.3)
    ax.plot(PW_CURRENTS, lo_p, color=col, lw=1.3, ls="--")
    ends.append((hi_p[-1], f"{v:g} V max {hi_p[-1]:+.2f} %", col))
    ends.append((lo_p[-1], f"{v:g} V min {lo_p[-1]:+.2f} %", col))
ax.axhline(0, color=MUTED, lw=0.6)
ax.set_xscale("log")
ax.set_xlim(I_PLOT_LO, I_MAX * 1.9)
style_axes(ax, "Load current (A), log scale", "Power error (% of reading)")
# Direct end labels, nudged apart so that the three voltages stay legible (no legend).
ends.sort(key=lambda e: e[0])
GAP = 0.09
ys = []
for y, _, _ in ends:
    ys.append(y if not ys else max(y, ys[-1] + GAP))
for (y0, text, col), y in zip(ends, ys):
    ax.annotate(text, xy=(I_MAX, y0), xytext=(I_MAX * 1.04, y), textcoords="data", va="center", fontsize=8.5,
                color=col)
ax.set_title("INA229 power error vs load current, ambient only (shunt + INA229 + bus, worst case)", color=INK,
             fontsize=10.5)
fig.tight_layout()
save(fig, "power_error.png")

# %% [markdown]
# ## INA229 self-heating (section 9, ESTIMATE, typical-based)
# Dissipation in the INA229 from the 3.3 V supply (quiescent current x VS, plus the ALERT_N pull-up current sunk into the
# part when asserted), and from the VBUS input divider (V_BUS / zvbus) at the bus. Junction temperature uses rtheta_ja,
# which is typical only (DS VSSOP-10, JEDEC board): the result is a typical-based estimate, not a bound.

# %%
RTJA_P = ina.param("rtheta_ja")
RTJA = RTJA_P.typ  # degC/W, typical only (DS p.4, DGS 10 pins)
TJ_ABS_P = ina.param("tj_abs")
TJ_ABS = TJ_ABS_P.max  # degC, absolute maximum junction temperature (DS p.3)
IQ_FULL_P = ina.param("iq_full")
IQ_FULL = IQ_FULL_P.max  # A, active supply current over -40 to 125 degC (VSENSE = 0), max only
IQ_25 = ina.param("iq_25c")  # A, active supply current at 25 degC (typ 640 uA, max 750 uA)
IQSD = ina.param("iqsd_25c")  # A, shutdown current (typ 2.8 uA, max 5 uA)
VOL_P = ina.param("vol_25c")
VOL_MAX = VOL_P.max  # V, ALERT_N low-level output at IOL = 1 mA, max 0.4 V
ZV = ina.param("zvbus_25c")  # ohm, VBUS input impedance (min 800 kohm, typ 1 Mohm, max 1.2 Mohm), 25 degC only
IB = ina.param("ib_25c")  # A, IN+/IN- input bias (+/-2.5 nA max), 25 degC only

# ALERT_N pull-up: 10 kohm from V3V3 (DESIGN CHOICE R_PULLUP), tolerance and TCR from the ERJ-3EKF library row. The
# library's tolerance row is measured at 20 degC (ERJ note), so the tolerance term uses t_ref_c = 20; the TCR term is
# referenced to 25 degC (the TCR test start, same note). With tcr = 0 the library function is R0 (1 + tol), so the two
# references are applied separately.
ERJ_TREF_TOL = 20.0  # degC, tolerance row reference (library note)
ERJ_TREF_TCR = 25.0  # degC, TCR test start (library note)


def erj_r_worst(r0, tol, tcr, t_c):
    """Not a DS equation: ERJ-3EKF worst-case resistance: tolerance referenced to 20 degC, TCR referenced to 25 degC."""
    return erj.calc.resistance_worst(r0, tol, 0.0, t_c, ERJ_TREF_TOL) * (1.0 + tcr * (t_c - ERJ_TREF_TCR))


R_PU_MPN = erj.mpn(R_PULLUP)
R_PU_LIB = wca(erj_r_worst, unit="ohm", r0=R_PULLUP, tol=erj.param("tol"), tcr=erj.param("tcr_10r_1m"), t_c=TA)
I_ALERT_MAX = VS_MAX / R_PU_LIB.min  # A, asserted, worst: highest supply, lowest resistance
I_ALERT_TYP = VS_NOM / R_PULLUP  # A, asserted, nominal
P_ALERT_RAIL = VS_MAX * I_ALERT_MAX  # W, drawn from V3V3 when asserted (worst)
P_ALERT_PART = VOL_MAX * I_ALERT_MAX  # W, dissipated in the INA229 when asserted (sink at VOL max, conservative)
P_PU_RES = VS_MAX * I_ALERT_MAX - P_ALERT_PART  # W, in the pull-up resistor (not the part), ERJ-3EKF rating below
P_PU_RATED = erj.param("p70").max

P_V3_PART = VS_MAX * IQ_FULL + P_ALERT_PART  # W, INA229 dissipation from V3V3 (worst)
P_BUS_PART_40 = VBUS_HI ** 2 / ZV.min + VBUS_HI * IB.max  # W, VBUS divider at 40 V (min impedance) + IN bias
P_BUS_PART_12 = VBUS_LO ** 2 / ZV.min + VBUS_LO * IB.max
TJ_V3 = TA.max + P_V3_PART * RTJA  # degC, ESTIMATE, V3V3 only (the requested path)
TJ_ALL = TA.max + (P_V3_PART + P_BUS_PART_40) * RTJA  # degC, ESTIMATE, plus the VBUS divider at 40 V
TJ_MARGIN = TJ_ABS - TJ_ALL

md += ["## 9. INA229 junction temperature (ESTIMATE, typical-based)", ""]
md += mdtable(["Term", "Value", "Basis (library cite)"], [
    ["Supply current, active worst (`iq_full`)", f"{IQ_FULL * 1e3:.3g} mA", cite(IQ_FULL_P)],
    ["Power from V3V3, active worst", f"{VS_MAX * IQ_FULL * 1e3:.4g} mW", f"`iq_full` x VS max ({VS_MAX:.3f} V)"],
    ["ALERT_N asserted: sink into the part (worst)", f"{P_ALERT_PART * 1e3:.4g} mW",
     f"VOL max {VOL_MAX:g} V x I_alert max {I_ALERT_MAX * 1e6:.4g} uA (section 10); {cite(VOL_P)}"],
    ["Dissipation from V3V3 (INA229)", f"**{P_V3_PART * 1e3:.4g} mW**", "iq + ALERT sink"],
    ["VBUS divider at 40 V (V^2 / zvbus min)", f"{P_BUS_PART_40 * 1e3:.4g} mW (incl. IN bias)", cite(ZV)],
    ["rtheta_ja (typical)", f"{RTJA:g} degC/W", cite(RTJA_P)],
    [f"Junction temperature at TA {TA.max:g} degC, V3V3 only (ESTIMATE)", f"**{TJ_V3:.4g} degC**",
     "TA + P_V3 x rtheta_ja"],
    [f"Junction temperature at TA {TA.max:g} degC, all INA229 power (ESTIMATE)", f"**{TJ_ALL:.4g} degC**",
     "adds the VBUS divider at 40 V"],
    ["Absolute maximum junction temperature", f"{TJ_ABS:g} degC", cite(TJ_ABS_P)],
    ["Margin to tj_abs (all power)", f"{TJ_MARGIN:.4g} degC", est_status(TJ_MARGIN)],
])
md += [
    "",
    f"`rtheta_ja` is a typical value (no max printed), so this is a typical-based estimate, not a worst-case bound. The "
    "junction temperature also depends on the board copper; the DS value assumes the JEDEC board.",
    "",
]

# %% [markdown]
# ## Quiescent current and power budget (section 10)
# Supply-side budget at 3.3 V and bus-side budget at 12 and 40 V. Table values come from the library rows; the bus-side
# shunt insertion loss (I^2 R) is outside the INA229 and is shown separately.

# %%
def mw(x):
    return f"{x * 1e3:.4g}"


def ua(x):
    return f"{x * 1e6:.4g}"


def ma(x):
    return f"{x * 1e3:.4g}"


ZV_12 = (VBUS_LO / ZV.max, VBUS_LO / ZV.typ, VBUS_LO / ZV.min)  # A, min, typ, max current at 12 V
ZV_40 = (VBUS_HI / ZV.max, VBUS_HI / ZV.typ, VBUS_HI / ZV.min)  # A, at 40 V
P_ZV_12 = (VBUS_LO * ZV_12[0], VBUS_LO * ZV_12[1], VBUS_LO * ZV_12[2])
P_ZV_40 = (VBUS_HI * ZV_40[0], VBUS_HI * ZV_40[1], VBUS_HI * ZV_40[2])
P_SHUNT_5 = (wca(power_worst, unit="W", i=I_MAX, r0=R_NOM, tol=TOL, tcr=TCR, t_c=TA).min,
             I_MAX ** 2 * R_NOM,  # typ: nominal R at 25 degC
             PW.max)
P_IQ_25_TYP = VS_NOM * IQ_25.typ
P_IQ_25_MAX = VS_MAX * IQ_25.max
P_IQ_FULL = VS_MAX * IQ_FULL
P_IQSD = VS_MAX * IQSD.max
TOT_V3_ACTIVE_TYP = P_IQ_25_TYP  # 25 degC typical, ALERT not asserted
TOT_V3_WORST = P_IQ_FULL + P_ALERT_RAIL  # active worst, ALERT asserted
TOT_V3_SD_WORST = P_IQSD + P_ALERT_RAIL  # shutdown worst, ALERT asserted (conservative assumption)
TOT_BUS_12 = P_ZV_12[2] + VBUS_LO * IB.max  # bus pin (in part), worst
TOT_BUS_40 = P_ZV_40[2] + VBUS_HI * IB.max
BUS_SHUNT_5 = P_SHUNT_5[2]  # shunt I^2 R at I_MAX, outside the part

md += ["## 10. Quiescent current and power budget", ""]
md += mdtable(["Item", "Supply", "Min", "Typ", "Max", "Basis (library cite)"], [
    ["INA229 active current, 25 degC (`iq_25c`)", "V3V3", "-", f"{ma(IQ_25.typ)} mA", f"{ma(IQ_25.max)} mA",
     cite(IQ_25)],
    ["INA229 active current, -40 to 125 degC (`iq_full`)", "V3V3", "-", "-", f"{ma(IQ_FULL)} mA", cite(IQ_FULL_P)],
    ["INA229 shutdown current (`iqsd_25c`)", "V3V3", "-", f"{ua(IQSD.typ)} uA", f"{ua(IQSD.max)} uA", cite(IQSD)],
    ["ALERT_N pull-up current, asserted (VS / R)", "V3V3", "0 (not asserted)", f"{ua(I_ALERT_TYP)} uA",
     f"{ua(I_ALERT_MAX)} uA", f"10 kohm {R_PU_MPN}: R min {R_PU_LIB.min / 1e3:.4g} kohm over tol, TCR, 0-70 degC "
     "(ERJ-3EKF tol at 20 degC, TCR at 25 degC)"],
    ["Power from V3V3: active, 25 degC", "V3V3", "-", f"{mw(P_IQ_25_TYP)} mW", f"{mw(P_IQ_25_MAX)} mW",
     f"`iq_25c` x VS (nom / max {VS_MAX:.3f} V)"],
    ["Power from V3V3: active, worst", "V3V3", "-", "-", f"{mw(P_IQ_FULL)} mW", "`iq_full` x VS max"],
    ["Power from V3V3: ALERT_N asserted (rail)", "V3V3", "0", f"{mw(VS_NOM * I_ALERT_TYP)} mW", f"{mw(P_ALERT_RAIL)} mW",
     "VS x I_alert"],
    ["VBUS input current at 12 V (`zvbus_25c`)", "Bus", f"{ua(ZV_12[0])} uA", f"{ua(ZV_12[1])} uA", f"{ua(ZV_12[2])} uA",
     cite(ZV)],
    ["VBUS input current at 40 V (`zvbus_25c`)", "Bus", f"{ua(ZV_40[0])} uA", f"{ua(ZV_40[1])} uA", f"{ua(ZV_40[2])} uA",
     "V / zvbus"],
    ["Power in INA229 from VBUS at 12 V", "Bus", mw(P_ZV_12[0]) + " mW", mw(P_ZV_12[1]) + " mW", mw(P_ZV_12[2]) + " mW",
     "V x I"],
    ["Power in INA229 from VBUS at 40 V", "Bus", mw(P_ZV_40[0]) + " mW", mw(P_ZV_40[1]) + " mW", mw(P_ZV_40[2]) + " mW",
     "V x I"],
    ["IN+ / IN- input bias (`ib_25c`)", "Bus", f"{IB.min * 1e9:.3g} nA", "0", f"{IB.max * 1e9:.3g} nA",
     cite(IB) + "; current, negligible power (< 1 uW)"],
    [f"Shunt insertion loss I^2 R at {I_MAX:g} A (outside the part)", "Bus", mw(P_SHUNT_5[0]) + " mW",
     mw(P_SHUNT_5[1]) + " mW", mw(P_SHUNT_5[2]) + " mW", "`power_worst` at TA 0 and 70 degC (ambient R); typ = nominal 25 degC"],
    ["ALERT pull-up resistor power, asserted (ERJ-3EKF)", "V3V3", "-", "-", f"{mw(P_PU_RES)} mW",
     f"rating p70 {mw(P_PU_RATED)} mW ({cite(erj.param('p70'))}): {status(P_PU_RATED - P_PU_RES)}"],
])
md += [
    "",
    "**Totals per supply.**",
    "",
]
md += mdtable(["Supply", "Case", "Total (mW)"], [
    ["V3V3", "Active, 25 degC, ALERT_N not asserted (typical)", mw(TOT_V3_ACTIVE_TYP)],
    ["V3V3", "Active, worst (`iq_full`, VS max), ALERT_N asserted", mw(TOT_V3_WORST)],
    ["V3V3", "Shutdown, worst (`iqsd_25c`), ALERT_N asserted (conservative)", mw(TOT_V3_SD_WORST)],
    ["Bus 12 V", "INA229 pin power, worst (VBUS divider + IN bias)", mw(TOT_BUS_12)],
    ["Bus 40 V", "INA229 pin power, worst (VBUS divider + IN bias)", mw(TOT_BUS_40)],
    ["Bus 12 V", f"Shunt I^2 R at {I_MAX:g} A, worst (outside the INA229)", mw(BUS_SHUNT_5)],
    ["Bus 40 V", f"Shunt I^2 R at {I_MAX:g} A, worst (outside the INA229)", mw(BUS_SHUNT_5)],
])
md += [
    "",
    "Assumptions: `iq_full` and `iq_25c` are printed without a VS condition; the worst case assumes VS = 3.399 V. "
    "`zvbus_25c` is a 25 degC row; the 800 kohm minimum is applied at all temperatures (open item). The ALERT_N pull-up "
    "is assumed asserted in shutdown (conservative).",
    "",
    "![Quiescent power budget](design-analysis/quiescent_budget.png)",
    "",
    "Figure 7. Worst-case power budget. Left: V3V3 rail (INA229 active plus ALERT_N pull-up asserted). Right: bus-pin power "
    f"inside the INA229 at 12 and 40 V. The shunt I^2 R at {I_MAX:g} A ({BUS_SHUNT_5 * 1e3:.0f} mW, outside the part) is annotated, "
    "not drawn to scale.",
    "",
]

# %%
fig, (axl, axr) = plt.subplots(1, 2, figsize=(9.6, 5.4), gridspec_kw=dict(width_ratios=[1, 1.25]))
v3_parts = [("INA229 active, worst (iq_full x VS max)", P_IQ_FULL, BLUE),
            ("ALERT_N pull-up, asserted", P_ALERT_RAIL, ORANGE)]
bottom = 0.0
for lbl, val, col in v3_parts:
    axl.bar([0], [val * 1e3], bottom=[bottom * 1e3], color=col, width=0.5, label=lbl)
    axl.text(0.27, (bottom + val / 2) * 1e3, f"{val * 1e3:.3g} mW", va="center", fontsize=8, color=INK)
    bottom += val
axl.text(0, bottom * 1e3 + 0.15, f"total {bottom * 1e3:.3g} mW", ha="center", fontsize=8.5, color=INK)
axl.set_xticks([0])
axl.set_xticklabels(["V3V3 rail\n(worst)"], color=MUTED, fontsize=9)
axl.set_xlim(-0.5, 0.75)
axl.set_ylim(0, bottom * 1e3 * 1.18)
style_axes(axl, "", "Power (mW)")
axl.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.14), fontsize=7.5, labelcolor=INK)

bus_lbls = ["12 V", "40 V"]
vbus_div = [P_ZV_12[2], P_ZV_40[2]]
ib_pw = [VBUS_LO * IB.max, VBUS_HI * IB.max]
xs = [0, 1]
axr.bar(xs, [v * 1e3 for v in vbus_div], color=BLUE, width=0.5, label="VBUS divider (V^2 / zvbus min), worst")
axr.bar(xs, [v * 1e3 for v in ib_pw], bottom=[v * 1e3 for v in vbus_div], color=ORANGE, width=0.5,
        label="IN+ / IN- bias x VCM (negligible)")
for x, v in zip(xs, vbus_div):
    axr.text(x + 0.27, v * 1e3, f"{v * 1e3:.3g} mW", va="center", fontsize=8, color=INK)
axr.set_xticks(xs)
axr.set_xticklabels([f"bus {b}" for b in bus_lbls], color=MUTED, fontsize=9)
axr.set_xlim(-0.5, 1.9)
axr.set_ylim(0, max(vbus_div) * 1e3 * 1.25)
style_axes(axr, "", "Power (mW)")
axr.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.14), fontsize=7.5, labelcolor=INK)
axr.set_title(f"shunt I^2 R at {I_MAX:g} A = {BUS_SHUNT_5 * 1e3:.0f} mW (outside the INA229) not drawn", color=MUTED,
              fontsize=8)
fig.suptitle("INA229 worst-case power budget: V3V3 rail (left) and bus pin (right)", color=INK, fontsize=10.5)
fig.tight_layout()
save(fig, "quiescent_budget.png")

# %% [markdown]
# ## Ratings (section 11)
# Absolute maximum and recommended operating limits from the library rows. Applied values are the worst case of the
# design. A row marked "rating only" is not applied by this design, and its owner is assembly or the board process.

# %%
def rating_row(name, lo, hi, rating, key, s, u, cite_text=None):
    """Margin = min(lo - rating.min, rating.max - hi); a missing rating bound is unlimited. Returns one table row."""
    r_lo = -float("inf") if rating.min is None else rating.min
    r_hi = float("inf") if rating.max is None else rating.max
    margin = min(lo - r_lo, r_hi - hi)
    shown_r = (f"{'-inf' if r_lo == -float('inf') else f'{r_lo * s:.4g}'} to "
               f"{'inf' if r_hi == float('inf') else f'{r_hi * s:.4g}'} {u}")
    return [name, f"{lo * s:.4g} to {hi * s:.4g} {u}", f"`{key}`: {shown_r}", f"{margin * s:.4g} {u}", status(margin),
            cite_text or cite(rating)]


VIO_ABS = ina.resolve("vio_abs", {"GND": 0.0, "VS": VS_RAIL})  # absolute bounds: GND - 0.3 V .. VS + 0.3 V at VS min
VOH_ROW = ina.param("voh_25c")
LOGIC_HI = VS_MAX  # V, the high level on MOSI/MISO/SCLK/ALERT_N (the 3.3 V +/-3 % rail at its maximum)
TJ_ROW_TXT = (f"{TJ_ALL:.4g} degC (ESTIMATE)", f"`tj_abs` {TJ_ABS:g} degC max", f"{TJ_MARGIN:.4g} degC",
              est_status(TJ_MARGIN))
DIFF_ROW = rating_row("Differential, hard load short (IN+ = 40 V, IN- = 0 V)", VBUS_HI, VBUS_HI,
                      ina.param("vin_diff_abs"), "vin_diff_abs", 1, "V")
VDIFF_ROW = rating_row("Shunt sense voltage (worst, self-heated)", -VS_SH, VS_SH, VDIFF, "vdiff_adcr0_full", 1e3, "mV")
ratings = [
    rating_row("Bus voltage VBUS (applied 12 to 40 V)", VBUS_LO, VBUS_HI, ina.param("vbus_abs"), "vbus_abs", 1, "V"),
    rating_row("Common mode VIN+/VIN- (abs, applied 12 to 40 V)", VBUS_LO, VBUS_HI, ina.param("vcm_abs"), "vcm_abs", 1, "V"),
    rating_row("Common mode VIN+/VIN- (rec, applied 12 to 40 V)", VBUS_LO, VBUS_HI, ina.param("vcm_rec"), "vcm_rec", 1, "V"),
    DIFF_ROW,
    rating_row("IIN into any pin (applied: input bias IB max)", 0.0, IB.max, ina.param("iin_abs"), "iin_abs", 1e3, "mA"),
    rating_row("IOUT, digital output (applied: ALERT_N sink I_alert max)", 0.0, I_ALERT_MAX, ina.param("iout_abs"),
               "iout_abs", 1e3, "mA"),
    rating_row("VS supply, rec (applied 3.3 V +/-3 %)", VS_MIN, VS_MAX, ina.param("vs_rec"), "vs_rec", 1, "V"),
    rating_row("VS supply, abs max (applied 3.3 V +/-3 %)", VS_MIN, VS_MAX, ina.param("vs_abs"), "vs_abs", 1, "V"),
    rating_row("VIO, MOSI/MISO/SCLK/ALERT_N low level (applied 0 V; limit GND - 0.3 V)", 0.0, 0.0,
               VIO_ABS, "vio_abs", 1, "V"),
    rating_row("VIO, MOSI/MISO/SCLK/ALERT_N high level (applied 3.3 V +/-3 % max; limit VS + 0.3 V, VS at its min)",
               LOGIC_HI, LOGIC_HI, VIO_ABS, "vio_abs", 1, "V"),
    rating_row("TA ambient (rec, applied 0 to 70 degC)", TA.min, TA.max, ina.param("ta_rec"), "ta_rec", 1, "degC"),
    ["Junction temperature (INA229, ESTIMATE)", TJ_ROW_TXT[0], TJ_ROW_TXT[1], TJ_ROW_TXT[2], TJ_ROW_TXT[3],
     cite(TJ_ABS_P)],
    VDIFF_ROW,
    ["Shunt dissipation vs rating (70 degC)", f"0 to {PW.max:.4g} W", f"p70 x derate: {RATED:.4g} W",
     f"{PW_MARGIN:.4g} W", status(PW_MARGIN), cite(shunt.param("p70"))],
    ["Shunt sense vs (P x R)^1/2", f"{VS_SH * 1e3:.4g} mV", f"(P x R)^1/2 = {MWV * 1e3:.4g} mV",
     f"{(MWV - VS_SH) * 1e3:.4g} mV", status(MWV - VS_SH), cite(shunt.param("max_working_voltage"))],
    ["Storage temperature Tstg (abs)", "-", f"{cite(ina.param('tstg_abs'))[:0]}`tstg_abs` -65 to 150 degC", "-",
     "rating only / owner: assembly", cite(ina.param("tstg_abs"))],
    ["ESD HBM (abs)", "-", "`esd_hbm` +/-2000 V", "-", "rating only / owner: assembly", cite(ina.param("esd_hbm"))],
    ["ESD CDM (abs)", "-", "`esd_cdm` +/-1000 V", "-", "rating only / owner: assembly", cite(ina.param("esd_cdm"))],
]
md += ["## 11. Ratings (absolute maximum and recommended operating)", ""]
md += mdtable(["Check", "Applied (worst case)", "Rating (library key)", "Margin", "Result", "Cite"], ratings)
md += [
    "",
    "VIO (`vio_abs`, DS p.3): the printed limits are GND - 0.3 V and VS + 0.3 V, referenced to GND and VS. The supply "
    "is taken at its MINIMUM for the upper limit, as the library's `resolve` does: VS + 0.3 V = "
    f"{VS_MIN + 0.3:.3f} V. The applied high level is the 3.3 V +/-3 % maximum, {VS_MAX:.3f} V, so the margin is "
    f"{(VS_MIN + 0.3 - VS_MAX) * 1e3:.0f} mV. This is the basis of the +/-3 % rail in spec.md: a 3.3 V +/-5 % rail "
    f"(3.465 V maximum) would fail by {(VS_NOM * 1.05 - (VS_NOM * 0.95 + 0.3)) * 1e3:.0f} mV "
    f"(3.465 V against {VS_NOM * 0.95 + 0.3:.3f} V). CS is not in the DS row (DS omission, library note): not checked.",
    "",
    "Differential check: the hard-short differential equals the 40 V bus across the shunt (zero drop at the short). "
    "It sits exactly at the `vin_diff_abs` max of 40 V (DS p.3). Zero margin: OPEN ITEM (needs a clamp, a "
    "lower bus maximum, or a DS-approved exception).",
    "",
    "The VS row checks 3.3 V +/-3 % (3.201 to 3.399 V) against `vs_rec` (2.7 to 5.5 V, DS p.4); the "
    "`vs_abs` max is 6 V (DS p.3).",
    "",
    "The ESD rows (DS p.4 ESD ratings) and the storage temperature are rating-only: this board's assembly and handling "
    "process owns them (open item, owner assembly). IIN and IOUT are applied at the worst values this design produces.",
    "",
]

# %% [markdown]
# ## MISO output note (section 12)

# %%
VOH = VOH_ROW
R_OUT_MAX = abs(VOH.min) / 1e-3  # DS p.6, VOH row: 0.4 V drop at the 1 mA test current in the row condition
md += ["## 12. MISO output note", ""]
md += [
    f"`voh_25c` ({cite(VOH)}): the DS bounds the MISO output drop to 0.4 V at 1 mA, so the source resistance is bounded by "
    f"R_out <= 0.4 V / 1 mA = **{R_OUT_MAX:.0f} ohm** from the datasheet alone. The {R_MISO:g} ohm series resistor in the "
    "design is a placeholder DESIGN CHOICE; it is inside that bound but is not a derived value (OPEN ITEM). The row's "
    "condition prints IOL rather than IOH (DS ambiguity, noted in the library).",
    "",
]

# %% [markdown]
# ## Summary
# Every key result, with its limit, margin and status. Estimates are labelled ESTIMATE. The requirement is judged on the
# self-heated worst case at I_MAX; the 100 mA and power results are REPORT ONLY (no limit set).

# %%
def err_row_val(lo, hi, i):
    return f"{pct(lo / i)} to +{pct(hi / i)} %"


dif_name, dif_applied, dif_rating, dif_margin, dif_status = DIFF_ROW[0], DIFF_ROW[1], DIFF_ROW[2], DIFF_ROW[3], DIFF_ROW[4]
vio_hi_row = [r for r in ratings if r[0].startswith("VIO, MOSI/MISO/SCLK/ALERT_N high")][0]

SUMMARY = [
    ["Design shunt (designer choice 2026-11-15, judged)", f"{DESIGN['value'] * 1e3:g} mohm, code {code} (+/-{pct(TOL.max)} %), "
     f"ADCRANGE {ADCRANGE}, `{MPN}`", "availability (GOOD: 2+ distributors)",
     f"{DESIGN['avail']}, {DESIGN['n_dist']} distributor(s); stock today {sk(DESIGN)} (informational)",
     "PASS (availability GOOD)" if DESIGN["avail"] == "GOOD" else f"{DESIGN['avail']} (open item: lifecycle not checked)"],
    [f"Sense voltage headroom (self-heated worst Vsense at {I_MAX:g} A)", f"{mv(VS_SH)} mV", f"{mv(ADC_LIM[ADCRANGE])} mV "
     f"(ADCRANGE {ADCRANGE})", f"{mv(VS_MARGIN)} mV ({pct(VS_MARGIN / ADC_LIM[ADCRANGE])} %)", status(VS_MARGIN)],
    [f"Shunt dissipation at {I_MAX:g} A, 70 degC (ambient rating)", f"{PW.max:.4g} W", f"{RATED:.4g} W (p70 x derate)",
     f"{PW_MARGIN:.4g} W ({pct(PW_MARGIN / RATED)} %)", status(PW_MARGIN)],
    [f"Shunt element temperature at {I_MAX:g} A, 70 degC (theta_est " f"{THETA:g} degC/W, self-consistent)",
     f"{T_HOT_5:.4g} degC", f"{T_ELEM_LIMIT:g} degC (ASSUMED element limit)", f"{T_MARGIN_5:.4g} degC",
     elem_status(T_MARGIN_5)],
    [f"Full-scale current error at {I_MAX:g} A, SELF-HEATED (judged)", f"{err_row_val(FS_SH_MIN, FS_SH_MAX, I_MAX)} (max "
     f"{FS_SH_PCT:.3f} %)", f"+/-{ERR_LIMIT_PCT:g} % of reading", f"{ERR_LIMIT_PCT - FS_SH_PCT:+.3f} % of reading",
     f"{err_pf(FS_SH_PCT)}, ESTIMATE"],
    [f"Full-scale current error at {I_MAX:g} A, ambient only (reference)", err_row_val(FS_MIN, FS_MAX, I_MAX)
     + f" (max {FS_AMB_PCT:.3f} %)", "reference only", "n/a", "REFERENCE (not judged)"],
    ["Current error at 100 mA, self-heated (report)", err_row_val(FS_SH_LO01_MIN, FS_SH_LO01_MAX, I_LOW)
     + f" (max {FS_SH_LO01_PCT:.3f} %)", "none set at 100 mA", "n/a", "REPORT ONLY (no limit set), ESTIMATE"],
    ["Current error at 100 mA, ambient only (report)", err_row_val(FS_AMB_LO01_MIN, FS_AMB_LO01_MAX, I_LOW)
     + f" (max {FS_AMB_LO01_PCT:.3f} %)", "none set at 100 mA", "n/a", "REPORT ONLY (no limit set)"],
    ["Bus-voltage error at 12 V (incl. 1 LSB)", f"+/-{mv(BUS_12)} mV (+/-{pct(BUS_12 / VBUS_LO)} %)",
     "none set", "n/a", "REPORT ONLY (no limit set)"],
    ["Bus-voltage error at 40 V (incl. 1 LSB)", f"+/-{mv(BUS_40)} mV (+/-{pct(BUS_40 / VBUS_HI)} %)",
     "none set", "n/a", "REPORT ONLY (no limit set)"],
    [f"Power error at {I_MAX:g} A, 12 V (ambient)", f"{pct(PW_5[12.0][0])} to +{pct(PW_5[12.0][1])} %",
     "none set", "n/a", "REPORT ONLY (no limit set)"],
    [f"Power error at {I_MAX:g} A, 40 V (ambient)", f"{pct(PW_5[40.0][0])} to +{pct(PW_5[40.0][1])} %",
     "none set", "n/a", "REPORT ONLY (no limit set)"],
    ["INA229 junction temperature at TA 70 degC (typical-based)", f"{TJ_ALL:.4g} degC (all power)",
     f"{TJ_ABS:g} degC (tj_abs)", f"{TJ_MARGIN:.4g} degC", est_status(TJ_MARGIN)],
    ["INA229 quiescent current, active worst", f"{ma(IQ_FULL)} mA (iq_full); {ma(IQ_25.typ)} mA typ at 25 degC",
     "none set", "n/a", "REPORT ONLY"],
    ["Quiescent power, V3V3 total worst (active + ALERT_N asserted)", f"{mw(TOT_V3_WORST)} mW",
     "none set", "n/a", "REPORT ONLY"],
    ["Firmware: CURRENT_LSB, SHUNT_CAL", f"{CURRENT_LSB * 1e6:.6g} uA/LSB; SHUNT_CAL {SHUNT_CAL}",
     "exact integer (no spec limit)" if CAL_EXACT else "integer (no spec limit)",
     f"{(SHUNT_CAL_RAW - SHUNT_CAL):+.2g} count",
     "CHECK (exact integer; no spec limit)" if CAL_EXACT else
     f"CHECK (rounded integer, {CAL_ERR * 100:+.3g} % gain term; no spec limit)"],
    [dif_name, dif_applied, dif_rating, dif_margin, dif_status],
    ["VIO high level, MOSI/MISO/SCLK/ALERT_N (3.3 V +/-3 %)", vio_hi_row[1], vio_hi_row[2], vio_hi_row[3], vio_hi_row[4]],
    ["MISO output source resistance (design placeholder)", f"{R_MISO:g} ohm", f"<= {R_OUT_MAX:.0f} ohm (`voh_25c`)",
     f"{R_OUT_MAX - R_MISO:.0f} ohm", "PASS (placeholder, OPEN ITEM)"],
    ["FALLBACK shunt (same value, GOOD availability, section 1.4)",
     (f"`{FALLBACK['mpn']}`, {FALLBACK['value'] * 1e3:g} mohm code {FALLBACK['code']}" if FALLBACK is not None else "-"),
     "1 % self-heated (full scale)",
     (f"{ERR_LIMIT_PCT - FALLBACK['fs5_pct']:+.3f} % of reading" if FALLBACK is not None else "-"),
     (f"{err_pf(FALLBACK['fs5_pct'])}, ESTIMATE; alternate, not selected" if FALLBACK is not None else "-")],
]

md_summary = ["## Summary of key results", ""]
md_summary += mdtable(["Result", "Value (worst case)", "Limit", "Margin", "Status"], SUMMARY)
md_summary += [
    "",
    "Status: PASS / FAIL against a limit from spec.md or the datasheet. ESTIMATE: a curve-based or typical-based result "
    "(theta_est, rtheta_ja, the self-heating band), not a datasheet bound. REPORT ONLY: no limit set in spec.md "
    "(`ERR_LIMIT_PCT` is set to 1 % for the full-scale current error only). REFERENCE: shown for comparison, not judged. "
    "OPEN ITEM: passes with zero or placeholder margin. CHECK: a value with no spec limit to judge against.",
    "",
]

# %% [markdown]
# ## Limits of this analysis (section 13)

# %%
md += ["## 13. Limits of this analysis", ""]
md += [
    "- DC steady state only. No transient, no SPI timing, no ADC conversion-time effects.",
    "- 25 degC-only rows are extended by their drift rows (SPEC recipe step 2). Drift rows are used as printed; GS_DRFT and "
    "GB_DRFT are DS-printed under the 25 degC header (DS ambiguity).",
    "- Typical-only rows are not used as bounds: `vos_bus_psrr_25c` is shown separately (section 7), `rtheta_ja` is typical "
    "only (section 9, typical-based estimate), and the `typ` columns of bound rows are centred at zero, not the DS typical "
    "spread. `zvbus_25c` and `ib_25c` are 25 degC rows used at all temperatures (section 10).",
    "- Self-heating (section 3.2) is a self-consistent fixed point: x = theta_est x I^2 x R_worst(TA + x), with R from "
    "`resistance_worst` at the element temperature, iterated to below 0.001 degC. theta_est is a derating-curve estimate of "
    "the shunt hot-spot thermal resistance, not a datasheet value. The result is ESTIMATE; the copper, the layout and the "
    "real element temperature are the open item.",
    "- The element limit (170 degC) is an ASSUMPTION (derate_zero read as the element limit). `t_oper` is an ambient "
    "rating in the library and is not used as the element limit. PASS on the element margin holds only if the limit is 170 degC.",
    "- Copper and connector heating at full scale is not analysed (trace, connector and terminal resistance and their temperature "
    "rise are outside this model).",
    "- PCB thermal coupling between the shunt and the INA229 is ignored: the INA229 is assumed at ambient except for its own "
    "dissipation (section 9).",
    "- The shunt TCR reference temperature (25 degC) and the TCR validity span are not stated in the WSK2512 DS.",
    "- Availability is a proxy from the `pcb bom` snapshot (2026-11-15): distributor listings and regions, with today's stock "
    "informational only. Lifecycle status is NOT checked (no lifecycle data in the WSK2512 library or the snapshot). Verify "
    "lifecycle and PCN with the manufacturer before release (open item).",
    "- ADCRANGE 1 rows use the same Vos rows as ADCRANGE 0: the DS gives no ADCRANGE condition for them (section 1.5). The "
    f"ADCRANGE 1 candidates are the series values up to {ADC1_MAX_MOHM:g} mohm (the 40.96 mV limit at {I_MAX:g} A, "
    "self-heated).",
    "- Excluded terms: CMRR (`cmrr_full`), INL, DNL, INA229 internal die-temperature effects, PCB and Kelvin trace resistance, "
    "the ADC noise, and the 1 LSB term is conservative (a quantizer is +/-0.5 LSB; 1 LSB was requested). SHUNT_CAL rounding "
    "is included (section 5). The INA229 POWER register is not used: the power result here is V x I from the measured "
    "current and bus voltage.",
    "- The gain error row (`gserr_25c`) is specified at VCM = 24 V; the bus is 12-40 V (open item).",
    "- The shunt rating is a DS rating against ambient at 70 degC; the element temperature is an estimate (section 3).",
    f"- The board's `vsense_max` ({VSENSE_MAX_MV:.1f} mV, `ina229.zen`) is the self-heated worst Vsense ({mv(VS_SH)} mV) "
    "rounded up to 0.1 mV. It is a declared value for the rating check, not a measured one.",
    "",
]

# %% [markdown]
# ## Judgement calls (section 14)

# %%
md += ["## 14. Judgement calls", ""]
md += [
    f"- Shunt: designer choice 2026-11-15, {R_NOM * 1e3:g} mohm, code {code}, ADCRANGE {ADCRANGE} (`SHUNT_SELECTED`). Basis: "
    f"best self-heated {I_MAX:g} A accuracy among the 1 % passers ({FS_SH_PCT:.3f} %). Availability is FAIR (one distributor listing) "
    "and is an open item (section 1.4). Fallback: the GOOD-availability 10 mohm code D part (section 1.4).",
    "- Availability: the snapshot is `analysis/data/shunt_stock_2026-11-15_rerank.json` (second check, all 169 candidates). "
    "Ranking uses the availability rating (distributor count, recognition, standard value), not today's stock. The first "
    "snapshot `analysis/data/shunt_stock_2026-11-15.json` is kept unchanged.",
    "- Shunt tolerance and TCR are bound-only rows; their typ is set to zero in the FS and bus analyses (caller choice).",
    "- `gs_drift_25c` and `gb_drift_25c` have no typ in the library and are centred at zero with `with_typ(0.0)`.",
    "- The dVS reference for the PSRR row is assumed to be the 3.3 V nominal (the row prints a VS range, not a "
    "single deviation).",
    "- Self-heating (section 3.2): theta_est = (zero - knee) / p70 from the derating curve; the rise is the "
    "self-consistent fixed point with the worst-case R at the element temperature. The self-heated band is the lowest-R "
    "rise at TA 0 degC up to the worst-R rise at TA 70 degC. The INA229 drift stays at ambient.",
    "- Power error (section 8) combines the ambient current error only. The self-heated power error is not computed; the "
    "The full-scale self-heated current error is in section 5 and is the judged result.",
    "- ALERT_N: the pull-up is 10 kohm (DESIGN CHOICE, spec.md); the sink into the INA229 uses VOL max (0.4 V at 1 mA) with "
    "the asserted current, which is conservative. ALERT_N is assumed asserted in shutdown.",
    "- The ERJ-3EKF pull-up tolerance is referenced to 20 degC (library note) and its TCR to 25 degC.",
    "- Junction temperature includes the VBUS divider at 40 V in the total (the requested path is V3V3 only; both shown).",
    f"- Error limit: +/-1 % of reading at {I_MAX:g} A (designer requirement, 2026-11-15), judged on the self-heated worst case "
    "(`ERR_LIMIT_PCT = 1.0`). The 100 mA error is REPORT ONLY.",
    "- Element temperature limit: 170 degC is an ASSUMPTION (`T_ELEM_LIMIT`). The element margin is ESTIMATE and PASS only "
    "under that assumption.",
    "",
]

# %% [markdown]
# ## Maximum full-scale current study (WSK2512 and WSK2512-LOW)
# Question (designer, 2026-11-15): the highest integer full-scale current the block can support. Requirement: +/-1 % of
# reading at full scale, self-heated worst case, TA 0 to 70 degC, bus 12 to 40 V, V3V3 3.3 V +/-3 %, derate 1.0, no
# fault-headroom requirement. Every integer I_FS from I_START is evaluated for every (series, tolerance code, value,
# ADCRANGE) in the library. A design passes only when all six criteria (a) to (f) hold at the 70 degC worst case. This
# study does not change the design: `SHUNT_SELECTED` (I_MAX, 8 A) stays the current design.

# %%
MAXFS_JSON = DATA / "shunt_stock_2026-11-15_maxfs.json"  # study stock snapshot: WSK2512 rows reused, WSK2512-LOW new
I_START = 5  # A, first integer full-scale current of the study (the scan starts below the current design, I_MAX = 8 A)
I_CAP = 60  # A, search guard; the scan stops on its own (CONFIRM_MORE)
CONFIRM_MORE = 3  # integers above the first empty one that must also be empty before the scan stops
SHUNT_CAL_MAX = 2 ** 15 - 1  # 32767: SHUNT_CAL is a 15-bit field, bits 14-0 (INA229 DS p.23, Table 7-7); bit 15 reserved
RUNAWAY_DEGC = 1000.0  # degC: a self-heating fixed point above this has no steady state (treated as a fail)
LOW = load("Vishay/WSK2512-LOW")
CRIT = ("a", "b", "c", "d", "e", "f")
CRIT_TEXT = {
    "a": "(a) Vsense inside the ADCRANGE VDIFF range",
    "b": "(b) shunt dissipation <= rated x derate at 70 degC",
    "c": "(c) (P x R)^1/2 >= Vsense",
    "d": "(d) self-heated worst-case error <= 1 % of reading",
    "e": "(e) element temperature <= derate_zero (ASSUMED element limit)",
    "f": "(f) SHUNT_CAL integer in the register range 1 to 32767",
}
CRIT_SHORT = {"a": "Vsense range", "b": "dissipation", "c": "(P x R)^1/2", "d": "error 1 %", "e": "element T",
              "f": "SHUNT_CAL"}
CRIT_CHART = {"a": "Vsense", "b": "dissip.", "c": "(PR)^1/2", "d": "error", "e": "elem. T", "f": "SHUNT_CAL"}  # chart labels

# %%
# Series constants, each read from its own library rows (value table, tolerance, TCR, power, derating curve).
MF = {}
for _name, _part in (("WSK2512", shunt), ("WSK2512-LOW", LOW)):
    _p70 = _part.param("p70").max  # W, P70 degC (DS p.1)
    _knee = _part.param("derate_knee").typ  # degC, DS p.3 curve
    _zero = _part.param("derate_zero").typ  # degC, DS p.3 curve
    _kw = _part.inputs("rated_power")  # {p70, knee_c, zero_c} as floats
    MF[_name] = dict(
        part=_part, p70=_p70, knee=_knee, zero=_zero,
        theta=(_zero - _knee) / _p70,  # degC/W, ESTIMATE: this series' own derating curve (theta_est, section 3.2)
        rated=_part.calc.rated_power(ta_c=TA.max, **_kw) * DERATE,  # W, rating at 70 degC x derate
        pairs=_part.pairs(),  # [(value ohm, code)]: every orderable pair the library allows
    )

# %%
def load_maxfs_stock(path):
    """(raw record, {(series, code, value_mohm): {match, mpn, offers}}) from the study snapshot. Offers are the BOM
    distributor listings (distributor, region, stock), kept even at zero stock."""
    raw = json.loads(path.read_text())
    table = {}
    for row in raw["rows"]:
        offers = (row.get("availability") or {}).get("offers") or []
        table[(row["series"], row["code"], round(float(row["value_mohm"]), 6))] = dict(
            match=row["bom_match"], mpn=row.get("bom_mpn"),
            offers=[(o["distributor"], o["region"], o["stock"]) for o in offers])
    return raw, table


MAXFS_RAW, MAXFS_STOCK = load_maxfs_stock(MAXFS_JSON)
_LOW_NOSTOCK = [(v, c) for v, c in MF["WSK2512-LOW"]["pairs"]
                if ("WSK2512-LOW", c, round(v * 1e3, 6)) not in MAXFS_STOCK]
assert not _LOW_NOSTOCK, f"WSK2512-LOW pairs without a stock row: {_LOW_NOSTOCK}; re-run the stock check"
_AV_CACHE = {}


def stock_info(name, code, v):
    """Availability record of one (series, code, value) from the study snapshot. The rating is avail_rating (GOOD, FAIR,
    POOR/UNKNOWN). The stock total is INFORMATIONAL only and never ranks a design."""
    key = (name, code, round(v * 1e3, 6))
    if key not in _AV_CACHE:
        rec = MAXFS_STOCK.get(key)
        if rec is None:
            raise RuntimeError(f"no stock row for {name} {code} {v * 1e3:g} mohm: re-run the stock check")
        offers = rec["offers"]
        dists = sorted({d for d, _, _ in offers})
        standard = any(abs(v - x) <= 1e-9 * x for x in MF[name]["part"].values(tol=code))
        info = dict(match=rec["match"], n_dist=len(dists), dists=dists,
                    regions=sorted({g for _, g, _ in offers}),
                    stock=(sum(s for _, _, s in offers) if offers else None), mpn=rec["mpn"])
        info["avail"] = avail_rating(info, standard)
        _AV_CACHE[key] = info
    return _AV_CACHE[key]


# %%
# Self-heating and error functions. Same physics as the WSK2512 cells above, with the calc module passed in, so the
# LOW series uses its own resistance_worst (TCR row by value, tolerance code). Not DS equations unless cited.
def _mf_rworst(calc, r0, tol, tcr, t_c):
    return calc.resistance_worst(r0, tol, tcr, t_c, T_REF)


def mf_r_side(calc, r0, tol, tcr, t_c, side):
    """Not a DS equation: highest ('hi') or lowest ('lo') resistance (ohm) over the tolerance and TCR rows at t_c."""
    w = wca(partial(_mf_rworst, calc), unit="ohm", r0=r0, tol=tol, tcr=tcr, t_c=t_c)
    return w.max if side == "hi" else w.min


def mf_rise(name, i, r0, tol, tcr, ta, side):
    """ESTIMATE, not a DS equation: self-heating rise (degC) of one element at ambient ta. x = theta x I^2 x R(ta + x)
    with theta from THIS series' derating curve. Fixed point from x = 0. Returns (x, converged)."""
    st = MF[name]
    x = 0.0
    for _ in range(300):
        x_new = st["theta"] * i * i * mf_r_side(st["part"].calc, r0, tol, tcr, ta + x, side)
        if x_new > RUNAWAY_DEGC:
            return x_new, False
        if abs(x_new - x) < 1e-10:
            return x_new, True
        x = x_new
    return x, False


def mf_elem(name, code, v, i):
    """ESTIMATE: element state of one candidate at full scale i. t_el = TA max + worst-R rise (the judged point),
    t_lo = TA min + lowest-R rise (band low edge), r_el = worst R at t_el (ohm)."""
    part = MF[name]["part"]
    tol = part.tolerance(code).with_typ(0.0)  # bound-only row (min and max used)
    tcr = part.tcr(v).with_typ(0.0)
    x_hi, conv = mf_rise(name, i, v, tol, tcr, TA.max, "hi")
    t_hi = TA.max + x_hi
    x_lo, _ = mf_rise(name, i, v, tol, tcr, TA.min, "lo")
    r_el = mf_r_side(part.calc, v, tol, tcr, t_hi, "hi") if conv else float("inf")
    return dict(t_el=t_hi, t_lo=TA.min + x_lo, conv=conv, r_el=r_el, tol=tol, tcr=tcr)


def _mf_fs_err(calc, i_true, r_nom, r_tol, r_tcr, t_sh, t_amb, gserr, gs_drift, vos25, vos_drift, cal_gain):
    """Not a DS equation: fs_error with the shunt at element temperature t_sh and the INA229 at t_amb (ESTIMATE). Same
    derivation as fs_error_sh: i_seen = i_true x R_act / R_nom x cal_gain, error = (i_seen - i_true) + current_error."""
    r_act = calc.resistance_worst(r_nom, r_tol, r_tcr, t_sh, T_REF)
    i_seen = i_true * r_act / r_nom * cal_gain
    return (i_seen - i_true) + ina.calc.current_error(i_seen, r_nom, vos25, vos_drift, gserr, gs_drift, t_amb)


def mf_err_mag(name, code, v, i_eval, lsb, gain, band, samples=0):
    """ESTIMATE: judged self-heated worst-case error magnitude (A, with the 1 LSB term) at current i_eval, the larger of
    the two DS Vos rows kept. band: element temperature Param for i_eval. Multilinear in (t_sh, t_amb): corners exact
    (samples = 0); samples > 0 is the interior check."""
    part = MF[name]["part"]
    tol = part.tolerance(code).with_typ(0.0)
    tcr = part.tcr(v).with_typ(0.0)
    best = 0.0
    for k in VOS_KEYS:
        w = wca(partial(_mf_fs_err, part.calc), unit="A", samples=samples, i_true=i_eval, r_nom=v, r_tol=tol,
                r_tcr=tcr, t_sh=band, t_amb=TA, gserr=GSERR, gs_drift=GS_DRIFT, vos25=ina.param(k),
                vos_drift=VOS_DRIFT, cal_gain=gain)
        best = max(best, abs(w.min - lsb), abs(w.max + lsb))
    return best


def _mf_power(calc, i_true, v_true, r_nom, r_tol, r_tcr, gserr, gs_drift, vos25, vos_drift, cal_gain, lsb_i,
              vos_bus, vos_bus_drift, gberr, gb_drift, lsb_v, t_c):
    """Not a DS equation: fractional power error (V x I reading), ambient only, as section 8 (power_error) but with this
    series' calc: (1 + e_I / I)(1 + e_V / V) - 1."""
    e_i = _mf_fs_err(calc, i_true, r_nom, r_tol, r_tcr, t_c, t_c, gserr, gs_drift, vos25, vos_drift, cal_gain) + lsb_i
    e_v = vbus_error(v_true, vos_bus, vos_bus_drift, 0.0, 0.0, gberr, gb_drift, t_c) + lsb_v
    return (i_true + e_i) * (v_true + e_v) / (i_true * v_true) - 1.0


def mf_power_band(name, code, v, i, gain, v_bus):
    """Ambient-only worst-case (min, max) fractional power error at full scale i and bus v_bus (report only). The ambient
    is enumerated over T_POWER (the product of drift and temperature makes it quadratic in T), both Vos rows."""
    part = MF[name]["part"]
    tol = part.tolerance(code).with_typ(0.0)
    tcr = part.tcr(v).with_typ(0.0)
    lsb = ina.calc.current_lsb(i)
    lsb_i = Param(min=-lsb, max=lsb, unit="A")
    lsb_v = Param(min=-LSB_VBUS, max=LSB_VBUS, unit="V")
    lo = hi = None
    for t in T_POWER:
        for k in VOS_KEYS:
            w = wca(partial(_mf_power, part.calc), unit="1", samples=0, i_true=i, v_true=v_bus, r_nom=v, r_tol=tol,
                    r_tcr=tcr, gserr=GSERR, gs_drift=GS_DRIFT, vos25=ina.param(k), vos_drift=VOS_DRIFT,
                    cal_gain=gain, lsb_i=lsb_i, vos_bus=VOS_BUS, vos_bus_drift=VOS_BUS_DRIFT, gberr=GBERR,
                    gb_drift=GB_DRIFT, lsb_v=lsb_v, t_c=t)
            lo = w.min if lo is None else min(lo, w.min)
            hi = w.max if hi is None else max(hi, w.max)
    return lo, hi


def mf_max_current_self_heated(name, code, v, rated):
    """ESTIMATE: largest continuous current (A) with I^2 x R_element(I) = rated (bisection; R_element self-heated at
    TA 70 degC). The rating basis (ambient R) is calc.max_current(rated, R_worst at 70 degC), reported next to it."""
    lo, hi = 0.0, 1000.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if mid * mid * mf_elem(name, code, v, mid)["r_el"] > rated:
            hi = mid
        else:
            lo = mid
    return lo

# %%
def mf_cal(raw):
    """SHUNT_CAL as written (round half up) and the integers the judged error must cover. A raw value on a .5 tie can be
    written either way (float noise decides a tie otherwise), so both integers are judged and the worse one is kept."""
    cal = math.floor(raw + 0.5)
    tie = abs((raw - math.floor(raw)) - 0.5) < 1e-9
    return cal, ([math.floor(raw), math.floor(raw) + 1] if tie else [cal]), tie


def mf_judged_err(name, code, v, i, lsb, raw, band, samples=0):
    """ESTIMATE: judged self-heated error magnitude (A) over the SHUNT_CAL integers that can be written (mf_cal)."""
    _, cands, _ = mf_cal(raw)
    return max(mf_err_mag(name, code, v, i, lsb, c / raw, band, samples) for c in cands)


def mf_candidate(name, code, v, i):
    """All six criteria for one (series, code, value) at full scale i, for ADCRANGE 0 and ADCRANGE 1 (two records).
    Criterion margins are in the units of the criterion; a margin >= 0 passes. (c) is (b) at the same R (exact identity:
    (P x R)^1/2 >= I x R  <=>  P >= I^2 x R), so it fails with (b) and is counted once in n_fail."""
    st = MF[name]
    part = st["part"]
    e = mf_elem(name, code, v, i)
    lsb = ina.calc.current_lsb(i)  # CURRENT_LSB = I_FS / 2^19 (DS p.30, Equation 3)
    vs = i * e["r_el"]
    p_el = i * i * e["r_el"]
    mwv = part.calc.max_working_voltage(st["rated"], e["r_el"])
    band = Param(min=e["t_lo"], max=e["t_el"], unit="degC")
    out = []
    for adc in (0, 1):
        raw = ina.calc.shunt_cal(lsb, v, adcrange=adc)  # DS p.30, Equation 2 (x4 for ADCRANGE 1)
        cal, _, tie = mf_cal(raw)
        gain = cal / raw
        err = mf_judged_err(name, code, v, i, lsb, raw, band) / i * 100 if e["conv"] else float("inf")  # %
        rec = dict(
            series=name, code=code, value=v, adc=adc, i=i, mpn=part.mpn(v, tol=code), conv=e["conv"],
            vs=vs, vdiff=ADC_LIM[adc], m_a=ADC_LIM[adc] - vs, headroom=(ADC_LIM[adc] - vs) / ADC_LIM[adc],
            p_el=p_el, rated=st["rated"], m_b=st["rated"] - p_el,
            mwv=mwv, m_c=mwv - vs,
            err=err, m_d=ERR_LIMIT_PCT - err,
            t_el=e["t_el"], zero=st["zero"], m_e=st["zero"] - e["t_el"],
            raw=raw, cal=cal, gain=gain, tie=tie, lsb=lsb, m_f=min(cal - 1, SHUNT_CAL_MAX - cal),
        )
        rec["ok"] = {"a": rec["m_a"] >= 0, "b": rec["m_b"] >= 0, "c": rec["m_c"] >= 0,
                     "d": err <= ERR_LIMIT_PCT + 1e-12, "e": e["conv"] and rec["m_e"] >= 0,
                     "f": rec["m_f"] >= 0}
        rec["fails"] = [k for k in CRIT if not rec["ok"][k]]
        rec["n_fail"] = len([k for k in rec["fails"] if k != "c"])  # (c) mirrors (b): counted once
        rec["pass"] = not rec["fails"]
        out.append(rec)
    return out


MF_SCAN = {}  # I_FS (integer A) -> every candidate record (both ADCRANGE settings)
_zero_run = 0
for _i in range(I_START, I_CAP + 1):
    _recs = []
    for _name in MF:
        for _v, _code in MF[_name]["pairs"]:
            _recs += mf_candidate(_name, _code, _v, float(_i))
    MF_SCAN[_i] = _recs
    _npass = sum(1 for r in _recs if r["pass"])
    _zero_run = _zero_run + 1 if _npass == 0 else 0
    if _zero_run > CONFIRM_MORE:
        break
else:
    raise RuntimeError("the study did not stop before I_CAP: raise I_CAP")
MF_I = sorted(MF_SCAN)
assert any(r["pass"] for r in MF_SCAN[MF_I[0]]), "the scan's first integer must have a passing design"
assert any(r["pass"] and r["series"] == "WSK2512" and r["code"] == code and abs(r["value"] - R_NOM) < 1e-12
           and r["adc"] == ADCRANGE for r in MF_SCAN[int(I_MAX)]), "the current design must pass the study at I_MAX"

# %%
# Availability: only passing designs need a rating (the snapshot must cover them: a passing design without a row is a
# stale snapshot). Passing designs at I_FS >= 5 A are a subset of the 5 A ADCRANGE 0 fit (Vsense rises with I), which the
# snapshot covers.
for _i in MF_I:
    for r in MF_SCAN[_i]:
        if r["pass"]:
            info = stock_info(r["series"], r["code"], r["value"])
            r["avail"] = info["avail"]
            r["n_dist"] = info["n_dist"]
            r["dists"] = info["dists"]
            r["regions"] = info["regions"]
            r["stock"] = info["stock"]
            r["bom_mpn"] = info["mpn"]
        else:
            r["avail"] = None

AVAIL_RANK = {"GOOD": 0, "FAIR": 1, "POOR/UNKNOWN": 2}


def mf_rank_key(r):
    """Ranking of passing designs: availability (GOOD before FAIR before POOR/UNKNOWN), then lowest self-heated error
    (compared at 0.01 %), then lower dissipation at 70 degC (element resistance)."""
    return (AVAIL_RANK[r["avail"]], round(r["err"], 2), r["p_el"])


def mf_key(r):
    return (r["series"], r["code"], r["value"], r["adc"])


def mf_label(r):
    """Short design label: series, value, code, ADCRANGE, part number, availability."""
    return (f"{r['series']} {r['value'] * 1e3:g} mohm {r['code']}, ADCRANGE {r['adc']}, `{r['mpn']}` "
            f"({r['avail']})")


MF_SUM = {}
for _i in MF_I:
    _recs = MF_SCAN[_i]
    _pass = sorted([r for r in _recs if r["pass"]], key=mf_rank_key)
    _closest = min(_recs, key=lambda r: (r["n_fail"], r["err"]))
    MF_SUM[_i] = dict(
        ranked=_pass,
        n_pass=len(_pass),
        n_gf=sum(1 for r in _pass if r["avail"] in ("GOOD", "FAIR")),
        n_good=sum(1 for r in _pass if r["avail"] == "GOOD"),
        closest=_closest,
        binding=(_closest["fails"][0] if _closest["fails"] else None),
    )

# First I_FS at which each criterion fails, per candidate (the scan is ascending). The first criterion to fail as the
# current rises is the binding one for that design (criterion c is the same test as b and is not counted separately).
MF_FIRST_FAIL = {}
for _i in MF_I:
    for r in MF_SCAN[_i]:
        d = MF_FIRST_FAIL.setdefault(mf_key(r), {})
        for k in r["fails"]:
            if k != "c":
                d.setdefault(k, _i)


def mf_limit(r):
    """(last integer I_FS in the scan where this design still passes all criteria, binding criteria list). The binding
    list is every criterion that fails first at the first failing integer. A design that already fails at I_START has
    no passing integer (None, with its binding list). A design that never fails in the scan has (None, [])."""
    d = MF_FIRST_FAIL.get(mf_key(r), {})
    if not d:
        return None, []
    first = min(d.values())
    last = first - 1 if first > I_START else None
    return last, sorted(k for k, v in d.items() if v == first)


# %%
# Top-level answers: maxima and the recommended design.
I_MAX_GF = max(i for i in MF_I if MF_SUM[i]["n_gf"] > 0)  # primary: GOOD or FAIR passing design
I_MAX_GOOD = max(i for i in MF_I if MF_SUM[i]["n_good"] > 0)  # GOOD only
I_MAX_ANY = max(i for i in MF_I if MF_SUM[i]["n_pass"] > 0)  # any availability (electrical pass only)
I_FIRST_EMPTY = min(i for i in MF_I if MF_SUM[i]["n_pass"] == 0)  # first integer with no passing design
REC = [r for r in MF_SUM[I_MAX_GOOD]["ranked"] if r["avail"] == "GOOD"][0]  # highest GOOD rated design
REC_LIMIT, REC_BIND = mf_limit(REC)
ALT = None  # the FAIR alternative only when it allows a higher integer rating than the GOOD design
if I_MAX_GF > I_MAX_GOOD:
    ALT = [r for r in MF_SUM[I_MAX_GF]["ranked"] if r["avail"] in ("GOOD", "FAIR")][0]
ALT_LIMIT, ALT_BIND = mf_limit(ALT) if ALT is not None else (None, [])
CLOSEST_NEXT = MF_SUM[I_FIRST_EMPTY]["closest"]  # the best candidate just above the maximum: what fails there
assert MF_SUM[I_MAX_GOOD]["n_pass"] > 0 and REC["pass"]

# %%
# Continuous curves for the top candidates (charts ii and iii): every design that ranks first at some integer I_FS,
# plus the recommended and the FAIR alternative.
MF_TOP = []
for _i in MF_I:
    if MF_SUM[_i]["ranked"]:
        _k = mf_key(MF_SUM[_i]["ranked"][0])
        if _k not in [mf_key(t) for t in MF_TOP]:
            MF_TOP.append(MF_SCAN[_i][[mf_key(x) for x in MF_SCAN[_i]].index(_k)])
for _extra in [REC] + ([ALT] if ALT is not None else []):
    if mf_key(_extra) not in [mf_key(t) for t in MF_TOP]:
        MF_TOP.append(_extra)
MF_GRID = [0.5 + 0.25 * k for k in range(int((max(MF_I) + 2 - 0.5) / 0.25) + 1)]  # A, 0.5 to max+2 in 0.25 A steps
MF_CURVE = {}  # key -> {"p": [W], "t": [degC]} along MF_GRID
for _r in MF_TOP:
    _p, _t = [], []
    for _x in MF_GRID:
        _e = mf_elem(_r["series"], _r["code"], _r["value"], _x)
        _p.append(_x * _x * _e["r_el"] if _e["conv"] else float("nan"))
        _t.append(_e["t_el"])
    MF_CURVE[mf_key(_r)] = {"p": _p, "t": _t}

# %%
# Recommendation detail: every number of the recommended design, at its maximum integer I_FS.
def mf_detail(r):
    """ESTIMATE/REPORT numbers for one design at its full-scale current r['i']: the self-heated error with the interior
    check (2 samples), the error at 1 % of full scale (report only), the ambient reference, the continuous current at
    70 degC on the rating basis and self-heated, and the power error at 12 and 40 V (ambient, report only)."""
    name, code, v, i = r["series"], r["code"], r["value"], r["i"]
    part = MF[name]["part"]
    tol = part.tolerance(code).with_typ(0.0)
    tcr = part.tcr(v).with_typ(0.0)
    e = mf_elem(name, code, v, i)
    band = Param(min=e["t_lo"], max=e["t_el"], unit="degC")
    d = {}
    d["err_s2"] = mf_judged_err(name, code, v, i, r["lsb"], r["raw"], band, samples=2) / i * 100
    lo_i = 0.01 * i
    e_lo = mf_elem(name, code, v, lo_i)
    band_lo = Param(min=e_lo["t_lo"], max=e_lo["t_el"], unit="degC")
    d["err_lo"] = mf_judged_err(name, code, v, lo_i, r["lsb"], r["raw"], band_lo) / lo_i * 100
    d["t_lo"] = e["t_lo"]
    band_amb = Param(min=TA.min, max=TA.max, unit="degC")
    d["err_amb"] = mf_judged_err(name, code, v, i, r["lsb"], r["raw"], band_amb) / i * 100
    d["i_amb"] = part.calc.max_current(r["rated"], mf_r_side(part.calc, v, tol, tcr, TA.max, "hi"))
    d["i_sh"] = mf_max_current_self_heated(name, code, v, r["rated"])
    d["pw12"] = mf_power_band(name, code, v, i, r["gain"], VBUS_LO)
    d["pw40"] = mf_power_band(name, code, v, i, r["gain"], VBUS_HI)
    d["band_lo_t"] = e_lo["t_el"]
    return d


REC_D = mf_detail(REC)
ALT_D = mf_detail(ALT) if ALT is not None else None
REC_MPN_BOM_OK = REC["bom_mpn"] == REC["mpn"]
assert REC_MPN_BOM_OK, f"BOM MPN {REC['bom_mpn']} differs from the library MPN {REC['mpn']}"

# LOW reference: the best WSK2512-LOW design at the study start, self-heated and ambient-only (no self-heating).
LOW_REF = min([r for r in MF_SCAN[I_START] if r["series"] == "WSK2512-LOW" and r["adc"] == 0], key=lambda r: r["err"])
LOW_REF_AMB = mf_judged_err("WSK2512-LOW", LOW_REF["code"], LOW_REF["value"], float(I_START), LOW_REF["lsb"],
                            LOW_REF["raw"], Param(min=TA.min, max=TA.max, unit="degC")) / I_START * 100
_LOW_ALL = [r for r in MF_SCAN[I_START] if r["series"] == "WSK2512-LOW" and r["adc"] == 0]
LOW_BELOW3 = [r for r in _LOW_ALL if r["value"] < 3e-3 - 1e-12]
LOW_ABOVE3 = [r for r in _LOW_ALL if r["value"] >= 3e-3 - 1e-12]
LOW_TCR_TXT = {}
for _row in LOW.info["series_tcr"]:
    _t, _rg = LOW.param(_row["tcr"]), LOW.param(_row["range"])
    LOW_TCR_TXT[_row["tcr"]] = (_t.max * 1e6, _rg.min * 1e3, _rg.max * 1e3)

# %%
def mf_name(r):
    return f"{r['series']} {r['value'] * 1e3:g} mohm, code {r['code']}, ADCRANGE {r['adc']}"


def mf_fails_txt(r):
    return ", ".join(f"{k} {CRIT_SHORT[k]}" for k in r["fails"] if k != "c") or "none"


def mf_bind_txt(keys):
    """Text of a binding list: 'b (dissipation; identical to (c))' when b is the binding test."""
    out = []
    for k in keys:
        out.append(f"({k}) {CRIT_SHORT[k]}" + ("; same test as (c)" if k == "b" else ""))
    return ", ".join(out)


# WSK2512-LOW without self-heating (ambient 0 to 70 degC only), at I_START: separates the TCR term from self-heating.
LOW_AMB = {}
for _r in _LOW_ALL:
    LOW_AMB[mf_key(_r)] = mf_judged_err("WSK2512-LOW", _r["code"], _r["value"], float(I_START), _r["lsb"], _r["raw"],
                                        Param(min=TA.min, max=TA.max, unit="degC")) / I_START * 100
LOW_AMB_BEST = min(LOW_AMB.values())
LOW_AMB_BELOW3 = min(v for k, v in LOW_AMB.items() if k[2] < 3e-3 - 1e-12)
LOW_AMB_ABOVE3 = min(v for k, v in LOW_AMB.items() if k[2] >= 3e-3 - 1e-12)


# Chart (i): best self-heated error vs I_FS, with the 1 % line and the binding constraint annotated.
fig, ax = plt.subplots(figsize=(9.6, 5.4))
_pa = [(i, MF_SUM[i]["ranked"][0]["err"]) for i in MF_I if MF_SUM[i]["ranked"]]
_pg = [(i, [r for r in MF_SUM[i]["ranked"] if r["avail"] == "GOOD"][0]["err"]) for i in MF_I if MF_SUM[i]["n_good"]]
_cl = [(i, MF_SUM[i]["closest"]["err"]) for i in MF_I if MF_SUM[i]["n_pass"] == 0]
_ymax = max([e for _, e in _cl] + [ERR_LIMIT_PCT]) * 1.12
ax.plot([i for i, _ in _pa], [e for _, e in _pa], color=BLUE, lw=1.4, marker="o", ms=5,
        label="best passing design (any availability; rank 1)")
ax.plot([i for i, _ in _pg], [e for _, e in _pg], color=GREEN, lw=0, marker="s", ms=6,
        label="best GOOD-availability passing design")
ax.plot([i for i, _ in _cl], [e for _, e in _cl], color=ORANGE, lw=0, marker="o", ms=7, mfc="white", mew=1.4,
        label="closest design where nothing passes (fails shown)")
ax.axhline(ERR_LIMIT_PCT, color=INK, lw=1.0, ls="--", label=f"requirement: {ERR_LIMIT_PCT:g} % of reading")
for i in MF_I:
    s = MF_SUM[i]
    if s["n_pass"] == 0:
        c = s["closest"]
        _k = s["binding"]
        ax.text(i, 0.04, f"no pass\n({_k}) {CRIT_CHART[_k]}", ha="center", va="bottom", fontsize=7.5, color=INK)
ax.set_xticks(MF_I)
ax.set_ylim(0, _ymax)
style_axes(ax, "Full-scale current I_FS (A, integer)", "Self-heated worst-case error at I_FS (% of reading, ESTIMATE)")
ax.set_title("Maximum full-scale study: best self-heated error vs I_FS (WSK2512 and WSK2512-LOW, ADCRANGE 0 and 1)",
             color=INK, fontsize=10)
ax.legend(frameon=False, loc="upper left", fontsize=8, labelcolor=INK)
fig.tight_layout()
save(fig, "maxfs_error.png")

# %%
# Chart (ii): dissipation vs I_FS for the top candidates against the derated rating at 70 degC.
# Chart (iii): element temperature vs I_FS for the same candidates against the ASSUMED element limit.
def mf_curve_label(r):
    return f"{r['series']} {r['value'] * 1e3:g} mohm {r['code']} ADC{r['adc']}"


_LOW_KEY = mf_key(LOW_REF)
MF_CURVE_PLOT = [(mf_key(r), mf_curve_label(r), r) for r in MF_TOP]
if _LOW_KEY not in MF_CURVE:
    _p, _t = [], []
    for _x in MF_GRID:
        _e = mf_elem(LOW_REF["series"], LOW_REF["code"], LOW_REF["value"], _x)
        _p.append(_x * _x * _e["r_el"] if _e["conv"] else float("nan"))
        _t.append(_e["t_el"])
    MF_CURVE[_LOW_KEY] = {"p": _p, "t": _t}
    MF_CURVE_PLOT.append((_LOW_KEY, mf_curve_label(LOW_REF) + " (reference, does not pass)", LOW_REF))
PLOT_COL = [BLUE, ORANGE, GREEN, RED, "#7a4fb0", MUTED]
RATED_LINE = MF["WSK2512"]["rated"]
fig, ax = plt.subplots(figsize=(9.6, 5.2))
for j, (k, lbl, r) in enumerate(MF_CURVE_PLOT):
    col = PLOT_COL[j % len(PLOT_COL)]
    ls = "--" if "reference" in lbl else "-"
    ax.plot(MF_GRID, MF_CURVE[k]["p"], color=col, lw=1.4, ls=ls, label=lbl)
ax.axhline(RATED_LINE, color=INK, lw=1.0, ls=":", label=f"rated at 70 degC x derate: {RATED_LINE:.3g} W")
ax.axvline(I_MAX_GOOD, color=MUTED, lw=0.8, ls=":")
ax.text(I_MAX_GOOD + 0.1, 0.02, f"GOOD max {I_MAX_GOOD} A", color=MUTED, fontsize=8)
style_axes(ax, "Full-scale current I_FS (A)", "Shunt dissipation at 70 degC (W, self-heated R, ESTIMATE)")
ax.set_xlim(MF_GRID[0], MF_GRID[-1])
ax.set_ylim(0, RATED_LINE * 1.6)
ax.set_title("Shunt dissipation vs current for the top candidates, against the rating at 70 degC", color=INK, fontsize=10)
ax.legend(frameon=False, loc="upper left", fontsize=7.5, labelcolor=INK)
fig.tight_layout()
save(fig, "maxfs_dissipation.png")

fig, ax = plt.subplots(figsize=(9.6, 5.2))
for j, (k, lbl, r) in enumerate(MF_CURVE_PLOT):
    col = PLOT_COL[j % len(PLOT_COL)]
    ls = "--" if "reference" in lbl else "-"
    ax.plot(MF_GRID, MF_CURVE[k]["t"], color=col, lw=1.4, ls=ls, label=lbl)
_zero_line = MF["WSK2512"]["zero"]
ax.axhline(_zero_line, color=INK, lw=1.0, ls="--",
           label=f"ASSUMED element limit {_zero_line:g} degC (derate_zero; not t_oper)")
ax.axvline(I_MAX_GOOD, color=MUTED, lw=0.8, ls=":")
style_axes(ax, "Full-scale current I_FS (A)", "Element temperature at TA 70 degC (degC, ESTIMATE)")
ax.set_xlim(MF_GRID[0], MF_GRID[-1])
ax.set_ylim(0, _zero_line * 1.2)
ax.set_title("Shunt element temperature vs current for the top candidates (ESTIMATE, theta from each derating curve)",
             color=INK, fontsize=10)
ax.legend(frameon=False, loc="upper left", fontsize=7.5, labelcolor=INK)
fig.tight_layout()
save(fig, "maxfs_temperature.png")

# %%
# Markdown: the maximum full-scale study section (placed after the summary, before the current-design sections).
MF_MD = ["## Maximum full-scale current study", ""]
MF_MD += [
    "Question (designer, 2026-11-15): the highest integer full-scale current the block can support. Requirement: "
    "+/-1 % of reading at full scale, self-heated worst case, TA 0 to 70 degC, bus 12 to 40 V, V3V3 3.3 V +/-3 %, derate "
    f"1.0, no fault-headroom requirement. The current design (`SHUNT_SELECTED`, {I_MAX:g} A, 10 mohm code B) is NOT changed by "
    "this study; the sections below are the study and a RECOMMENDATION for the designer to approve.",
    "",
    "### MF.0 Result",
    "",
    f"- **Maximum I_FS, primary rule** (at least one passing design rated GOOD or FAIR): **{I_MAX_GF} A**.",
    f"- **GOOD only:** **{I_MAX_GOOD} A**. **Any availability** (electrical pass only, POOR/UNKNOWN included): "
    f"**{I_MAX_ANY} A**.",
    f"- **RECOMMENDATION (GOOD):** {I_MAX_GOOD} A with `{REC['mpn']}` ({mf_name(REC)}; {REC['n_dist']} distributors). "
    f"Self-heated error at {I_MAX_GOOD} A: {REC['err']:.3f} % (limit 1 %, margin {REC['m_d']:.3f} %). "
    f"Limit: {mf_bind_txt(REC_BIND)}; at {REC_LIMIT + 1} A it fails.",
    (f"- **FAIR alternative (higher rating):** {I_MAX_GF} A with `{ALT['mpn']}` ({mf_name(ALT)}; {ALT['n_dist']} "
     f"distributor, {', '.join(ALT['dists'])}). Self-heated error {ALT['err']:.3f} % (margin {ALT['m_d']:.3f} %). "
     f"Limit: {mf_bind_txt(ALT_BIND)}; at {I_MAX_GF + 1} A nothing passes.") if ALT is not None else
    "- No FAIR design allows a higher rating than the GOOD design.",
    f"- **WSK2512-LOW:** no design passes at any integer I_FS from {I_START} A. Its best self-heated error at "
    f"{I_START} A is {LOW_REF['err']:.3f} % (`{LOW_REF['mpn']}`, criterion (d)); see MF.5.",
    "",
]

MF_MD += ["### MF.1 Criteria (all at the 70 degC worst case; a design passes only if all six hold)", ""]
MF_MD += mdtable(["Criterion", "Check", "Numbers from", "Status"], [
    ["(a) Vsense", "I_FS x R_element(worst) <= VDIFF max of the ADCRANGE (163.84 mV for 0, 40.96 mV for 1)",
     f"`vdiff_adcr0_full` ({cite(VDIFF)}), `vdiff_adcr1_full`; R_element at the self-heated element temperature", "pass/fail"],
    ["(b) Dissipation", "I_FS^2 x R_element(worst) <= p70 x derate at TA 70 degC",
     f"`p70` ({cite(shunt.param('p70'))}); the derating curve (knee and zero) of each series; derate {DERATE:g}",
     "pass/fail"],
    ["(c) Working voltage", "(P x R)^1/2 >= I_FS x R_element (P = rated at 70 degC)",
     f"`max_working_voltage` ({cite(shunt.param('max_working_voltage'))}). Algebraically the same test as (b) at the same R, "
     "counted once", "pass/fail"],
    ["(d) Error", "Self-heated worst-case error <= 1 % of reading at I_FS: element tolerance and TCR at the element band "
     "(TA 0 to 70 degC plus its own rise); INA229 gain (GSERR, GS_DRFT) and offset (both Vos rows) at ambient; the "
     "SHUNT_CAL integer rounding; 1 LSB = I_FS / 2^19",
     f"`tol_*`, `tcr_*` rows (per series); `gserr_25c`, `gs_drift_25c`, `vos_*`, `vos_drift_full` (INA229 DS p.5)",
     "pass/fail; ESTIMATE (self-heating)"],
    ["(e) Element temperature", "T_element (worst) <= derate_zero of the series (the 0 % rating point, read from the "
     "derating curve)", "ASSUMPTION: the element limit is read as derate_zero (as section 3), not t_oper", "ESTIMATE"],
    ["(f) SHUNT_CAL", "1 <= round(13107.2e6 x CURRENT_LSB x R x [4 for ADCRANGE 1]) <= 32767",
     "`shunt_cal` (INA229 DS p.30, Equation 2); register width: DS p.23, Table 7-7 (SHUNT_CAL bits 14-0, bit 15 reserved)",
     "pass/fail; library gap, see MF.7"],
])
MF_MD += [
    "",
    f"Candidate set: every orderable pair of each series (WSK2512: {len(MF['WSK2512']['pairs'])} pairs, codes B, D and F; "
    f"WSK2512-LOW: {len(MF['WSK2512-LOW']['pairs'])} pairs, codes D and F), each at ADCRANGE 0 and 1 "
    f"({len(MF_SCAN[I_START])} records per integer I_FS). The self-heating fixed point is per series "
    f"(theta_est {MF['WSK2512']['theta']:g} degC/W for WSK2512 and {MF['WSK2512-LOW']['theta']:g} degC/W for WSK2512-LOW, "
    "each from its own derating curve). The scan steps I_FS in 1 A integers from "
    f"{I_START} A and stops {CONFIRM_MORE + 1} integers after the first integer with no passing design "
    f"({I_FIRST_EMPTY} A), so {MF_I[-1]} A is the last integer checked.",
    "",
    "**Ranking of passing designs:** availability (GOOD, then FAIR, then POOR/UNKNOWN), then lowest self-heated error "
    "(compared at 0.01 %), then lower dissipation at 70 degC. **Availability rating:** as the existing script "
    "(`avail_rating`): GOOD = recognized by the BOM (MATCH_EXACT), 2 or more distributors, standard value; FAIR = "
    "recognized, 1 distributor; POOR/UNKNOWN = not recognized, or no distributor listing. Stock is informational only. "
    "Lifecycle is not checked.",
    "",
]

MF_MD += ["### MF.2 Series data used (each from its library rows; numbers are generated, not retyped)", ""]
_rows = []
for _name in MF:
    _p = MF[_name]["part"]
    _tol = "; ".join(f"{c} +/-{_p.tolerance(c).max * 100:g} % ({_p.param(o['range']).min * 1e3:g} to "
                     f"{_p.param(o['range']).max * 1e3:g} mohm)" for c, o in _p.info["series_tolerances"].items())
    _tcr = "; ".join(f"+/-{_p.param(o['tcr']).max * 1e6:g} ppm/degC for {_p.param(o['range']).min * 1e3:g} to "
                     f"{_p.param(o['range']).max * 1e3:g} mohm" for o in _p.info["series_tcr"])
    _vals = [v for v, _ in MF[_name]["pairs"]]
    _rows.append([f"`{_name}`", f"{min(_vals) * 1e3:g} to {max(_vals) * 1e3:g} mohm ({len(MF[_name]['pairs'])} pairs)",
                  _tol, _tcr, f"{MF[_name]['p70']:g} W ({cite(_p.param('p70'))})",
                  f"knee {MF[_name]['knee']:g} degC, zero {MF[_name]['zero']:g} degC (curve, `{_name}` DS p.3); "
                  f"theta_est {MF[_name]['theta']:g} degC/W (ESTIMATE)"])
MF_MD += mdtable(["Series", "Values (library value table)", "Tolerance codes (DS p.1 range, before the directory clip)", "TCR (DS p.2)",
                  "Power P70 (DS p.1)", "Derating (DS p.3, curve)"], _rows)
MF_MD += [
    "",
    f"Value tables: `{MF['WSK2512']['part'].param('res_range').cite}` and `{LOW.param('res_range').cite}` (the "
    "directory ranges); the decade values are Vishay doc 30117 (lib/series.py, DECADE). WSK2512-LOW has no code B "
    "(B starts at 0.01 ohm) and its lowest value is 1.0 mohm (library rule eseries_min 0.001 ohm; the 0.5 to 0.99 mohm "
    "TCR row therefore has no values). Its TCR is NOT the WSK2512 TCR: "
    + "; ".join(f"+/-{_t:g} ppm/degC for {_lo:g} to {_hi:g} mohm" for _t, _lo, _hi in LOW_TCR_TXT.values())
    + f", against +/-{MF['WSK2512']['part'].param('tcr_5m_200m').max * 1e6:g} ppm/degC for WSK2512 (5 to 200 mohm).",
    "",
]

# %%
# Result by integer I_FS
MF_MD += ["### MF.3 Result by integer full-scale current (5 A upward, until nothing passes)", ""]
_rows = []
for _i in MF_I:
    _s = MF_SUM[_i]
    _best = _s["ranked"][0] if _s["ranked"] else None
    if _best is not None:
        _bt = (f"{_best['series']} {_best['value'] * 1e3:g} mohm {_best['code']}, ADCRANGE {_best['adc']}, "
               f"`{_best['mpn']}`, {_best['avail']}")
        _vals = [f"{_best['err']:.3f}", f"{_best['t_el']:.1f}", f"{_best['p_el']:.3f}", f"{_best['vs'] * 1e3:.2f}"]
        _bind = "-"
    else:
        _bt = "none"
        _vals = ["-", "-", "-", "-"]
        _c = _s["closest"]
        _bind = (f"closest: {mf_name(_c)}, fails {mf_fails_txt(_c)}; first: {mf_bind_txt([_s['binding']])}")
    _rows.append([_i, f"{_s['n_pass']} / {_s['n_gf']} / {_s['n_good']}", _bt] + _vals + [_bind])
MF_MD += mdtable(["I_FS (A)", "Passing designs: any / GOOD+FAIR / GOOD", "Best passing design (rank 1)",
                  "Self-heated error at I_FS (%, ESTIMATE)", "Element T at 70 degC (degC, ESTIMATE)",
                  "Dissipation at 70 degC (W, rated 1.0 W)", "Vsense (mV)", "Binding (closest design if nothing passes)"],
                 _rows)
MF_MD += [
    "",
    f"Maximum I_FS: primary (GOOD or FAIR) **{I_MAX_GF} A**; GOOD only **{I_MAX_GOOD} A**; any availability "
    f"**{I_MAX_ANY} A**. First integer with nothing passing: **{I_FIRST_EMPTY} A** (scan continued to {MF_I[-1]} A, "
    "nothing passes above it).",
    "",
    "![Best self-heated error vs I_FS](design-analysis/maxfs_error.png)",
    "",
    "Figure M1. Best passing design's self-heated error at each integer I_FS (blue: any availability; green: GOOD only), "
    "against the 1 % requirement. Where nothing passes (orange, hollow), the closest design is shown with the criterion "
    "that fails first.",
    "",
]
_ADC1_PASS = {i: sum(1 for r in MF_SCAN[i] if r["pass"] and r["adc"] == 1) for i in MF_I}
_ADC1_GOOD = sum(1 for i in MF_I for r in MF_SUM[i]["ranked"] if r["avail"] == "GOOD" and r["adc"] == 1)
MF_MD += [
    "ADCRANGE 1 (40.96 mV) passing designs by I_FS: "
    + ", ".join(f"{i} A: {n}" for i, n in _ADC1_PASS.items() if n)
    + f". GOOD-availability ADCRANGE 1 designs among them: {_ADC1_GOOD}. The ranking takes availability first, so an "
    "ADCRANGE 1 design ranks only where it is FAIR or better than the ADCRANGE 0 choice.",
    "",
]

# %%
# Limits of the top candidates: last passing integer, binding criterion and the margins at that integer.
MF_MD += ["### MF.4 Limits of the candidates (where each design stops passing, and what stops it)", ""]
_rows = []
for _r in MF_TOP + [LOW_REF]:
    _lim, _bk = mf_limit(_r)
    _at = MF_SCAN[_lim][[mf_key(x) for x in MF_SCAN[_lim]].index(mf_key(_r))] if _lim is not None else None
    if _lim is not None:
        _lim_txt = f"{_lim} A"
    elif _bk:
        _lim_txt = f"none (fails at {I_START} A)"
    else:
        _lim_txt = "beyond scan"
    _rows.append([mf_name(_r), _r["avail"] if _r["avail"] else "-", _lim_txt,
                  mf_bind_txt(_bk) if _bk else "-",
                  (f"{_at['err']:.3f} %, T {_at['t_el']:.1f} degC, P {_at['p_el']:.3f} W" if _at else "-")])
MF_MD += mdtable(["Design", "Availability (at its limit)", "Last integer passing", "Binding criterion (first to fail)",
                  "At the last passing integer"], _rows)
MF_MD += [
    "",
    "The limit is the last integer before the first criterion fails (the scan is ascending; each design's first failure is "
    "recorded). Designs listed here are the rank-1 design at some integer, the recommended design, the FAIR alternative and "
    "the best WSK2512-LOW design at I_FS = 5 A (reference; it does not pass at any integer).",
    "",
    "![Shunt dissipation vs current](design-analysis/maxfs_dissipation.png)",
    "",
    "Figure M2. Shunt dissipation at 70 degC (element resistance, ESTIMATE) vs I_FS for the top candidates (solid) and the "
    "WSK2512-LOW reference (dashed), against the rating at 70 degC (p70 x derate). The dotted line marks the GOOD maximum.",
    "",
    "![Shunt element temperature vs current](design-analysis/maxfs_temperature.png)",
    "",
    "Figure M3. Element temperature at TA 70 degC vs I_FS (ESTIMATE, each series' own theta) for the same candidates, against "
    "the ASSUMED element limit (derate_zero, 170 degC).",
    "",
]

# %%
# Recommendation (section MF.5 in the report)
MF_MD += ["### MF.5 RECOMMENDATION (for the designer to approve)", ""]
MF_MD += [
    f"**Recommended integer full-scale current: {I_MAX_GOOD} A**, with `{REC['mpn']}` (WSK2512, "
    f"{REC['value'] * 1e3:g} mohm, tolerance code {REC['code']}, ADCRANGE {REC['adc']}, GOOD availability: "
    f"{REC['n_dist']} distributors: {', '.join(REC['dists'])}). Stock today {REC['stock'] if REC['stock'] is not None else 'n/a'} "
    "(informational). The design is judged at 70 degC, self-heated worst case, derate 1.0.",
    "",
]
_rec_rows = [
    ["Shunt (library MPN; BOM MPN agrees)", f"`{REC['mpn']}`, {REC['value'] * 1e3:g} mohm, code {REC['code']} "
     f"(+/-{MF[REC['series']]['part'].tolerance(REC['code']).max * 100:g} %)", "-", "-",
     f"{REC['avail']} (proxy; lifecycle open item)"],
    ["Vsense worst at I_FS, self-heated", f"{REC['vs'] * 1e3:.2f} mV", f"{REC['vdiff'] * 1e3:.2f} mV (ADCRANGE {REC['adc']})",
     f"{REC['m_a'] * 1e3:.2f} mV ({REC['headroom'] * 100:.1f} % of range)", "PASS (a)"],
    ["Shunt dissipation at 70 degC (element R, worst)", f"{REC['p_el']:.3f} W", f"{REC['rated']:.3g} W (p70 x derate)",
     f"{REC['m_b']:.3f} W ({REC['m_b'] / REC['rated'] * 100:.1f} % of rating)", "PASS (b)"],
    ["(P x R)^1/2 vs Vsense (working voltage)", f"Vsense {REC['vs'] * 1e3:.2f} mV", f"(P x R)^1/2 {REC['mwv'] * 1e3:.2f} mV",
     f"{REC['m_c'] * 1e3:.2f} mV", "PASS (c)"],
    ["Element temperature at 70 degC (ESTIMATE, self-consistent)", f"{REC['t_el']:.2f} degC (band low {REC_D['t_lo']:.1f} "
     "degC at TA 0)", f"{REC['zero']:g} degC (ASSUMED element limit = derate_zero)", f"{REC['m_e']:.2f} degC",
     "ESTIMATE, PASS only under the assumed limit"],
    ["Self-heated worst-case error at I_FS (judged; ESTIMATE)", f"{REC['err']:.4f} % of reading (2-sample interior "
     f"check {REC_D['err_s2']:.4f} %)", f"{ERR_LIMIT_PCT:g} % of reading", f"{REC['m_d']:.4f} % of reading",
     "PASS (d), ESTIMATE"],
    ["Self-heated error at 1 % of I_FS (REPORT ONLY, no limit)", f"{REC_D['err_lo']:.3f} % of reading at "
     f"{0.01 * I_MAX_GOOD:.2f} A" + (" (above 1 %: at this low end the offset, 1 uV at 25 degC plus 10 nV/degC drift, "
     "is a large fraction of the 1 % of I_FS drop; no requirement is set at the low end)" if REC_D["err_lo"] > ERR_LIMIT_PCT else ""),
     "none set", "-", "REPORT ONLY"],
    ["Ambient-only error at I_FS (reference)", f"{REC_D['err_amb']:.4f} %", "reference", "-", "REFERENCE"],
    ["CURRENT_LSB = I_FS / 2^19", f"{REC['lsb']:.6g} A/LSB ({REC['lsb'] * 1e6:.4g} uA)", "-", "-",
     "DS p.30 Equation 3"],
    ["SHUNT_CAL (ADCRANGE " + str(REC["adc"]) + ")", f"raw {REC['raw']:.4f}, register {REC['cal']}"
     + (" (on a .5 tie: both integers judged, round half up written)" if REC["tie"] else ""),
     f"{SHUNT_CAL_MAX} (15-bit field, DS p.23)", f"{REC['m_f']} counts", "PASS (f)"],
    ["Bus-voltage error (design independent, section 7, incl. 1 LSB)", f"+/-{mv(BUS_12)} mV at 12 V, +/-{mv(BUS_40)} mV at 40 V",
     "none set", "-", "REPORT ONLY"],
    ["Power error at I_FS, 12 V (ambient, REPORT ONLY)", f"{pct(REC_D['pw12'][0])} to +{pct(REC_D['pw12'][1])} %",
     "none set", "-", "REPORT ONLY"],
    ["Power error at I_FS, 40 V (ambient, REPORT ONLY)", f"{pct(REC_D['pw40'][0])} to +{pct(REC_D['pw40'][1])} %",
     "none set", "-", "REPORT ONLY"],
    ["Continuous current at 70 degC, rating basis (ambient R, calc.max_current)", f"{REC_D['i_amb']:.2f} A",
     f"I_FS {I_MAX_GOOD} A", f"{REC_D['i_amb'] - I_MAX_GOOD:+.2f} A", "rating basis"],
    ["Continuous current at 70 degC, self-heated (element R, bisection)", f"{REC_D['i_sh']:.2f} A",
     f"I_FS {I_MAX_GOOD} A", f"{REC_D['i_sh'] - I_MAX_GOOD:+.2f} A", "ESTIMATE"],
]
MF_MD += mdtable(["Item", "Value (worst case)", "Limit", "Margin", "Status"], _rec_rows)
_REC_NEXT = MF_SCAN[REC_LIMIT + 1][[mf_key(x) for x in MF_SCAN[REC_LIMIT + 1]].index(mf_key(REC))]
MF_MD += [
    "",
    f"**Limits of this design and what fails above it.** The design passes up to {REC_LIMIT} A. At {REC_LIMIT + 1} A its "
    f"failing criteria are {mf_fails_txt(_REC_NEXT)} (first to fail: {mf_bind_txt(REC_BIND)}); its self-heated error is "
    f"{_REC_NEXT['err']:.3f} % of reading, its dissipation {_REC_NEXT['p_el']:.3f} W against {RATED_LINE:.3g} W, and its "
    f"element temperature {_REC_NEXT['t_el']:.1f} degC against the {_REC_NEXT['zero']:g} degC assumed limit.",
    "",
]
if ALT is not None:
    MF_MD += [
        f"**FAIR alternative (only if the availability risk is accepted):** `{ALT['mpn']}` ({ALT['value'] * 1e3:g} mohm, "
        f"code {ALT['code']}, ADCRANGE {ALT['adc']}), {I_MAX_GF} A. Self-heated error {ALT['err']:.4f} % (margin "
        f"{ALT['m_d']:.4f} %), element {ALT['t_el']:.1f} degC (ESTIMATE), dissipation {ALT['p_el']:.3f} W "
        f"(margin {ALT['m_b']:.3f} W), Vsense {ALT['vs'] * 1e3:.2f} mV (headroom {ALT['headroom'] * 100:.1f} %), "
        f"SHUNT_CAL {ALT['cal']}. Distributor: {', '.join(ALT['dists'])}, stock today "
        f"{ALT['stock'] if ALT['stock'] is not None else 'n/a'} (informational). Limit: {mf_bind_txt(ALT_BIND)}.",
        "",
    ]
MF_MD += [
    f"**Why WSK2512-LOW does not reach {I_START} A.** Without self-heating (ambient 0 to 70 degC only) its best design, "
    f"`{LOW_REF['mpn']}`, would be {LOW_AMB[mf_key(LOW_REF)]:.3f} % at {I_START} A; self-heating (element "
    f"{LOW_REF['t_el']:.1f} degC at 70 degC) raises it to {LOW_REF['err']:.3f} %. The 1.0 to 2.9 mohm values carry "
    f"+/-{LOW_TCR_TXT[LOW.info['series_tcr'][1]['tcr']][0]:g} ppm/degC and their best ambient-only error is already "
    f"{LOW_AMB_BELOW3:.3f} % at {I_START} A; the 3.0 to 4.9 mohm values (+/-{LOW_TCR_TXT[LOW.info['series_tcr'][2]['tcr']][0]:g} "
    f"ppm/degC) reach {LOW_AMB_ABOVE3:.3f} % ambient-only. The main WSK2512 (+/-{MF['WSK2512']['part'].param('tcr_5m_200m').max * 1e6:g} "
    "ppm/degC) carries a much smaller TCR term.",
    "",
]

# %%
MF_MD += ["### MF.6 What limits the maximum", ""]
_B10 = [r for r in MF_SCAN[I_MAX_GOOD] if r["series"] == "WSK2512" and r["code"] == "B" and abs(r["value"] - 0.01) < 1e-12
        and r["adc"] == 0][0]
_B10_LIM, _B10_BIND = mf_limit(_B10)
_B10_AT = [r for r in MF_SCAN[_B10_LIM] if mf_key(r) == mf_key(_B10)][0]  # the code B record at its own limit
_D10_AT_B = [r for r in MF_SCAN[_B10_LIM] if r["series"] == "WSK2512" and r["code"] == "D" and abs(r["value"] - 0.01) < 1e-12
             and r["adc"] == 0][0]
MF_MD += [
    f"- **GOOD design (`{REC['mpn']}`):** the first criterion to fail above {REC_LIMIT} A is {mf_bind_txt(REC_BIND)} "
    f"(self-heated error {REC['err']:.3f} % at {REC_LIMIT} A, {_REC_NEXT['err']:.3f} % at {REC_LIMIT + 1} A).",
    f"- **Nothing passes at {I_FIRST_EMPTY} A.** The closest design there is {mf_name(CLOSEST_NEXT)}, failing "
    f"{mf_fails_txt(CLOSEST_NEXT)} (self-heated error {CLOSEST_NEXT['err']:.3f} %, element {CLOSEST_NEXT['t_el']:.1f} degC).",
    f"- **Code B (+/-0.1 %), 10 mohm, ADCRANGE 0** (the current design's value and tolerance, FAIR availability) passes up "
    f"to {_B10_LIM} A; its first criterion to fail is {mf_bind_txt(_B10_BIND)}. At {_B10_LIM} A its self-heated error is "
    f"{_B10_AT['err']:.3f} % against {_D10_AT_B['err']:.3f} % for code D at the same current, and its dissipation "
    f"({_B10_AT['p_el']:.3f} W) and element temperature ({_B10_AT['t_el']:.1f} degC) are the limits.",
    "",
]

# %%
MF_MD += ["### MF.7 Library gaps, data read off the datasheet, and cites", ""]
MF_MD += [
    "- **SHUNT_CAL register width (INA229), library gap:** `calc.shunt_cal` returns the value and the library has no "
    "register-width row. Read from the datasheet page image (INA229 DS p.23, Table 7-7): SHUNT_CAL is bits 14-0 (R/W, "
    "reset 1000h), bit 15 is reserved. A 15-bit field, so the largest value is 32767 (7FFFh). Used as criterion (f) "
    "through the constant `SHUNT_CAL_MAX`. The library owner should add the register width as a row.",
    "- **WSK2512-LOW data:** the LOW part has its own library directory (v0.2.0 is the pinned package tag; v0.3.0 identical). "
    "Its datasheet is the same Vishay document as WSK2512 (DS 30108, 11-Dec-2023), so every LOW number cites DS 30108: "
    "tolerance codes D and F (DS p.1, Standard Electrical Specifications), P70 1.0 W (DS p.1), TCR by range (DS p.2, "
    "Technical Specifications; TCR, tolerance and P70 rows checked against the datasheet text, pages 1 and 2), derating "
    "(DS p.3, DERATING graph; 100 % to 70 degC, 0 % at 170 degC, read off the curve; the page image checked), and the decade "
    "value table (Vishay doc 30117 "
    "(WSL), not referenced by DS 30108: library open item).",
    "- **Derating curve (both series):** the knee (70 degC) and zero (170 degC) are curve readings, so theta_est "
    f"({MF['WSK2512']['theta']:g} degC/W) and every element temperature depend on them. Both series print the same "
    "curve, but each series' theta is computed from its own rows.",
    "- **TCR reference temperature:** not stated by either WSK2512 datasheet; 25 degC is assumed (existing open item). The "
    "TCR is applied as (1 + TCR x (T - 25 degC)) and the tolerance as a fixed +/- offset.",
    "",
]

MF_MD += ["### MF.8 Current path (open item, one line)", ""]
MF_MD += [
    f"- **Open item:** the block's BUS_IN/BUS_OUT copper, pads and connectors must be rated for the chosen I_FS "
    f"({I_MAX_GOOD} A recommended, {I_MAX_GF} A FAIR alternative); layout not started.",
    "",
]

MF_MD += ["### MF.9 Method and limits of this study", ""]
MF_MD += [
    "- Every criterion is a worst-case bound at 70 degC, except (d) and (e), which are ESTIMATES (self-heating from the "
    "derating curve). The INA229 terms (gain, offset, LSB) are at the ambient 0 to 70 degC.",
    "- (c) is the same test as (b) at the same R (exact identity), so it is counted once in the fail count.",
    "- SHUNT_CAL rounding: the written register value is round-half-up. A raw value on an exact .5 tie can be written either "
    "way, so the judged error takes the worse of the two integers (the rounding term is a worst case, not a sign).",
    "- The self-heated error is multilinear in its two temperatures (element and ambient), so corners are exact. The "
    "recommended design is also checked with two interior points per input (2-sample check above).",
    "- 'First failing' criterion per design: the scan is ascending, so the first integer where each criterion fails is "
    "recorded; a design whose criteria are not monotone would show its first failure only.",
    "- The availability snapshot (`analysis/data/shunt_stock_2026-11-15_maxfs.json`) covers every passing design; the "
    "WSK2512 rows are reused from `shunt_stock_2026-11-15_rerank.json` and the WSK2512-LOW rows are new (80 pairs, "
    "pcb bom -f json in a scratch copy with the LOW dependency 0.2.0). Lifecycle, PCN and lead time are not checked. "
    "Stock is a snapshot.",
    "- Power error (MF.5) is ambient only, as section 8 (report only, no limit).",
    "",
]

# %%
# Library data observed while building this report (for the report and the library owner)
LIBRARY_OBSERVATIONS = [
    "`vih_25c` (INA229): MIN 1.2 V and MAX = VS, stored as an offset of 0 from pin VS (max_ref = VS). `part.resolve()` gives "
    "the absolute bound. Consistent with DS p.6; not used in this report.",
    "`shunt_tempco_fs` (INA229): max 0.01638 1/degC (16383 ppm/degC), which matches the SHUNT_TEMPCO register full scale, "
    "not a physical tempco. Unused here; check the row.",
    "`voh_25c` (INA229): stored as min -400 mV (offset below VS), the R_out bound uses it as a drop at 1 mA. The row's "
    "condition prints IOL = 1 mA (DS p.6, as printed).",
    "`zvbus_25c` (INA229): 25 degC only; no temperature drift row, so the 800 kohm minimum is applied at all temperatures.",
    "`rtheta_ja` (INA229): typical only, no max; used only for a typical-based estimate.",
    "`vos_bus_psrr_25c` (INA229): typical only (0 with a +/-0.25 mV/V spread); shown, not bounded.",
    "WSK2512 `derate_knee` and `derate_zero`: typ-only curve values (curve = true). The theta_est estimate depends on them.",
    "WSK2512 `t_oper`: -65 to 170 degC, read as AMBIENT (DS p.2). Not used as the element limit (section 3.3).",
]

# %% [markdown]
# ## Assemble and write the Markdown

# %%
HEADER = [
    "# INA229 + WSK2512 shunt block: design analysis",
    "",
    "> GENERATED by `python3 -m analysis.ina229_block` from `analysis/ina229_block.py`. Do not edit this file; "
    "change the script and re-run.",
    "",
    f"- Library: zener-library {ZL_VERSION} (`analysis/_zl.py`, component data and calc functions).",
    f"- Run date: {RUN_DATE}.",
    f"- Inputs: `analysis/data/shunt_stock_2026-11-15_rerank.json` (fit set at 5 A, a superset of the I_MAX fit set) and "
    f"`analysis/data/shunt_stock_2026-11-15_maxfs.json` (maximum full-scale study, WSK2512 and WSK2512-LOW). Stock "
    "snapshots are sourcing records, not datasheet values.",
    "",
    f"High-side current sense: {I_MAX:g} A bidirectional full scale, bus {VBUS_LO:g} to {VBUS_HI:g} V, "
    f"TA {TA.min:g} to {TA.max:g} degC, VS {VS_NOM:g} V +/-{VS_TOL * 100:g} %. Current design: ADCRANGE {ADCRANGE}, "
    f"{R_NOM * 1e3:g} mohm code {code}.",
    "",
    "## Requirement and design choices (not datasheet values)",
    "",
    f"- Accuracy requirement: +/-{ERR_LIMIT_PCT:g} % of reading at {I_MAX:g} A full scale (designer requirement 2026-11-15), "
    "judged on the self-heated worst case (ESTIMATE). The 100 mA error is REPORT ONLY.",
    f"- I_MAX = {I_MAX:g} A, full scale, bidirectional. Bus {VBUS_LO:g} to {VBUS_HI:g} V, high-side.",
    f"- TA {TA.min:g} to {TA.max:g} degC; nominal (typ) reference 25 degC.",
    f"- VS = {VS_NOM:g} V +/-{VS_TOL * 100:g} %.",
    f"- Design shunt `SHUNT_SELECTED` = (ADCRANGE {SHUNT_SELECTED[0]}, code {SHUNT_SELECTED[1]}, {SHUNT_SELECTED[2]:g} mohm). "
    "The designer's selection basis and the fallback are in section 1.4.",
    f"- Derate = {DERATE:g}. Shunt TCR reference temperature {T_REF:g} degC (the WSK2512 DS states none).",
    f"- Element temperature limit {T_ELEM_LIMIT:g} degC is an ASSUMPTION (`T_ELEM_LIMIT`), not t_oper.",
    f"- Ambient full-scale error uses {SAMPLES} interior samples per input (quadratic in TA); the self-heated band is multilinear "
    "in its two temperatures, so corners are exact (checked in section 5).",
    f"- Shunt candidates: every WSK2512 (value, code) fitting ADCRANGE 0 with self-heated Vsense ({CAND_N} pairs; "
    f"{CAND_N_ADC1} also fit ADCRANGE 1). Availability snapshot {STOCK_DATE}, a `pcb bom` sourcing record, not a guarantee.",
    f"- MISO series resistor {R_MISO:g} ohm (placeholder), ALERT_N pull-up {R_PULLUP / 1e3:g} kohm (spec.md).",
    "",
    "Conventions: PASS / FAIL against a datasheet, spec.md or the designer's requirement; ESTIMATE for curve-based or "
    "typical-based results; REPORT ONLY where no limit is set; REFERENCE for comparison only; OPEN ITEM for zero or "
    "placeholder margin; CHECK for a value with no spec limit.",
    "",
]

BODY_ORDER_NOTE = [
    "## Contents",
    "",
    "Summary; maximum full-scale current study and RECOMMENDATION (MF.0 to MF.9); 1 shunt selection and re-rank (1.4 selected design and basis, 1.5 ADCRANGE rows, 1.6 SHUNT_TEMPCO); 2 shunt power; "
    "3 shunt self-heating (estimate); 4 firmware constants; 5 full-scale current error; 6 error vs current; "
    "7 bus-voltage error; 8 power measurement error; 9 INA229 junction temperature (estimate); 10 quiescent current and "
    "power budget; 11 ratings; 12 MISO; 13 limits; 14 judgement calls.",
    "",
]

LIB_BLOCK = ["## Library observations (for the library owner)", ""] + [f"- {x}" for x in LIBRARY_OBSERVATIONS] + [""]

FINAL = HEADER + BODY_ORDER_NOTE + md_summary + MF_MD + md + LIB_BLOCK
DOCS.mkdir(parents=True, exist_ok=True)
MD_PATH.write_text("\n".join(FINAL) + "\n")

# %% [markdown]
# ### Chart: self-heated I_MAX error vs shunt value, recognized candidates (section 1)

# %%
fig, ax = plt.subplots(figsize=(8.8, 5.2))
for adc, mk, col, off in ((0, "o", BLUE, (6, -12)), (1, "D", GREEN, (6, 6))):
    pts = [r for r in RECOGNIZED_ROWS if r["adc"] == adc]
    if not pts:
        continue
    xs_ = [r["value"] * 1e3 for r in pts]
    ys_ = [r["fs5_pct"] for r in pts]
    ax.scatter(xs_, ys_, marker=mk, s=40, color=col, zorder=5,
               label=f"ADCRANGE {adc} (recognized, MATCH_EXACT)" + (" (ADCRANGE 1 is 40.96 mV full scale)" if adc == 1 else ""))
    for r, x_, y_ in zip(pts, xs_, ys_):
        if r["code"] == "F":  # F (+/-1 %) cannot pass 1 %: unlabelled to keep the D and B points readable
            continue
        ax.annotate(f"{r['code']} {r['value'] * 1e3:g}", xy=(x_, y_),
                    xytext=off, textcoords="offset points", fontsize=7.5, color=INK)
ax.axhline(ERR_LIMIT_PCT, color=INK, lw=1.0, ls="--", label=f"requirement: {ERR_LIMIT_PCT:g} % of reading (self-heated)")
ax.axvline(ADC_LIM[1] / I_MAX * 1e3, color=GREEN, lw=0.8, ls=":")
ax.text(ADC_LIM[1] / I_MAX * 1e3 * 1.02, 0.05, f"ADCRANGE 1 limit at {I_MAX:g} A", color=GREEN, fontsize=7.5, rotation=90,
        va="bottom")
ax.set_xscale("log")
style_axes(ax, "WSK2512 value (mohm), log scale", f"{I_MAX:g} A error, self-heated worst case (% of reading, ESTIMATE)")
ax.set_title(f"Stocked WSK2512 candidates: self-heated {I_MAX:g} A error vs value (ADCRANGE 0 and 1)", color=INK, fontsize=10.5)
ax.legend(frameon=False, loc="upper left", fontsize=8, labelcolor=INK)
ax.set_ylim(0, max(max(r["fs5_pct"] for r in RECOGNIZED_ROWS) * 1.12, ERR_LIMIT_PCT * 1.3) + 0.6)
fig.tight_layout()
save(fig, "shunt_candidates.png")

# %% [markdown]
# ## Console summary

# %%
print(f"Design: {R_NOM * 1e3:g} mohm, code {code}, ADCRANGE {ADCRANGE}, MPN {MPN}, availability snapshot {STOCK_DATE}: {DESIGN['avail']}, {DESIGN['n_dist']} distributor(s)")
print(f"Vsense worst (self-heated) {mv(VS_SH)} mV, margin {mv(VS_MARGIN)} mV ({pct(VS_MARGIN / ADC_LIM[ADCRANGE])} % of limit)")
print(f"Power worst {PW.max:.4g} W, rated {RATED:.4g} W, margin {PW_MARGIN:.4g} W ({pct(PW_MARGIN / RATED)} %)")
print(f"theta_est {THETA:g} degC/W; element at {I_MAX:g} A, 70 degC {T_HOT_5:.4g} degC (self-consistent, residual {T_RESID_5:.1e} degC); "
      f"margin to assumed {T_ELEM_LIMIT:g} degC {T_MARGIN_5:.4g} degC (ESTIMATE)")
print(f"CURRENT_LSB {CURRENT_LSB:.10g} A/LSB, SHUNT_CAL raw {SHUNT_CAL_RAW:.6f} -> {SHUNT_CAL}")
print(f"FS error at {I_MAX:g} A, self-heated (judged vs {ERR_LIMIT_PCT:g} %): max {FS_SH_PCT:.3f} % -> {err_pf(FS_SH_PCT)}; "
      f"ambient reference max {FS_AMB_PCT:.3f} %")
print(f"Error at {I_LOW:g} A (report only): self-heated max {FS_SH_LO01_PCT:.3f} %, ambient max {FS_AMB_LO01_PCT:.3f} %")
print(f"Power error at {I_MAX:g} A: 12 V {pct(PW_5[12.0][0])} to +{pct(PW_5[12.0][1])} %, "
      f"40 V {pct(PW_5[40.0][0])} to +{pct(PW_5[40.0][1])} %")
print(f"INA229 Tj at TA {TA.max:g}: V3V3 only {TJ_V3:.4g} degC, all power {TJ_ALL:.4g} degC (ESTIMATE, typ rtheta_ja)")
print(f"Quiescent V3V3 worst {mw(TOT_V3_WORST)} mW; typ 25 degC {mw(TOT_V3_ACTIVE_TYP)} mW")
print(f"Candidates: {CAND_N} (ADCRANGE 0), {CAND_N_ADC1} also fit ADCRANGE 1; recognized (MATCH_EXACT) rows "
      f"{len(RECOGNIZED_ROWS)}, ranked (1 %, recognized, ADC/rating checks) {len(RANKED)}")
for n, r in enumerate(TOP3, start=1):
    print(f"Top {n}: {r['value'] * 1e3:g} mohm {r['code']} ADCRANGE {r['adc']} {r['mpn']} self-heated {r['fs5_pct']:.3f} % "
          f"availability {r['avail']} ({r['n_dist']} distributor(s)), stock today {sk(r)} (informational)")
print(f"Selected: {DESIGN['mpn']} (designer choice); fallback: {FALLBACK['mpn'] if FALLBACK else '-'}; "
      f"rank 1 by availability: {REC['mpn'] if REC else '-'}; vsense_max {VSENSE_MAX_MV:.1f} mV")
print("Bus error table (mV):")
for r in bus_rows:
    print("  ", r)
print(f"Max full-scale study: primary (GOOD or FAIR) {I_MAX_GF} A, GOOD only {I_MAX_GOOD} A, any availability {I_MAX_ANY} A; "
      f"first empty integer {I_FIRST_EMPTY} A (scan to {MF_I[-1]} A)")
for _i in MF_I:
    _s = MF_SUM[_i]
    _b = _s["ranked"][0] if _s["ranked"] else None
    print(f"  I_FS {_i:>2} A: passing any/GOOD+FAIR/GOOD {_s['n_pass']}/{_s['n_gf']}/{_s['n_good']}; best "
          + (f"{_b['series']} {_b['value'] * 1e3:g} mohm {_b['code']} ADC{_b['adc']} {_b['mpn']} ({_b['avail']}), "
             f"error {_b['err']:.3f} %, T {_b['t_el']:.1f} degC, P {_b['p_el']:.3f} W" if _b else "none")
          + ("" if _b else f"; closest fails {mf_fails_txt(_s['closest'])}"))
print(f"RECOMMENDATION (GOOD): {I_MAX_GOOD} A {REC['mpn']} ADC{REC['adc']}, error {REC['err']:.4f} % (limit 1 %), "
      f"P {REC['p_el']:.4f} W, T {REC['t_el']:.2f} degC, SHUNT_CAL {REC['cal']}, binding {mf_bind_txt(REC_BIND)}")
print(f"FAIR alternative: {I_MAX_GF} A {ALT['mpn']} ADC{ALT['adc']}, error {ALT['err']:.4f} %" if ALT is not None else "no FAIR alternative")
print(f"WSK2512-LOW best at {I_START} A: {LOW_REF['mpn']} error {LOW_REF['err']:.3f} % (ambient-only {LOW_AMB[mf_key(LOW_REF)]:.3f} %); "
      f"passing LOW designs over the scan: {sum(1 for i in MF_I for r in MF_SCAN[i] if r['series'] == 'WSK2512-LOW' and r['pass'])}")
print(f"Wrote {MD_PATH} and charts in {FIG_DIR}")
