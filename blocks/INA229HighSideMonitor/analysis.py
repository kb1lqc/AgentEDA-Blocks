# %% [markdown]
# # INA229HighSideMonitor block: design-analysis generator
#
# This file is the SOURCE of `design-analysis.md`, the PNG charts in `design-analysis/` and `design-analysis/values.json`
# in the output folder. Those outputs are generated: never edit them by hand. One run analyses one instance:
#
#     python3 blocks/INA229HighSideMonitor/analysis.py --params <file.json> --out <dir> [--instance NAME]
#
# Defaults (resolved from the repo root, so the cwd does not matter): --params benches/INA229HighSideMonitor/params.json,
# --out benches/INA229HighSideMonitor/docs, --instance INA (the bench reference instance). Exit 0 = everything regenerated.
# A board runs the same script with its own params file and writes its own output folder per instance.
#
# It is a jupytext "percent" file (`# %%` code cells, `# %% [markdown]` text cells), so it opens as a notebook in
# VS Code / Jupyter and stays a plain runnable module. In a notebook the kernel's own arguments are ignored and the
# defaults are used (parse_known_args).
#
# Scope: one instance as set by its params file. The bench reference instance is 6 A bidirectional full scale,
# WSK2512R0100DEA (10 mohm, code D, +/-0.5 %), ADCRANGE 0, bus 12-40 V (high side), TA 0-70 degC, VS 3.3 V +/-3 %.
# Accuracy requirement: +/-1 % of reading at 6 A (ERR_LIMIT_PCT), judged on the SELF-HEATED worst case. The report
# analyses the instance it is given and nothing else.
# Every datasheet number comes from the component library (`part.param`, `part.inputs`, calc functions). Design choices
# are the constants in the DESIGN CHOICES cell. Worst case = `lib.wca` over every min/max corner. Where a function is not
# multilinear in its inputs (the ambient current error is quadratic in TA), wca also samples interior points.
#
# Thermal results (shunt self-heating, INA229 junction temperature) use a curve-based estimate of the shunt thermal
# resistance and typical-only datasheet values; every such result is labelled ESTIMATE in the report.

# %%
import argparse
import datetime
import itertools
import json
import math
import random
import sys
from pathlib import Path


def find_repo_root():
    """The repo root: the nearest folder above this file (above the cwd in a notebook, which has no __file__) that holds
    analysis/_zl.py and blocks/. Found this way, the script works from any cwd."""
    try:
        start = Path(__file__).resolve().parent
    except NameError:
        start = Path.cwd().resolve()
    for folder in [start, *start.parents]:
        if (folder / "analysis" / "_zl.py").is_file() and (folder / "blocks").is_dir():
            return folder
    raise SystemExit("repo root not found: no analysis/_zl.py and blocks/ above this file or the cwd")


REPO = find_repo_root()
sys.path.insert(0, str(REPO))  # first, so `analysis` is the repo package and not this script's own folder

from analysis import _zl  # noqa: F401  (side effect: puts zener-library on sys.path)
from analysis._zl import ZL_VERSION
from lib import Param, inputs_section, load, unverified_inputs, wca
from analysis.md_pdf import write_report
from analysis._export import write_library_inputs

import matplotlib.pyplot as plt  # imported after lib, which selects the Agg backend
from matplotlib.lines import Line2D

parser = argparse.ArgumentParser(description="INA229HighSideMonitor worst-case analysis for one instance.")
parser.add_argument("--params", help="instance params JSON (default: benches/INA229HighSideMonitor/params.json)")
parser.add_argument("--out", help="output folder for design-analysis.md and design-analysis/ "
                                  "(default: benches/INA229HighSideMonitor/docs)")
parser.add_argument("--instance", default="INA", help="instance name written to the report and values.json (default: INA)")
ARGS, _ = parser.parse_known_args()  # known args only: a notebook's own kernel arguments are ignored

PARAMS_PATH = Path(ARGS.params).resolve() if ARGS.params else REPO / "benches" / "INA229HighSideMonitor" / "params.json"
OUT = Path(ARGS.out).resolve() if ARGS.out else REPO / "benches" / "INA229HighSideMonitor" / "docs"
INSTANCE = ARGS.instance


def rel_to_repo(path):
    """Path text for the report: relative to the repo root when inside it, else absolute."""
    try:
        return str(Path(path).relative_to(REPO))
    except ValueError:
        return str(path)


PARAMS_REL = rel_to_repo(PARAMS_PATH)
DOCS = OUT  # output folder: design-analysis.md and design-analysis/ (charts and values.json) go here
FIG_DIR = DOCS / "design-analysis"  # generated charts (PNG) and values.json
MD_PATH = DOCS / "design-analysis.md"  # generated report
RUN_DATE = datetime.date.today().isoformat()

# The instance's design inputs. Every key is required; a missing or unknown key stops the run, so a typo can never fall
# back to a default. "<key>_basis" entries carry the reason for each value and are not read.
PARAM_KEYS = {
    "i_fs_a", "i_low_a", "vbus_lo_v", "vbus_hi_v", "vbus_test_v", "ta_min_c", "ta_typ_c", "ta_max_c", "t_ref_c",
    "vs_nom_v", "vs_tol_frac", "derate", "r_miso_ohm", "r_alert_pullup_ohm", "err_limit_pct", "cal_ref_pct", "cal_ta_c",
    "t_elem_limit_c", "adcrange", "rshunt_tol_code", "rshunt_ohm", "vbus_mid_v",
}
OPTIONAL_KEYS = {"cal_i_a"}  # null or omitted: the calibration point defaults to i_fs_a (see below)


def load_params(path):
    raw = json.loads(Path(path).read_text())
    keys = {k for k in raw if not k.endswith("_basis")}
    missing, extra = (PARAM_KEYS - OPTIONAL_KEYS) - keys, keys - (PARAM_KEYS | OPTIONAL_KEYS)
    if missing or extra:
        raise SystemExit(f"{path}: missing keys {sorted(missing)}, unknown keys {sorted(extra)}")
    return raw


P = load_params(PARAMS_PATH)
if P.get("cal_i_a") is None:
    CAL_I_SRC = "not set in params (cal_i_a null or omitted): defaults to i_fs_a"
    P["cal_i_a"] = P["i_fs_a"]
else:
    CAL_I_SRC = "set in params (cal_i_a)"

# %% [markdown]
# ## Design choices
# Every constant here is a DESIGN CHOICE (not a datasheet value), with the reason beside it.

# %%
# Instance design inputs, read from the params file P (see PARAM_KEYS above). The reason for each value is the
# "<key>_basis" entry in that file. Derived values (supply limits) are computed here from the inputs.
I_MAX = P["i_fs_a"]  # A, full-scale current, bidirectional
I_LOW = P["i_low_a"]  # A, low-current point for the error tables
VBUS_LO, VBUS_HI = P["vbus_lo_v"], P["vbus_hi_v"]  # V, high-side bus range
VBUS_TEST = list(P["vbus_test_v"])  # V, bus voltages for the bus-error table
TA = Param(min=P["ta_min_c"], typ=P["ta_typ_c"], max=P["ta_max_c"], unit="degC")  # ambient range; typ = 25 degC reference
T_REF = P["t_ref_c"]  # degC, reference temperature for the shunt TCR
VS_NOM = P["vs_nom_v"]  # V, INA229 supply
VS_TOL = P["vs_tol_frac"]  # fraction, supply tolerance
VS_MAX = VS_NOM * (1 + VS_TOL)  # V, worst supply for supply-side budgets
VS_MIN = VS_NOM * (1 - VS_TOL)  # V, lowest supply (rating checks use it for the VIO high-level limit)
DERATE = P["derate"]  # shunt rating derate
SAMPLES = 2  # interior points per input for the ambient full-scale error (checked against samples = 0 in section 5)
N_PLOT = 40  # log-spaced current points for the error chart
I_PLOT_LO = 0.1  # A, error chart starts here
R_MISO = P["r_miso_ohm"]  # ohm, MISO series resistor (open item, section 12)
R_PULLUP = P["r_alert_pullup_ohm"]  # ohm, ALERT_N pull-up; tolerance and TCR come from the library
ERR_LIMIT_PCT = P["err_limit_pct"]  # % of reading at I_MAX, the accuracy requirement
CAL_REF_PCT = P["cal_ref_pct"]  # % of reading, one-point calibration reference accuracy
CAL_TA = P["cal_ta_c"]  # degC, ambient at the calibration
CAL_I = P["cal_i_a"]  # A, one-point gain calibration point
T_ELEM_LIMIT = P["t_elem_limit_c"]  # degC, assumed shunt element limit (not t_oper, which is an ambient rating)
N_PLOT_PWR = 30  # log-spaced current points for the power-error chart
T_SAMPLES_PWR = 2  # interior ambient points for the joint power error (checked against 9 in section 8)
ADCRANGE = P["adcrange"]  # INA229 CONFIG ADCRANGE of the instance: 0 = +/-163.84 mV, 1 = +/-40.96 mV
code = P["rshunt_tol_code"]  # WSK2512 tolerance code of the shunt
R_NOM = P["rshunt_ohm"]  # ohm, nominal value of the shunt

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
ADC_LIM = {0: VDIFF.max}  # V, full scale of the design's ADCRANGE (0 = 163.84 mV)
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




# %% [markdown]
# ## Design shunt (from R_NOM, code and ADCRANGE)
# Every later section uses these names: the WSK2512 tolerance and TCR rows, the self-heated worst-case Vsense and the
# SHUNT_CAL gain of the design.

# %%
TOL = shunt.tolerance(code)  # Param: fraction, min and max of the code's tolerance row
TCR = shunt.tcr(R_NOM)  # Param: TCR row (1/degC)
TOL_W = TOL.with_typ(0.0)  # bound-only row: nominal part at R_NOM (caller assumption, as in lib examples)
TCR_W = TCR.with_typ(0.0)  # bound-only row: nominal TCR 0
MPN = shunt.mpn(R_NOM, tol=code)  # orderable part number of the design
SHUNT_CAL_RAW_DESIGN = ina.calc.shunt_cal(CURRENT_LSB, R_NOM, adcrange=ADCRANGE)  # DS p.30 Equation 2
CAL_GAIN = round(SHUNT_CAL_RAW_DESIGN) / SHUNT_CAL_RAW_DESIGN  # register integer / exact value (1.0 when exact)
# ESTIMATE: self-heated worst-case Vsense at I_MAX. The element rise is solved at TA max (rise(), section 3), and R is
# the worst tolerance and TCR at the element temperature.
X_SH, _ = rise(I_MAX, R_NOM, TOL_W, TCR_W, TA.max, "hi")
VS_SH = I_MAX * r_bound(R_NOM, TOL, TCR, TA.max + X_SH, "hi")  # V
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
# ## 1. Shunt operating point (self-heated Vsense vs ADC range)
# Vsense = I_MAX x R_element. R_element is the worst tolerance and TCR at the self-heated element temperature (TA 70 degC
# plus the self-consistent rise, section 3). The limit is the INA229 shunt-voltage full scale for the design's ADCRANGE
# (`vdiff_adcr0_full`, DS p.5).

# %%
VSENSE_MAX_MV = math.ceil(round(VS_SH * 1e4, 6)) / 10  # mV: self-heated worst Vsense rounded UP to 0.1 mV (vsense_max)
VS_AMB = I_MAX * R_WORST  # V, reference: ambient resistance at TA 70 degC

md += ["## 1. Shunt operating point", ""]
md += [
    f"Sense limit, ADCRANGE {ADCRANGE}: `vdiff_adcr0_full` max = {mv(ADC_LIM[ADCRANGE])} mV ({cite(VDIFF)}). "
    f"The shunt element runs at the self-heated temperature of section 3 (theta_est {THETA:g} degC/W, ESTIMATE); R_element "
    "is the worst tolerance and TCR at that temperature.",
    "",
]
md += mdtable(["Check", "Vsense at I_MAX (mV)", "Limit (mV)", "Headroom (mV)", "Headroom (% of limit)", "Result"], [
    [f"Self-heated worst case, ESTIMATE (judged)", mv(VS_SH), mv(ADC_LIM[ADCRANGE]), mv(VS_MARGIN),
     pct(VS_MARGIN / ADC_LIM[ADCRANGE]), status(VS_MARGIN)],
    [f"Ambient resistance at TA {TA.max:g} degC (reference)", mv(VS_AMB), mv(ADC_LIM[ADCRANGE]), mv(ADC_LIM[ADCRANGE] - VS_AMB),
     pct((ADC_LIM[ADCRANGE] - VS_AMB) / ADC_LIM[ADCRANGE]), status(ADC_LIM[ADCRANGE] - VS_AMB)],
])
md += [
    "",
    f"Instance configs (`{INSTANCE}`, bench: `benches/INA229HighSideMonitor/bench.zen`): `rshunt=\"{R_NOM * 1e3:g}mohm\"`, `rshunt_tol=\"{code}\"`, "
    f"`vsense_max=\"{VSENSE_MAX_MV:.1f}mV\"`. vsense_max is the self-heated worst Vsense {mv(VS_SH)} mV rounded up to "
    "0.1 mV.",
    "",
]

# %% [markdown]
# ## 2. Shunt power and rating (ambient, the DS rating basis)

# %%
md += ["## 2. Shunt power and rating", ""]
md += [
    f"Worst-case dissipation at I_MAX = {I_MAX:g} A and R_worst (TA {TA.max:g} degC, ambient resistance; the worst "
    f"corner): **{PW.max:.4g} W**. Rating: `p70` max = {P70:g} W at 70 degC ambient (DS p.1, {cite(shunt.param('p70'))}), "
    f"`rated_power` at TA = {TA.max:g} degC (derating knee `derate_knee` = {KNEE:g} degC and zero `derate_zero` = "
    f"{ZERO:g} degC are read off the DS p.3 graph, curve) x derate {DERATE:g} = {RATED:.4g} W.",
    "",
]
md += mdtable(["Check", "Worst-case (W)", "Rated (W)", "Margin (W)", "Margin (% of rating)", "Result", "Cite"], [[
    f"Shunt dissipation at {I_MAX:g} A, {TA.max:g} degC (ambient rating)", f"{PW.max:.4g}", f"{RATED:.4g}", f"{PW_MARGIN:.4g}",
    pct(PW_MARGIN / RATED), status(PW_MARGIN), cite(shunt.param("p70")),
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
    f"Figure 1. Derating curve with the {I_MAX:g} A, {TA.max:g} degC operating point (dissipation at worst R, from section 2).",
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
for i_pt in [I_LOW] + [float(k) for k in range(1, int(I_MAX) + 1)]:
    t70 = t_shunt(i_pt, TA.max)
    t0 = t_shunt(i_pt, TA.min)
    t_rows.append([
        f"{i_pt:g}", f"{p_at(i_pt, TA.min):.4g}", f"{p_at(i_pt, TA.max):.4g}",
        f"{t0 - TA.min:.4g}", f"{t70 - TA.max:.4g}", f"{t0:.4g}", f"{t70:.4g}", f"{T_ELEM_LIMIT - t70:.4g}",
    ])
md += mdtable(["Current (A)", f"Worst P at TA {TA.min:g} (W)", f"Worst P at TA {TA.max:g} (W)", f"Rise at TA {TA.min:g} (degC)",
               f"Rise at TA {TA.max:g} (degC)", f"T_shunt at TA {TA.min:g} (estimate, degC)", f"T_shunt at TA {TA.max:g} (estimate, degC)",
               f"Margin to {T_ELEM_LIMIT:g} degC at TA {TA.max:g} (degC, assumed limit)"], t_rows)
md += [
    "",
    "Reproduce a row: T_shunt = TA + rise, rise = theta_est x I^2 x R_worst(T_shunt); the 'Worst P' columns are at "
    "ambient resistance (rating basis) and are below the element-resistance power that sets the rise.",
    "",
    f"Limits: the element limit used here is **{T_ELEM_LIMIT:g} degC, an ASSUMPTION** (the derating curve's zero point, "
    f"`derate_zero`, read as the element limit). It is NOT `t_oper`: the library records `t_oper` (max {T_OPER_MAX:g} degC, "
    f"DS p.2) as an AMBIENT operating range, so it is not cited as the element limit. At {I_MAX:g} A and TA {TA.max:g} degC: "
    f"T_shunt = {T_HOT_5:.4g} degC (ESTIMATE), margin {T_MARGIN_5:.4g} degC: **{elem_status(T_MARGIN_5)}**. The "
    "estimate depends on theta_est; the copper, the layout and the true element limit are open items.",
    "",
    "![Shunt temperature vs current](design-analysis/shunt_temperature.png)",
    "",
    f"Figure 2. Estimated shunt element temperature vs current at TA {TA.min:g} and {TA.max:g} degC (self-consistent, worst-case R), against "
    f"the assumed {T_ELEM_LIMIT:g} degC element limit.",
    "",
]

md += ["### 3.4 Dissipation vs current and the derated limit at each ambient", ""]
d_rows = []
for ta in (TA.min, TA.typ, 50.0, TA.max):
    p5 = p_at(I_MAX, ta)
    d_rows.append([f"{ta:g}", f"{rated_at(ta):.4g}", f"{p5:.4g}", f"{rated_at(ta) - p5:.4g}",
                   pct((rated_at(ta) - p5) / rated_at(ta)), status(rated_at(ta) - p5)])
md += mdtable(["TA (degC)", "Derated limit (W)", "Worst dissipation at full scale (W)", "Margin (W)", "Margin (%)", "Result"],
              d_rows)
md += [
    "",
    f"The limit is flat at p70 x derate below the knee ({KNEE:g} degC), so the {TA.max:g} degC row is the tightest. Dissipation is "
    "I^2 x R_worst (ambient resistance, rating against ambient, as the DS states).",
    "",
    "![Shunt dissipation vs current](design-analysis/shunt_dissipation.png)",
    "",
    f"Figure 3. Worst-case dissipation vs current at TA {TA.min:g}, {TA.typ:g}, 50 and {TA.max:g} degC, with the derated limit at each ambient. The "
    f"four dissipation curves overlap: the shunt TCR changes R by at most {max(abs(TCR.min), abs(TCR.max)) * (TA.max - TA.min) * 100:.2f} % across {TA.min:g}-{TA.max:g} degC, far below the scale of the "
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
        label=f"derated rating (DS p.3 curve; above {TA.max:g} degC a curve-based estimate)")
ax.axvline(KNEE, color=MUTED, lw=0.8, ls=":")
ax.axvline(ZERO, color=MUTED, lw=0.8, ls=":")
ax.text(KNEE + 2, P70 * 0.04, f"knee {KNEE:g} degC", color=MUTED, fontsize=8)
ax.text(ZERO - 2, P70 * 0.04, f"0 W at {ZERO:g} degC", color=MUTED, fontsize=8, ha="right")
ax.scatter([TA.max], [rated_at(TA.max)], color=INK, s=22, zorder=5,
           label=f"rating at {TA.max:g} degC: {rated_at(TA.max):.3g} W")
ax.scatter([TA.max], [p_at(I_MAX, TA.max)], color=ORANGE, s=34, zorder=6,
           label=f"operating point: {I_MAX:g} A dissipation at {TA.max:g} degC, {p_at(I_MAX, TA.max):.3g} W")
style_axes(ax, "Ambient temperature (degC)", "Rated power (W)")
ax.set_xlim(0, ZERO + 5)
ax.set_ylim(0, P70 * 1.2)
ax.set_title(f"WSK2512 rated power vs ambient (derate {DERATE:g})", color=INK, fontsize=10.5)
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
ax.annotate(f"{I_MAX:g} A, {TA.max:g} degC: {T_HOT_5:.1f} degC", xy=(I_MAX, T_HOT_5), xytext=(-10, 10),
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
for j, ta in enumerate((TA.min, TA.typ, 50.0, TA.max)):
    col = BLUE_RAMP[j]
    ax.plot(I_GRID, [p_at(i, ta) for i in I_GRID], color=col, lw=1.4, label=f"dissipation, TA {ta:g} degC")
    ax.axhline(rated_at(ta), color=col, lw=1.0, ls="--")
ax.text(0.1, rated_at(TA.max) * 1.03,
        f"derated limits at TA {TA.min:g}, {TA.typ:g}, 50, {TA.max:g} degC: all {rated_at(TA.max):.2f} W (dashed, flat below the {KNEE:g} degC knee)",
        color=INK, fontsize=8)
ax.scatter([I_MAX], [p_at(I_MAX, TA.max)], color=ORANGE, s=34, zorder=6)
ax.annotate(f"{I_MAX:g} A, {TA.max:g} degC: {p_at(I_MAX, TA.max):.3f} W, margin {rated_at(TA.max) - p_at(I_MAX, TA.max):.3f} W",
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
CAL_ERR = SHUNT_CAL / SHUNT_CAL_RAW - 1.0  # fraction, reading gain error from the integer register
CAL_EXACT = abs(SHUNT_CAL_RAW - SHUNT_CAL) < 1e-9

md += ["## 4. Firmware constants", ""]
md += mdtable(["Constant", "Value", "Equation / cite"], [
    ["CURRENT_LSB", f"{CURRENT_LSB:.10g} A/LSB ({CURRENT_LSB * 1e6:.6g} uA)",
     f"I_MAX / 2^19, DS p.30 Equation 3 (`calc.current_lsb`); same for both ADCRANGE"],
    ["SHUNT_CAL (raw)", f"{SHUNT_CAL_RAW:.6f}", f"13107.2e6 x CURRENT_LSB x R_NOM, DS p.30 Equation 2 (`calc.shunt_cal`, "
                                                f"ADCRANGE {ADCRANGE})"],
    ["SHUNT_CAL (register)", f"**{SHUNT_CAL}**", "rounded integer written to the register"],
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
# Two references: the ambient-only result (INA229 and shunt both at TA), and the self-heated ESTIMATE (shunt at its element
# temperature, datasheet tolerance only, before the solder-heat shift). The judged BOL result is section 5b.

# %%
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

ZERO_KW = dict(r_nom=R_NOM, r_tol=0.0, r_tcr=0.0, t_c=25.0, gserr=0.0, gs_drift=0.0, vos25=0.0, vos_drift=0.0,
               cal_gain=1.0)
term_specs = [
    ("Shunt tolerance (code " + code + ")", dict(r_tol=TOL_W)),
    (f"Shunt TCR (TA {TA.min:g}-{TA.max:g} degC)", dict(r_tcr=TCR_W, t_c=TA)),
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

md += [f"## 5. Full-scale current error at {I_MAX:g} A (TA {TA.min:g} to {TA.max:g} degC)", ""]
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
    f"**Vos row.** The larger-magnitude of the two DS rows (`vos_vcm48_25c`, `vos_vcm0_25c`, +/-1 uV max at 25 degC) "
    f"is used: `{VOS_KEY_AMB}`. Neither row is at the {VBUS_LO:g}-{VBUS_HI:g} V bus voltage; the offset is specified at VCM 48 V and 0 V "
    "only (open item).",
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
    f"**Self-heated ESTIMATE, pre-solder (reference, not judged).** The shunt element runs from TA {TA.min:g} degC plus its lowest-R rise to TA {TA.max:g} "
    f"degC plus its worst-R rise (self-consistent, section 3). At {I_MAX:g} A the element reaches {T_HOT_5:.4g} degC at TA {TA.max:g} degC "
    f"(rise {T_RISE_5:.4g} degC), so the shunt TCR is evaluated up to {T_HOT_5:.4g} degC. The INA229 drift stays at the "
    f"ambient {TA.min:g} to {TA.max:g} degC.",
    "",
]
md += mdtable([f"Case ({I_MAX:g} A)", "Min error (A)", "Max error (A)", "Min (% of reading)", "Max (% of reading)",
               "Max magnitude (% of reading)", f"{ERR_LIMIT_PCT:g} % check"], [
    [f"Ambient only (TA {TA.min:g}-{TA.max:g} degC), reference", f"{FS_MIN:.4g}", f"{FS_MAX:.4g}", pct(FS_MIN / I_MAX),
     pct(FS_MAX / I_MAX), f"{FS_AMB_PCT:.4g}", "REFERENCE, not judged"],
    ["With self-heating, pre-solder (ESTIMATE), reference", f"{FS_SH_MIN:.4g}", f"{FS_SH_MAX:.4g}", pct(FS_SH_MIN / I_MAX),
     pct(FS_SH_MAX / I_MAX), f"{FS_SH_PCT:.4g}", "REFERENCE, not judged (BOL: section 5b)"],
])
md += [
    "",
    f"Self-heating check: corners only give min {FS_SH_CORNERS.min:.6g} A, max {FS_SH_CORNERS.max:.6g} A; with 2 interior "
    f"samples min {FS_SH_CHK.min:.6g} A, max {FS_SH_CHK.max:.6g} A. The function is MULTILINEAR in the shunt and the "
    "ambient temperature (separate inputs), so corners are exact, and the interior check agrees.",
    "",
    f"Result: the self-heated worst case at {I_MAX:g} A, before the solder-heat shift, is **{FS_SH_PCT:.3f} %** of reading "
    f"(ambient reference {FS_AMB_PCT:.3f} %). This is a reference only; the judged BOL result (uncalibrated and calibrated, "
    "with the solder-heat shift and the one-point calibration) is section 5b. The shunt TCR and the self-heating rise are the "
    "terms that move from the ambient to the self-heated case.",
    "",
]

# %% [markdown]
# ## 5b. BOL accuracy, uncalibrated and calibrated (judged)
# Beginning of life, self-heated (element band, section 3), TA 0 to 70 degC. UNCALIBRATED: the shunt initial tolerance and
# the solder-heat shift are both worst-case bounds. CALIBRATED: one-point gain calibration at CAL_I (6 A) and CAL_TA
# (25 degC) with a reference of +/-CAL_REF_PCT. The calibrated case is the judged one.

# %%
SOLDER = shunt.param("perf_solder_heat")  # DS p.3 PERFORMANCE, resistance to solder heat, +/-0.5 % of R (percent)
OFFSET_P = shunt.param("perf_offset_ohm")  # DS p.3 PERFORMANCE, +0.0005 ohm on every row
SOLDER_FRAC = max(abs(SOLDER.min), abs(SOLDER.max))  # fraction of R: the library stores the printed % as a fraction (0.005)
S_MAX = SOLDER_FRAC * R_NOM + OFFSET_P.max  # ohm, test limit +/-(0.5 % of R + 0.0005 ohm)
S_ABS = Param(min=-S_MAX, max=S_MAX, unit="ohm")  # solder-heat shift of R, absolute
SOLDER_CITE = f"{cite(SOLDER)}; {cite(OFFSET_P)}"
VOS_P = ina.param(VOS_KEY_AMB)  # DS p.5 offset row (the one used in section 5)
ZERO_P = Param(min=0.0, max=0.0, unit="1")
CAL_REF = Param(min=-CAL_REF_PCT / 100, max=CAL_REF_PCT / 100, unit="1")  # calibration reference, fraction
Q_CAL = Param(min=-LSB, max=LSB, unit="A")  # 1 LSB quantisation of the reading taken at the cal point
OFF_FS_PCT = max(abs(VOS_P.min), abs(VOS_P.max)) / (R_NOM * I_MAX) * 100  # % of reading at I_MAX: offset alone

# %%
def t_sh_band(i):
    """ESTIMATE: operating element temperature band (degC) at current i over TA 0 to 70 degC. The solder-heat shift moves
    the rise (rise scales with R): the low end uses R - S_MAX at TA min, the high end R + S_MAX at TA max."""
    return Param(min=TA.min + rise(i, R_NOM - S_MAX, TOL_W, TCR_W, TA.min, "lo")[0],
                 max=TA.max + rise(i, R_NOM + S_MAX, TOL_W, TCR_W, TA.max, "hi")[0], unit="degC")


T_CAL = Param(min=CAL_TA + rise(CAL_I, R_NOM - S_MAX, TOL_W, TCR_W, CAL_TA, "lo")[0],
              max=CAL_TA + rise(CAL_I, R_NOM + S_MAX, TOL_W, TCR_W, CAL_TA, "hi")[0], unit="degC")  # cal element band
T_CAL_NOM = CAL_TA + rise(CAL_I, R_NOM, ZERO_P, ZERO_P, CAL_TA, "hi")[0]  # nominal cal element temperature (base value)


def meas_i(i_true, tau, alpha, s_abs, t_el, t_amb, gserr, gs_drift, vos25, vos_drift, cal_gain=1.0):
    """Not a DS equation: INA229 current reading (A) before any calibration, for true current i_true. The shunt is
    resistance_worst(R_NOM, tau, alpha, t_el) + s_abs (initial tolerance, TCR at element temperature t_el, solder-heat
    shift). The INA229 gain and offset are calc.current_error at ambient t_amb (DS p.5 terms). cal_gain: SHUNT_CAL scale.
    Returns: A (positive = reads high)."""
    r_act = shunt.calc.resistance_worst(R_NOM, tau, alpha, t_el, T_REF) + s_abs
    i_seen = i_true * r_act / R_NOM * cal_gain
    return i_seen + ina.calc.current_error(i_seen, R_NOM, vos25, vos_drift, gserr, gs_drift, t_amb)


def fs_uncal_error(i_true, tau, alpha, s_abs, t_sh, t_amb, gserr, gs_drift, vos25, vos_drift, cal_gain):
    """Not a DS equation: uncalibrated BOL error (A), measured minus true. No calibration, so every term of meas_i stays.
    Returns: A (positive = reads high)."""
    return meas_i(i_true, tau, alpha, s_abs, t_sh, t_amb, gserr, gs_drift, vos25, vos_drift, cal_gain) - i_true


def fs_cal_error(i_true, tau, alpha, s_abs, t_cal, t_sh, t_amb, gserr, gs_drift, vos25, vos_drift, e_ref, q_cal):
    """Not a DS equation: calibrated BOL error (A) after ONE-POINT GAIN calibration at CAL_I and CAL_TA.

    At calibration the unit reads m_cal = meas_i(CAL_I; element t_cal, ambient CAL_TA) + q_cal (q_cal: 1 LSB quantisation
    of that reading). The reference is CAL_I x (1 + e_ref). One gain k = CAL_I (1 + e_ref) / m_cal is written, so the
    operating reading is k x meas_i(i_true; element t_sh, ambient t_amb) and the error is that minus i_true.

    What the gain absorbs, exactly: a term that scales the whole reading by the same factor at the cal point is removed.
    The shunt tolerance, the solder-heat shift, GSERR and the SHUNT_CAL rounding are such terms (they cancel in k x m / i,
    residual 0). What remains: e_ref; the shunt TCR between the operating element temperature and the cal element
    temperature (the cal element sits at CAL_TA plus its own rise at CAL_I); the INA229 gain drift relative to CAL_TA
    (GS_DRFT x (TA - CAL_TA)); the offset drift; and the offset away from the cal point. The offset is NOT a gain: Vos/R
    adds a fixed current, so its share of the reading is Vos/(i R) at i and Vos/(CAL_I R) at the cal point. The gain
    fixes only the share at CAL_I, leaving Vos/(i R) - Vos/(CAL_I R) at other currents. Its drift is the offset drift term.
    The 1 LSB of the operating reading is added outside this function (section 5b totals).
    Returns: A (positive = reads high)."""
    m_cal = meas_i(CAL_I, tau, alpha, s_abs, t_cal, CAL_TA, gserr, gs_drift, vos25, vos_drift) + q_cal
    k = CAL_I * (1.0 + e_ref) / m_cal
    return k * meas_i(i_true, tau, alpha, s_abs, t_sh, t_amb, gserr, gs_drift, vos25, vos_drift) - i_true


BASE_UNCAL = dict(tau=0.0, alpha=0.0, s_abs=0.0, t_sh=T_REF, t_amb=T_REF, gserr=0.0, gs_drift=0.0, vos25=0.0,
                  vos_drift=0.0, cal_gain=CAL_GAIN)  # every input fixed at its nominal (zero spread)
BASE_CAL = dict(tau=0.0, alpha=0.0, s_abs=0.0, t_cal=T_CAL_NOM, t_sh=T_CAL_NOM, t_amb=T_REF, gserr=0.0, gs_drift=0.0,
                vos25=0.0, vos_drift=0.0, e_ref=0.0, q_cal=0.0)


def uncal_active(i):
    """Every uncalibrated input as a Param (the judged total)."""
    return dict(tau=TOL_W, alpha=TCR_W, s_abs=S_ABS, t_sh=t_sh_band(i), t_amb=TA, gserr=GSERR, gs_drift=GS_DRIFT,
                vos25=VOS_P, vos_drift=VOS_DRIFT)


def cal_active(i):
    """Every calibrated input as a Param (the judged total). The unit's own tolerance, solder shift and GSERR are included:
    the corner analysis shows they cancel, rather than assuming it."""
    return dict(tau=TOL_W, alpha=TCR_W, s_abs=S_ABS, t_cal=T_CAL, t_sh=t_sh_band(i), t_amb=TA, gserr=GSERR,
                gs_drift=GS_DRIFT, vos25=VOS_P, vos_drift=VOS_DRIFT, e_ref=CAL_REF, q_cal=Q_CAL)


def wca_case(fn, i, base, active):
    """wca of fn at current i: floats from base, Params from active (corners of every Param)."""
    return wca(fn, unit="A", samples=0, i_true=i, **{**base, **active})


def total_uncal(i):
    return wca_case(fs_uncal_error, i, BASE_UNCAL, uncal_active(i))


def total_cal(i):
    return wca_case(fs_cal_error, i, BASE_CAL, cal_active(i))


def judged(w, i):
    """Judged worst case in % of reading: the corner result plus 1 LSB of the operating reading. Returns (min, max, mag)."""
    lo = (w.min - LSB) / i * 100
    hi = (w.max + LSB) / i * 100
    return lo, hi, max(abs(lo), abs(hi))


# Contributor specs: (short label, name, source/cite, active Params for this term alone). Others stay at base.
def uncal_specs(i):
    return [
        ("Shunt tolerance", f"Shunt initial tolerance (code {code})", cite(TOL), dict(tau=TOL_W)),
        ("Solder-heat shift", "Resistance shift at solder heat (qualification test limit)", SOLDER_CITE,
         dict(s_abs=S_ABS)),
        ("Shunt TCR, element T", "Shunt TCR over the self-heated element temperature", cite(TCR),
         dict(alpha=TCR_W, t_sh=t_sh_band(i))),
        ("INA229 gain error", "INA229 gain error GSERR (25 degC)", cite(GSERR), dict(gserr=GSERR)),
        ("INA229 gain drift", f"INA229 gain drift over TA {TA.min:g}-{TA.max:g} degC (vs 25 degC)", cite(GS_DRIFT),
         dict(gs_drift=GS_DRIFT, t_amb=TA)),
        ("INA229 offset", "INA229 offset Vos (larger DS row)", cite(VOS_P), dict(vos25=VOS_P)),
        ("INA229 offset drift", f"INA229 offset drift over TA {TA.min:g}-{TA.max:g} degC (vs 25 degC), referred to the shunt", cite(VOS_DRIFT),
         dict(vos_drift=VOS_DRIFT, t_amb=TA)),
        ("SHUNT_CAL rounding", f"SHUNT_CAL integer rounding (register {SHUNT_CAL})", "`calc.shunt_cal`, DS p.30 Eq. 2",
         dict()),
    ]


def cal_specs(i):
    return [
        ("Cal reference", "One-point calibration reference accuracy", f"design input `CAL_REF_PCT` = +/-{CAL_REF_PCT:g} %",
         dict(e_ref=CAL_REF)),
        ("Cal reading quantisation", "1 LSB quantisation of the reading taken at the cal point",
         "`current_lsb`, DS p.30 Eq. 3", dict(q_cal=Q_CAL)),
        ("Shunt TCR vs cal element T", "Shunt TCR: operating element temperature vs the calibration element temperature",
         f"{cite(TCR)}; element bands from `rise()` (ESTIMATE)", dict(alpha=TCR_W, t_sh=t_sh_band(i), t_cal=T_CAL)),
        ("INA229 gain drift", "INA229 gain drift relative to 25 degC", cite(GS_DRIFT), dict(gs_drift=GS_DRIFT, t_amb=TA)),
        ("INA229 offset drift", "INA229 offset drift relative to 25 degC, referred to the shunt", cite(VOS_DRIFT),
         dict(vos_drift=VOS_DRIFT, t_amb=TA)),
        ("Offset away from cal", "INA229 offset: not a gain, so its share differs away from the cal point", cite(VOS_P),
         dict(vos25=VOS_P)),
        ("Shunt tolerance (removed)", "Shunt initial tolerance (removed by the gain)", cite(TOL), dict(tau=TOL_W)),
        ("Solder-heat shift (removed)", "Solder-heat shift (removed by the gain)", SOLDER_CITE, dict(s_abs=S_ABS)),
        ("GSERR (removed)", "INA229 gain error GSERR at 25 degC (removed by the gain)", cite(GSERR), dict(gserr=GSERR)),
    ]


LBL = {
    "tau": ("tol -", "tol +"), "alpha": ("TCR -", "TCR +"), "s_abs": ("solder -", "solder +"),
    "gserr": ("GSERR -", "GSERR +"), "gs_drift": ("GS drift -", "GS drift +"), "vos25": ("Vos -", "Vos +"),
    "vos_drift": ("dVos -", "dVos +"), "t_sh": ("element cold", "element hot"), "t_cal": ("cal element cold", "cal element hot"),
    "t_amb": (f"TA {TA.min:g}", f"TA {TA.max:g}"), "e_ref": ("ref -", "ref +"), "q_cal": ("cal quant -", "cal quant +"),
}


def sign_text(fn, i, base, active):
    """Worst-case sign handling of one term: which corner gives its max and min (computed by enumerating its corners)."""
    names = [n for n, p in active.items() if p.max > p.min]
    if not names:
        return "none (no spread)"
    pts = []
    for sides in itertools.product((0, 1), repeat=len(names)):
        kw = {**base, **{n: active[n].min for n in active}}
        kw.update({n: (active[n].max if s else active[n].min) for n, s in zip(names, sides)})
        pts.append((fn(i_true=i, **kw), sides))
    hi_v, hi_s = max(pts, key=lambda p: p[0])
    lo_v, lo_s = min(pts, key=lambda p: p[0])
    eps = 1e-12  # A
    if abs(hi_v) < eps and abs(lo_v) < eps:
        return "none (cancels)"
    if lo_v > -eps:
        return "one-sided, + only"
    if hi_v < eps:
        return "one-sided, - only"
    lab = lambda s: ", ".join(LBL[n][b] for n, b in zip(names, s))
    return f"both signs: max at {lab(hi_s)}; min at {lab(lo_s)}"


def contrib_rows(fn, i, base, specs):
    """One row per contributor (its own corners only), plus 1 LSB of the operating reading. Sorted largest first."""
    rows = []
    for short, name, src, active in specs:
        w = wca_case(fn, i, base, active)
        lo, hi = w.min / i * 100, w.max / i * 100
        lo, hi = (0.0 if abs(x) < 1e-9 else x for x in (lo, hi))  # floating-point noise from cancelling terms
        rows.append(dict(short=short, name=name, src=src, lo=lo, hi=hi, mag=max(abs(lo), abs(hi)),
                         sign=sign_text(fn, i, base, active)))
    rows.append(dict(short="1 LSB (operating)", name="1 LSB of the operating reading (added outside the corner analysis)",
                     src="`current_lsb`, DS p.30 Eq. 3", lo=-LSB / i * 100, hi=LSB / i * 100, mag=LSB / i * 100,
                     sign="both signs, fixed +/-1 LSB"))
    return sorted(rows, key=lambda r: -r["mag"])


def stack_md(title, rows, w, i):
    """Markdown: contributor table (largest first) and the totals block."""
    lo, hi, jm = judged(w, i)
    lin = sum(r["mag"] for r in rows)
    rss = math.sqrt(sum(r["mag"] ** 2 for r in rows))
    out = [f"**{title}**", ""]
    out += mdtable(["#", "Contributor", "Source / cite", "Min (% of reading)", "Max (% of reading)",
                    "Magnitude (% of reading)", "Worst-case sign handling"],
                   [[k + 1, r["name"], r["src"], f"{r['lo']:.4g}", f"{r['hi']:.4g}", f"{r['mag']:.4g}", r["sign"]]
                    for k, r in enumerate(rows)])
    out += [""]
    out += mdtable(["Total", "% of reading", "Note"], [
        ["Linear sum, worst case (sum of magnitudes)", f"{lin:.4g}",
         "a bound, but it adds every contributor's worst side, which cannot all occur together"],
        ["Judged worst case (corner analysis of all inputs, + 1 LSB)", f"{lo:.4g} to +{hi:.4g} (magnitude {jm:.4g})",
         "THE judged number; cross products included"],
        ["Linear sum minus judged magnitude", f"{lin - jm:+.4g}",
         "positive: linear sum is more conservative; negative: cross products add beyond it"],
        ["RSS, information only (NOT a bound)", f"{rss:.4g}", "root-sum-square of magnitudes; assumes independent contributors"],
    ])
    return out


def mc_extremes(fn, i, base, active, n=20000, seed=7):
    """Sampling check: uniform random draws of every Param inside its min/max. Returns (min, max) of the error (A)."""
    rng = random.Random(seed)
    vals = []
    for _ in range(n):
        kw = {**base, **{k: rng.uniform(p.min, p.max) for k, p in active.items()}}
        vals.append(fn(i_true=i, **kw))
    return min(vals), max(vals)


# %%
UN_FS, UN_LO = total_uncal(I_MAX), total_uncal(I_LOW)
CA_FS, CA_LO = total_cal(I_MAX), total_cal(I_LOW)
FS_UN_LO, FS_UN_HI, FS_UNCAL_PCT = judged(UN_FS, I_MAX)
FS_CAL_LO, FS_CAL_HI, FS_CAL_PCT = judged(CA_FS, I_MAX)
LO_UN_LO, LO_UN_HI, LO_UNCAL_PCT = judged(UN_LO, I_LOW)
LO_CAL_LO, LO_CAL_HI, LO_CAL_PCT = judged(CA_LO, I_LOW)
R_UN_FS = contrib_rows(fs_uncal_error, I_MAX, BASE_UNCAL, uncal_specs(I_MAX))
R_CA_FS = contrib_rows(fs_cal_error, I_MAX, BASE_CAL, cal_specs(I_MAX))
R_UN_LO = contrib_rows(fs_uncal_error, I_LOW, BASE_UNCAL, uncal_specs(I_LOW))
R_CA_LO = contrib_rows(fs_cal_error, I_LOW, BASE_CAL, cal_specs(I_LOW))
MC_UN = mc_extremes(fs_uncal_error, I_MAX, BASE_UNCAL, uncal_active(I_MAX))
MC_CA = mc_extremes(fs_cal_error, I_MAX, BASE_CAL, cal_active(I_MAX))
MC_UN_OK = MC_UN[1] <= UN_FS.max + 1e-15 and MC_UN[0] >= UN_FS.min - 1e-15
MC_CA_OK = MC_CA[1] <= CA_FS.max + 1e-15 and MC_CA[0] >= CA_FS.min - 1e-15
TOP_CA_FS = [r["short"] for r in R_CA_FS[:3]]
TOP_UN_FS = [r["short"] for r in R_UN_FS[:3]]
FS_CAL_MARGIN = ERR_LIMIT_PCT - FS_CAL_PCT

md += ["## 5b. BOL accuracy, uncalibrated and calibrated (judged)", ""]
md += [
    f"Beginning of life (BOL), self-heated, TA {TA.min:g} to {TA.max:g} degC, at {I_MAX:g} A and {I_LOW * 1e3:g} mA. Every contributor is a library datasheet "
    "row (cited) or a design input; the judged total is the worst case over the corners of all inputs together.",
    "",
    "**Uncalibrated.** The shunt initial tolerance and the solder-heat shift are worst-case bounds. The solder-heat shift is "
    f"the qualification TEST LIMIT +/-(0.5 % of R + 0.0005 ohm) (`perf_solder_heat`, `perf_offset_ohm`): for {R_NOM * 1e3:g} mohm the "
    f"0.0005 ohm term alone is 5 %, so the limit is +/-{S_MAX / R_NOM * 100:.2f} % of R (+/-{S_MAX * 1e3:.3g} mohm). It is a "
    "bound, not a typical value: typical shifts are usually much smaller but are not specified, so they are not used. The "
    "element temperature band includes the shift.",
    "",
    f"**Calibrated (judged).** One-point gain calibration at {CAL_I:g} A and {CAL_TA:g} degC ambient (ASSUMPTION: "
    f"`CAL_TA`), after assembly, against a reference of +/-{CAL_REF_PCT:g} % of reading (design input). The gain removes the "
    "shunt tolerance, the solder-heat shift, GSERR and the SHUNT_CAL rounding (each scales the whole reading the same way at "
    "the cal point; their residual is zero, shown as 'removed' rows). What remains is the reference, the shunt TCR between "
    "the operating and the cal element temperature, the INA229 gain and offset drift relative to 25 degC, the offset away "
    "from the cal point, and the quantisation of the cal reading and of the operating reading (1 LSB each). Derivation: "
    "`fs_cal_error`.",
    "",
    f"A zero-current offset-only calibration would remove only the offset, which is {OFF_FS_PCT:.4f} % of reading at "
    f"{I_MAX:g} A (1 uV / ({R_NOM * 1e3:g} mohm x {I_MAX:g} A)). It is not what is modelled: the gain calibration at {CAL_I:g} A is.",
    "",
]
md += stack_md(f"Stack-up, BOL UNCALIBRATED, {I_MAX:g} A, self-heated", R_UN_FS, UN_FS, I_MAX)
md += [""]
md += stack_md(f"Stack-up, BOL CALIBRATED, {I_MAX:g} A, self-heated (judged)", R_CA_FS, CA_FS, I_MAX)
md += [""]
md += stack_md(f"Stack-up, BOL UNCALIBRATED, {I_LOW * 1e3:g} mA, self-heated (report only)", R_UN_LO, UN_LO, I_LOW)
md += [""]
md += stack_md(f"Stack-up, BOL CALIBRATED, {I_LOW * 1e3:g} mA, self-heated (report only)", R_CA_LO, CA_LO, I_LOW)
md += [
    "",
    f"**Judged result (requirement +/-{ERR_LIMIT_PCT:g} % of reading at {I_MAX:g} A, BOL, calibrated, self-heated):** "
    f"{FS_CAL_LO:.3f} to +{FS_CAL_HI:.3f} %, max magnitude **{FS_CAL_PCT:.3f} %**, **{err_pf(FS_CAL_PCT)}**, margin "
    f"{FS_CAL_MARGIN:+.3f} % of reading. Top contributors: {', '.join(TOP_CA_FS)}.",
    "",
    f"Uncalibrated, same case: max magnitude **{FS_UNCAL_PCT:.3f} %** (min {FS_UN_LO:.3f} %, max +{FS_UN_HI:.3f} %): "
    f"**{err_pf(FS_UNCAL_PCT)}** against {ERR_LIMIT_PCT:g} %, reported and not judged. The uncalibrated error is set by the "
    f"solder-heat test limit ({SOLDER_CITE}). Top contributors: {', '.join(TOP_UN_FS)}.",
    "",
    f"At {I_LOW * 1e3:g} mA (REPORT ONLY, no limit set): calibrated max {LO_CAL_PCT:.3f} % (min {LO_CAL_LO:.3f} %, max "
    f"+{LO_CAL_HI:.3f} %); uncalibrated max {LO_UNCAL_PCT:.3f} %. The calibrated {I_LOW * 1e3:g} mA error is set by the shunt TCR "
    "relative to the cal element temperature and by the offset away from the cal point.",
    "",
    f"Sampling check (uniform random draws of every input inside its limits, {20000} points, at {I_MAX:g} A): calibrated "
    f"{MC_CA[0] / I_MAX * 100:.4f} to +{MC_CA[1] / I_MAX * 100:.4f} % ({'inside' if MC_CA_OK else 'OUTSIDE'} the corner "
    f"bounds); uncalibrated {MC_UN[0] / I_MAX * 100:.3f} to +{MC_UN[1] / I_MAX * 100:.3f} % "
    f"({'inside' if MC_UN_OK else 'OUTSIDE'} the corner bounds). The ratio form of the calibrated error is not multilinear, "
    "so the corners are checked by sampling.",
    "",
    "![Error stack-up](design-analysis/error_stackup.png)",
    "",
    f"Figure 4a. Contributors, largest first, for uncalibrated and calibrated BOL error at {I_MAX:g} A (top) and {I_LOW * 1e3:g} mA (bottom). "
    f"Magnitudes in % of reading; the dotted line is the {ERR_LIMIT_PCT:g} % requirement ({I_MAX:g} A only). Each panel has its own x scale.",
    "",
]

# %%
fig, axes = plt.subplots(2, 2, figsize=(12.2, 9.4))
panels = [
    (axes[0][0], R_UN_FS, ORANGE, f"BOL uncalibrated, {I_MAX:g} A", FS_UNCAL_PCT, True),
    (axes[0][1], R_CA_FS, BLUE, f"BOL calibrated, {I_MAX:g} A (judged)", FS_CAL_PCT, True),
    (axes[1][0], R_UN_LO, ORANGE, f"BOL uncalibrated, {I_LOW * 1e3:g} mA", LO_UNCAL_PCT, False),
    (axes[1][1], R_CA_LO, BLUE, f"BOL calibrated, {I_LOW * 1e3:g} mA", LO_CAL_PCT, False),
]
for ax, rows, col, title, jm, is_judged in panels:
    names = [r["short"] for r in rows][::-1]  # barh plots the first item at the bottom: largest ends on top
    vals = [r["mag"] for r in rows][::-1]
    ax.barh(names, vals, color=col, height=0.62)
    top = max(vals)
    for y, v in enumerate(vals):
        ax.text(v + top * 0.012, y, f"{v:.3g}", va="center", fontsize=8, color=INK)
    ax.axvline(ERR_LIMIT_PCT, color=INK, lw=0.9, ls=(0, (1, 2)))
    ax.text(ERR_LIMIT_PCT, len(vals) - 0.45, f" {ERR_LIMIT_PCT:g} % requirement", fontsize=7.5, color=INK, va="bottom")
    ax.set_xlim(0, max(top, ERR_LIMIT_PCT) * 1.32)
    style_axes(ax, "Magnitude (% of reading)", "")
    ax.tick_params(axis="y", labelsize=8.5, colors=INK)
    ax.set_title(f"{title}: worst case {jm:.3g} %" + ("" if is_judged else " (report only)"), color=INK, fontsize=9.5)
fig.suptitle(f"INA229 + {R_NOM * 1e3:g} mohm WSK2512, BOL error stack-up, self-heated, TA {TA.min:g}-{TA.max:g} degC (largest contributor first)",
             color=INK, fontsize=10.5)
fig.tight_layout()
save(fig, "error_stackup.png")

# %% [markdown]
# ## Error vs current (section 6)

# %%
CURRENTS = [I_PLOT_LO * (I_MAX / I_PLOT_LO) ** (k / (N_PLOT - 1)) for k in range(N_PLOT)]  # log-spaced, 0.1 to 6 A
CUR_UN = [total_uncal(i) for i in CURRENTS]
CUR_CA = [total_cal(i) for i in CURRENTS]
un_lo_p = [judged(w, i)[0] for w, i in zip(CUR_UN, CURRENTS)]
un_hi_p = [judged(w, i)[1] for w, i in zip(CUR_UN, CURRENTS)]
ca_lo_p = [judged(w, i)[0] for w, i in zip(CUR_CA, CURRENTS)]
ca_hi_p = [judged(w, i)[1] for w, i in zip(CUR_CA, CURRENTS)]

# %%
fig, (ax1, ax2) = plt.subplots(2, 1, sharex=True, figsize=(9.4, 7.8))
for ax in (ax1, ax2):
    if ax is ax1:  # the uncalibrated band is off the scale of the zoom panel
        ax.fill_between(CURRENTS, un_lo_p, un_hi_p, color=ORANGE, alpha=0.14, linewidth=0)
        ax.plot(CURRENTS, un_hi_p, color=ORANGE, lw=1.3)
        ax.plot(CURRENTS, un_lo_p, color=ORANGE, lw=1.3, ls="--")
    ax.fill_between(CURRENTS, ca_lo_p, ca_hi_p, color=BLUE, alpha=0.18, linewidth=0)
    ax.plot(CURRENTS, ca_hi_p, color=BLUE, lw=1.3)
    ax.plot(CURRENTS, ca_lo_p, color=BLUE, lw=1.3, ls="--")
    ax.axhline(0, color=MUTED, lw=0.6)
    ax.axhline(ERR_LIMIT_PCT, color=INK, lw=0.9, ls=(0, (1, 2)))
    ax.axhline(-ERR_LIMIT_PCT, color=INK, lw=0.9, ls=(0, (1, 2)))
    ax.set_xscale("log")
    style_axes(ax, "Load current (A), log scale", "Error (% of reading)")
ax1.set_ylim(-1.12 * max(abs(v) for v in un_lo_p + un_hi_p), 1.12 * max(abs(v) for v in un_lo_p + un_hi_p))
ax2.set_ylim(-1.3 * ERR_LIMIT_PCT, 1.3 * ERR_LIMIT_PCT)
handles = [
    Line2D([], [], color=ORANGE, lw=1.3, label="BOL uncalibrated, self-heated: max (min dashed)"),
    Line2D([], [], color=BLUE, lw=1.3, label="BOL calibrated, self-heated (judged): max (min dashed)"),
    Line2D([], [], color=INK, lw=0.9, ls=(0, (1, 2)), label=f"+/-{ERR_LIMIT_PCT:g} % requirement ({I_MAX:g} A)"),
]
ax1.legend(handles=handles, frameon=False, loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=8.5, labelcolor=INK)
ax1.set_title(f"INA229 + {R_NOM * 1e3:g} mohm WSK2512: BOL error vs load current, TA {TA.min:g}-{TA.max:g} degC", color=INK, fontsize=10.5)
ax2.set_title("Zoom on the calibrated band (uncalibrated is off this scale)", color=INK, fontsize=9.5)
ax2.text(CURRENTS[-1], ca_hi_p[-1], f" {I_MAX:g} A: {FS_CAL_LO:.2f} to +{FS_CAL_HI:.2f} %", fontsize=8, color=INK, va="bottom",
         ha="right")
ax2.set_xlabel("Load current (A), log scale", color=MUTED)
fig.tight_layout()
save(fig, "current_error.png")

md += [f"## 6. Error vs current ({I_PLOT_LO * 1e3:g} mA to {I_MAX:g} A, BOL)", ""]
md += [
    f"{N_PLOT} log-spaced points, each the worst case over all corners of section 5b with 1 LSB added. Blue: BOL calibrated, "
    "self-heated (judged). Orange: BOL uncalibrated, self-heated. Top: the full range, with the uncalibrated band. Bottom: "
    f"zoom on the calibrated band against the {ERR_LIMIT_PCT:g} % requirement.",
    "",
    "![INA229 current error](design-analysis/current_error.png)",
    "",
    "Figure 4. BOL error vs load current: uncalibrated and calibrated, self-heated.",
    "",
    f"Calibrated: at {I_PLOT_LO:g} A min {ca_lo_p[0]:.3g} %, max {ca_hi_p[0]:.3g} %; at {I_MAX:g} A min {ca_lo_p[-1]:.3g} %, max "
    f"{ca_hi_p[-1]:.3g} %. Uncalibrated: at {I_PLOT_LO:g} A min {un_lo_p[0]:.3g} %, max {un_hi_p[0]:.3g} %; at {I_MAX:g} A min "
    f"{un_lo_p[-1]:.3g} %, max {un_hi_p[-1]:.3g} %.",
    "",
    f"**Judged: BOL calibrated, self-heated, {I_MAX:g} A: {FS_CAL_PCT:.3f} % of reading (vs {ERR_LIMIT_PCT:g} %: "
    f"{err_pf(FS_CAL_PCT)}); {I_LOW * 1e3:g} mA: {LO_CAL_PCT:.3f} % (REPORT ONLY, no limit set at {I_LOW * 1e3:g} mA).** Uncalibrated: "
    f"{FS_UNCAL_PCT:.3f} % at {I_MAX:g} A, {LO_UNCAL_PCT:.3f} % at {I_LOW * 1e3:g} mA (reported, not judged).",
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
ax.set_title(f"INA229 bus-voltage error vs bus voltage, TA {TA.min:g}-{TA.max:g} degC", color=INK, fontsize=10.5)
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
    f"dVS = +/-{mv(DVS.max)} mV ({VS_NOM:g} V +/-{VS_TOL * 100:g} %). The PSRR row's test condition is VS = 2.7 to 5.5 V, so the dVS reference "
    f"is an assumption (open item).",
    "",
]
md += mdtable(["V_true (V)", "Min err (mV)", "Typ err (mV)", "Max err (mV)", "Min (% of V)", "Max (% of V)",
               "Min incl. 1 LSB (mV)", "Max incl. 1 LSB (mV)", "PSRR typical, not bounded (mV)"], bus_rows)
md += [
    "",
    "![Bus-voltage error vs bus voltage](design-analysis/bus_error.png)",
    "",
    f"Figure 5. Bus-voltage error vs bus voltage {VBUS_LO:g} to {VBUS_HI:g} V (band: min to max incl. 1 LSB; the typ-only PSRR is not in the band).",
    "",
    f"1 LSB = {LSB_VBUS * 1e6:.4f} uV (`lsb_vbus_25c`), added to min and max. Typ = nominal part at 25 degC with all bound "
    "rows centred (their DS typical spread is not added).",
    "",
    "The bus offset (`vos_bus_25c`) is specified only at VBUS = 20 mV, and the bus gain error at the table header "
    f"condition. Their use at {VBUS_LO:g}-{VBUS_HI:g} V assumes the errors are flat with bus voltage (open item).",
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


P_VOLTS = (VBUS_LO, P["vbus_mid_v"], VBUS_HI)  # V, bus voltages for the power-error table and chart
PW_CURRENTS = [I_PLOT_LO * (I_MAX / I_PLOT_LO) ** (k / (N_PLOT_PWR - 1)) for k in range(N_PLOT_PWR)]
PW_BAND = {v: [power_band(i_pt, v) for i_pt in PW_CURRENTS] for v in P_VOLTS}

# sampling check: finer ambient grid at the two summary points
PW_CHK = {}
T_FINE = [TA.min + k * (TA.max - TA.min) / 8 for k in range(9)]
for v in (VBUS_LO, VBUS_HI):
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
md += mdtable(["Current (A)", f"{P_VOLTS[0]:g} V min (%)", f"{P_VOLTS[0]:g} V max (%)", f"{P_VOLTS[1]:g} V min (%)", f"{P_VOLTS[1]:g} V max (%)", f"{P_VOLTS[2]:g} V min (%)",
               f"{P_VOLTS[2]:g} V max (%)"], pw_rows)
md += [
    "",
    f"Sampling check (9 ambient points vs the {len(T_POWER)} used, at {I_MAX:g} A): difference at {VBUS_LO:g} V "
    f"{PW_CHK[VBUS_LO][0]:.2e} low, {PW_CHK[VBUS_LO][1]:.2e} high; at {VBUS_HI:g} V {PW_CHK[VBUS_HI][0]:.2e} low, "
    f"{PW_CHK[VBUS_HI][1]:.2e} high (fraction).",
    "",
    f"Cross-check: the INA229 device spec `ptme_full` ({cite(PTME)}) is {PTME.min * 100:g} to +{PTME.max * 100:g} % at full "
    "scale over -40 to 125 degC. It is a device figure with an ideal shunt and does not include the external shunt's "
    "tolerance and TCR, which this result does. The power error here is therefore larger, as expected.",
    "",
    "![Power error vs current](design-analysis/power_error.png)",
    "",
    f"Figure 6. Worst-case power measurement error vs current at {P_VOLTS[0]:g}, {P_VOLTS[1]:g} and {P_VOLTS[2]:g} V (min and max, ambient only).",
    "",
]

# %%
fig, ax = plt.subplots(figsize=(9.4, 4.8))
pw_cols = {P_VOLTS[0]: BLUE, P_VOLTS[1]: GREEN, P_VOLTS[2]: RED}
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
    [f"VBUS divider at {VBUS_HI:g} V (V^2 / zvbus min)", f"{P_BUS_PART_40 * 1e3:.4g} mW (incl. IN bias)", cite(ZV)],
    ["rtheta_ja (typical)", f"{RTJA:g} degC/W", cite(RTJA_P)],
    [f"Junction temperature at TA {TA.max:g} degC, V3V3 only (ESTIMATE)", f"**{TJ_V3:.4g} degC**",
     "TA + P_V3 x rtheta_ja"],
    [f"Junction temperature at TA {TA.max:g} degC, all INA229 power (ESTIMATE)", f"**{TJ_ALL:.4g} degC**",
     f"adds the VBUS divider at {VBUS_HI:g} V"],
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
     f"{ua(I_ALERT_MAX)} uA", f"{R_PULLUP / 1e3:g} kohm {R_PU_MPN}: R min {R_PU_LIB.min / 1e3:.4g} kohm over tol, TCR, {TA.min:g}-{TA.max:g} degC "
     "(ERJ-3EKF tol at 20 degC, TCR at 25 degC)"],
    ["Power from V3V3: active, 25 degC", "V3V3", "-", f"{mw(P_IQ_25_TYP)} mW", f"{mw(P_IQ_25_MAX)} mW",
     f"`iq_25c` x VS (nom / max {VS_MAX:.3f} V)"],
    ["Power from V3V3: active, worst", "V3V3", "-", "-", f"{mw(P_IQ_FULL)} mW", "`iq_full` x VS max"],
    ["Power from V3V3: ALERT_N asserted (rail)", "V3V3", "0", f"{mw(VS_NOM * I_ALERT_TYP)} mW", f"{mw(P_ALERT_RAIL)} mW",
     "VS x I_alert"],
    [f"VBUS input current at {VBUS_LO:g} V (`zvbus_25c`)", "Bus", f"{ua(ZV_12[0])} uA", f"{ua(ZV_12[1])} uA", f"{ua(ZV_12[2])} uA",
     cite(ZV)],
    [f"VBUS input current at {VBUS_HI:g} V (`zvbus_25c`)", "Bus", f"{ua(ZV_40[0])} uA", f"{ua(ZV_40[1])} uA", f"{ua(ZV_40[2])} uA",
     "V / zvbus"],
    [f"Power in INA229 from VBUS at {VBUS_LO:g} V", "Bus", mw(P_ZV_12[0]) + " mW", mw(P_ZV_12[1]) + " mW", mw(P_ZV_12[2]) + " mW",
     "V x I"],
    [f"Power in INA229 from VBUS at {VBUS_HI:g} V", "Bus", mw(P_ZV_40[0]) + " mW", mw(P_ZV_40[1]) + " mW", mw(P_ZV_40[2]) + " mW",
     "V x I"],
    ["IN+ / IN- input bias (`ib_25c`)", "Bus", f"{IB.min * 1e9:.3g} nA", "0", f"{IB.max * 1e9:.3g} nA",
     cite(IB) + "; current, negligible power (< 1 uW)"],
    [f"Shunt insertion loss I^2 R at {I_MAX:g} A (outside the part)", "Bus", mw(P_SHUNT_5[0]) + " mW",
     mw(P_SHUNT_5[1]) + " mW", mw(P_SHUNT_5[2]) + " mW", f"`power_worst` at TA {TA.min:g} and {TA.max:g} degC (ambient R); typ = nominal 25 degC"],
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
    [f"Bus {VBUS_LO:g} V", "INA229 pin power, worst (VBUS divider + IN bias)", mw(TOT_BUS_12)],
    [f"Bus {VBUS_HI:g} V", "INA229 pin power, worst (VBUS divider + IN bias)", mw(TOT_BUS_40)],
    [f"Bus {VBUS_LO:g} V", f"Shunt I^2 R at {I_MAX:g} A, worst (outside the INA229)", mw(BUS_SHUNT_5)],
    [f"Bus {VBUS_HI:g} V", f"Shunt I^2 R at {I_MAX:g} A, worst (outside the INA229)", mw(BUS_SHUNT_5)],
])
md += [
    "",
    f"Assumptions: `iq_full` and `iq_25c` are printed without a VS condition; the worst case assumes VS = {VS_MAX:.3f} V. "
    "`zvbus_25c` is a 25 degC row; the 800 kohm minimum is applied at all temperatures (open item). The ALERT_N pull-up "
    "is assumed asserted in shutdown (conservative).",
    "",
    "![Quiescent power budget](design-analysis/quiescent_budget.png)",
    "",
    "Figure 7. Worst-case power budget. Left: V3V3 rail (INA229 active plus ALERT_N pull-up asserted). Right: bus-pin power "
    f"inside the INA229 at {VBUS_LO:g} and {VBUS_HI:g} V. The shunt I^2 R at {I_MAX:g} A ({BUS_SHUNT_5 * 1e3:.0f} mW, outside the part) is annotated, "
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

bus_lbls = [f"{VBUS_LO:g} V", f"{VBUS_HI:g} V"]
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
DIFF_ROW = rating_row(f"Differential, hard load short (IN+ = {VBUS_HI:g} V, IN- = 0 V)", VBUS_HI, VBUS_HI,
                      ina.param("vin_diff_abs"), "vin_diff_abs", 1, "V")
VDIFF_ROW = rating_row("Shunt sense voltage (worst, self-heated)", -VS_SH, VS_SH, VDIFF, "vdiff_adcr0_full", 1e3, "mV")
ratings = [
    rating_row(f"Bus voltage VBUS (applied {VBUS_LO:g} to {VBUS_HI:g} V)", VBUS_LO, VBUS_HI, ina.param("vbus_abs"), "vbus_abs", 1, "V"),
    rating_row(f"Common mode VIN+/VIN- (abs, applied {VBUS_LO:g} to {VBUS_HI:g} V)", VBUS_LO, VBUS_HI, ina.param("vcm_abs"), "vcm_abs", 1, "V"),
    rating_row(f"Common mode VIN+/VIN- (rec, applied {VBUS_LO:g} to {VBUS_HI:g} V)", VBUS_LO, VBUS_HI, ina.param("vcm_rec"), "vcm_rec", 1, "V"),
    DIFF_ROW,
    rating_row("IIN into any pin (applied: input bias IB max)", 0.0, IB.max, ina.param("iin_abs"), "iin_abs", 1e3, "mA"),
    rating_row("IOUT, digital output (applied: ALERT_N sink I_alert max)", 0.0, I_ALERT_MAX, ina.param("iout_abs"),
               "iout_abs", 1e3, "mA"),
    rating_row(f"VS supply, rec (applied {VS_NOM:g} V +/-{VS_TOL * 100:g} %)", VS_MIN, VS_MAX, ina.param("vs_rec"), "vs_rec", 1, "V"),
    rating_row(f"VS supply, abs max (applied {VS_NOM:g} V +/-{VS_TOL * 100:g} %)", VS_MIN, VS_MAX, ina.param("vs_abs"), "vs_abs", 1, "V"),
    rating_row("VIO, MOSI/MISO/SCLK/ALERT_N low level (applied 0 V; limit GND - 0.3 V)", 0.0, 0.0,
               VIO_ABS, "vio_abs", 1, "V"),
    rating_row(f"VIO, MOSI/MISO/SCLK/ALERT_N high level (applied {VS_NOM:g} V +/-{VS_TOL * 100:g} % max; limit VS + 0.3 V, VS at its min)",
               LOGIC_HI, LOGIC_HI, VIO_ABS, "vio_abs", 1, "V"),
    rating_row(f"TA ambient (rec, applied {TA.min:g} to {TA.max:g} degC)", TA.min, TA.max, ina.param("ta_rec"), "ta_rec", 1, "degC"),
    ["Junction temperature (INA229, ESTIMATE)", TJ_ROW_TXT[0], TJ_ROW_TXT[1], TJ_ROW_TXT[2], TJ_ROW_TXT[3],
     cite(TJ_ABS_P)],
    VDIFF_ROW,
    [f"Shunt dissipation vs rating ({TA.max:g} degC)", f"0 to {PW.max:.4g} W", f"p70 x derate: {RATED:.4g} W",
     f"{PW_MARGIN:.4g} W", status(PW_MARGIN), cite(shunt.param("p70"))],
    ["Shunt sense vs (P x R)^1/2", f"{VS_SH * 1e3:.4g} mV", f"(P x R)^1/2 = {MWV * 1e3:.4g} mV",
     f"{(MWV - VS_SH) * 1e3:.4g} mV", status(MWV - VS_SH), cite(shunt.param("max_working_voltage"))],
    ["Storage temperature Tstg (abs)", "-", f"`tstg_abs` -65 to 150 degC", "-",
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
    f"{VS_MIN + 0.3:.3f} V. The applied high level is the {VS_NOM:g} V +/-{VS_TOL * 100:g} % maximum, {VS_MAX:.3f} V, so the margin is "
    f"{(VS_MIN + 0.3 - VS_MAX) * 1e3:.0f} mV. CS is not in the DS row (DS omission, library note): not checked.",
    "",
    f"Differential check: the hard-short differential equals the {VBUS_HI:g} V bus across the shunt (zero drop at the short). "
    f"It sits exactly at the `vin_diff_abs` max of {VBUS_HI:g} V (DS p.3). Zero margin: OPEN ITEM.",
    "",
    f"The VS row checks {VS_NOM:g} V +/-{VS_TOL * 100:g} % ({VS_MIN:.3f} to {VS_MAX:.3f} V) against `vs_rec` (2.7 to 5.5 V, DS p.4); the "
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
    [f"Sense voltage headroom (self-heated worst Vsense at {I_MAX:g} A)", f"{mv(VS_SH)} mV", f"{mv(ADC_LIM[ADCRANGE])} mV "
     f"(ADCRANGE {ADCRANGE})", f"{mv(VS_MARGIN)} mV ({pct(VS_MARGIN / ADC_LIM[ADCRANGE])} %)", status(VS_MARGIN)],
    [f"Shunt dissipation at {I_MAX:g} A, {TA.max:g} degC (ambient rating)", f"{PW.max:.4g} W", f"{RATED:.4g} W (p70 x derate)",
     f"{PW_MARGIN:.4g} W ({pct(PW_MARGIN / RATED)} %)", status(PW_MARGIN)],
    [f"Shunt element temperature at {I_MAX:g} A, {TA.max:g} degC (theta_est " f"{THETA:g} degC/W, self-consistent)",
     f"{T_HOT_5:.4g} degC", f"{T_ELEM_LIMIT:g} degC (ASSUMED element limit)", f"{T_MARGIN_5:.4g} degC",
     elem_status(T_MARGIN_5)],
    [f"BOL calibrated error at {I_MAX:g} A (REQUIREMENT, judged)", f"{FS_CAL_LO:.3f} to +{FS_CAL_HI:.3f} % (max {FS_CAL_PCT:.3f} %)",
     f"+/-{ERR_LIMIT_PCT:g} % of reading", f"{FS_CAL_MARGIN:+.3f} % of reading",
     f"{err_pf(FS_CAL_PCT)}, BOL, self-heated, one-point cal at {CAL_I:g} A / {CAL_TA:g} degC"],
    [f"BOL uncalibrated error at {I_MAX:g} A (self-heated, solder-heat limit)", f"{FS_UN_LO:.3f} to +{FS_UN_HI:.3f} % (max {FS_UNCAL_PCT:.3f} %)",
     f"+/-{ERR_LIMIT_PCT:g} % (not the judged case)", f"{ERR_LIMIT_PCT - FS_UNCAL_PCT:+.3f} % of reading",
     f"{err_pf(FS_UNCAL_PCT)} (reported; the judged case is calibrated)"],
    [f"Full-scale current error at {I_MAX:g} A, ambient only (reference)", err_row_val(FS_MIN, FS_MAX, I_MAX)
     + f" (max {FS_AMB_PCT:.3f} %)", "reference only", "n/a", "REFERENCE (not judged)"],
    [f"BOL calibrated error at {I_LOW * 1e3:g} mA (report)", f"{LO_CAL_LO:.3f} to +{LO_CAL_HI:.3f} % (max {LO_CAL_PCT:.3f} %)",
     f"none set at {I_LOW * 1e3:g} mA", "n/a", "REPORT ONLY (no limit set)"],
    [f"BOL uncalibrated error at {I_LOW * 1e3:g} mA (report)", f"{LO_UN_LO:.3f} to +{LO_UN_HI:.3f} % (max {LO_UNCAL_PCT:.3f} %)",
     f"none set at {I_LOW * 1e3:g} mA", "n/a", "REPORT ONLY (no limit set)"],
    [f"Bus-voltage error at {VBUS_LO:g} V (incl. 1 LSB)", f"+/-{mv(BUS_12)} mV (+/-{pct(BUS_12 / VBUS_LO)} %)",
     "none set", "n/a", "REPORT ONLY (no limit set)"],
    [f"Bus-voltage error at {VBUS_HI:g} V (incl. 1 LSB)", f"+/-{mv(BUS_40)} mV (+/-{pct(BUS_40 / VBUS_HI)} %)",
     "none set", "n/a", "REPORT ONLY (no limit set)"],
    [f"Power error at {I_MAX:g} A, {VBUS_LO:g} V (ambient)", f"{pct(PW_5[VBUS_LO][0])} to +{pct(PW_5[VBUS_LO][1])} %",
     "none set", "n/a", "REPORT ONLY (no limit set)"],
    [f"Power error at {I_MAX:g} A, {VBUS_HI:g} V (ambient)", f"{pct(PW_5[VBUS_HI][0])} to +{pct(PW_5[VBUS_HI][1])} %",
     "none set", "n/a", "REPORT ONLY (no limit set)"],
    [f"INA229 junction temperature at TA {TA.max:g} degC (typical-based)", f"{TJ_ALL:.4g} degC (all power)",
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
    [f"VIO high level, MOSI/MISO/SCLK/ALERT_N ({VS_NOM:g} V +/-{VS_TOL * 100:g} %)", vio_hi_row[1], vio_hi_row[2], vio_hi_row[3], vio_hi_row[4]],
    ["MISO output source resistance (design placeholder)", f"{R_MISO:g} ohm", f"<= {R_OUT_MAX:.0f} ohm (`voh_25c`)",
     f"{R_OUT_MAX - R_MISO:.0f} ohm", "PASS (placeholder, OPEN ITEM)"],
]

md_summary = ["## Summary of key results", ""]
md_summary += mdtable(["Result", "Value (worst case)", "Limit", "Margin", "Status"], SUMMARY)
md_summary += [
    "",
    "Status: PASS / FAIL against a limit from blocks/INA229HighSideMonitor/spec.md or the datasheet. ESTIMATE: a curve-based or typical-based result "
    "(theta_est, rtheta_ja, the self-heating band), not a datasheet bound. REPORT ONLY: no limit set in blocks/INA229HighSideMonitor/spec.md "
    f"(`ERR_LIMIT_PCT` is set to {ERR_LIMIT_PCT:g} % for the full-scale current error only). REFERENCE: shown for comparison, not judged. "
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
    f"- The element limit ({T_ELEM_LIMIT:g} degC) is an ASSUMPTION (derate_zero read as the element limit). `t_oper` is an ambient "
    f"rating in the library and is not used as the element limit. PASS on the element margin holds only if the limit is {T_ELEM_LIMIT:g} degC.",
    "- Copper and connector heating at full scale is not analysed (trace, connector and terminal resistance and their temperature "
    "rise are outside this model).",
    "- PCB thermal coupling between the shunt and the INA229 is ignored: the INA229 is assumed at ambient except for its own "
    "dissipation (section 9).",
    "- The shunt TCR reference temperature (25 degC) and the TCR validity span are not stated in the WSK2512 DS.",
    "- Excluded terms: CMRR (`cmrr_full`), INL, DNL, INA229 internal die-temperature effects, PCB and Kelvin trace resistance, "
    "the ADC noise, and the 1 LSB term is conservative (a quantizer is +/-0.5 LSB; 1 LSB was requested). SHUNT_CAL rounding "
    "is included (section 5). The INA229 POWER register is not used: the power result here is V x I from the measured "
    "current and bus voltage.",
    f"- The gain error row (`gserr_25c`) is specified at VCM = 24 V; the bus is {VBUS_LO:g}-{VBUS_HI:g} V (open item).",
    "- End-of-life (EOL) drift is OUT OF SCOPE for now (open item): load life, thermal shock, high-temperature exposure and "
    "bias humidity (test limits +/-(0.5-1 % + 0.0005 ohm), DS p.3) are not analysed. The INA229 has no long-term drift spec.",
    f"- Calibration: the cal ambient ({CAL_TA:g} degC) and the cal reference (+/-{CAL_REF_PCT:g} % of reading) are assumptions / design inputs. "
    f"A one-point gain calibration at {CAL_I:g} A is modelled; a zero-current offset-only calibration is not.",
    "- The solder-heat shift is a qualification test limit used as a worst-case bound; typical shifts are not specified and "
    "are not used.",
    "- The shunt rating is a DS rating against ambient at 70 degC; the element temperature is an estimate (section 3).",
    f"- The board's `vsense_max` ({VSENSE_MAX_MV:.1f} mV, instance configs) is the self-heated worst Vsense ({mv(VS_SH)} mV) "
    "rounded up to 0.1 mV. It is a declared value for the rating check, not a measured one.",
    "",
]

# %% [markdown]
# ## Assemble and write the Markdown

# %%
HEADER = [
    "# INA229HighSideMonitor block: design analysis",
    "",
    "> GENERATED by `python3 blocks/INA229HighSideMonitor/analysis.py` (see its header for --params and --out). Do not edit this file; "
    "change the script and re-run.",
    "",
    f"- Library: zener-library {ZL_VERSION} (`analysis/_zl.py`, component data and calc functions).",
    f"- Results date: {RUN_DATE}.",
    f"- Instance `{INSTANCE}`, params `{PARAMS_REL}`. Key results: `design-analysis/values.json` (generated by the same run).",
    "",
    f"High-side current sense: {I_MAX:g} A bidirectional full scale, bus {VBUS_LO:g} to {VBUS_HI:g} V, "
    f"TA {TA.min:g} to {TA.max:g} degC, VS {VS_NOM:g} V +/-{VS_TOL * 100:g} %. Design: ADCRANGE {ADCRANGE}, "
    f"{R_NOM * 1e3:g} mohm code {code} (`{MPN}`).",
    "",
    "## Operating conditions and assumptions",
    "",
    f"- Full scale: {I_MAX:g} A, bidirectional (basis: `i_fs_a_basis` in the params file). Bus {VBUS_LO:g} to {VBUS_HI:g} V DC, high-side.",
    f"- Accuracy requirement: +/-{ERR_LIMIT_PCT:g} % of reading at {I_MAX:g} A full scale (basis: `err_limit_pct_basis` in the params file), "
    "judged on the BOL CALIBRATED self-heated worst case (ESTIMATE). The uncalibrated BOL error is reported, not judged. "
    "The 100 mA error is REPORT ONLY.",
    f"- TA {TA.min:g} to {TA.max:g} degC (board ambient); nominal (typ) reference 25 degC.",
    f"- VS = {VS_NOM:g} V +/-{VS_TOL * 100:g} %, one rail shared by the host and the INA229.",
    f"- Derate = {DERATE:g}. Shunt TCR reference temperature {T_REF:g} degC (the WSK2512 DS states none; assumed).",
    f"- Self-heating is an ESTIMATE from the shunt derating curve (theta_est, section 3).",
    f"- Production: one-point gain calibration at {CAL_I:g} A and {CAL_TA:g} degC ambient (ASSUMPTION, `CAL_TA`), reference "
    f"+/-{CAL_REF_PCT:g} % of reading (design input). Solder-heat shift: qualification test limit, used as a bound.",
    f"- Element temperature limit {T_ELEM_LIMIT:g} degC is an ASSUMPTION (`T_ELEM_LIMIT`), not t_oper.",
    f"- Ambient full-scale error uses {SAMPLES} interior samples per input (quadratic in TA); the self-heated band is "
    "multilinear in its two temperatures, so corners are exact (section 5).",
    f"- MISO series resistor {R_MISO:g} ohm (placeholder), ALERT_N pull-up {R_PULLUP / 1e3:g} kohm (blocks/INA229HighSideMonitor/spec.md).",
    "",
    "Conventions: PASS / FAIL against a datasheet, blocks/INA229HighSideMonitor/spec.md or the designer's requirement; ESTIMATE for curve-based or "
    "typical-based results; REPORT ONLY where no limit is set; REFERENCE for comparison only; OPEN ITEM for zero or "
    "placeholder margin; CHECK for a value with no spec limit.",
    "",
    "## Contents",
    "",
    "Design definition; summary; 1 shunt operating point; 2 shunt power and rating; 3 shunt self-heating (estimate); "
    "4 firmware constants; 5 full-scale current error (pre-solder reference); 5b BOL accuracy, uncalibrated and calibrated "
    "(judged, stack-up); 6 error vs current; 7 bus-voltage error; 8 power measurement error; "
    "9 INA229 junction temperature (estimate); 10 quiescent current and power budget; 11 ratings; 12 MISO; 13 limits.",
    "",
]

DESIGN_MD = ["## Design definition", ""] + mdtable(
    ["Function", "Part", "Value / choice", "Basis (library cite or source)"], [
        ["Monitor", "TI INA229AIDGS", f"ADCRANGE {ADCRANGE} (+/-{mv(ADC_LIM[ADCRANGE])} mV), CURRENT_LSB, SHUNT_CAL",
         cite(VDIFF)],
        ["Shunt", f"`{MPN}` (Vishay WSK2512)", f"{R_NOM * 1e3:g} mohm, +/-{pct(TOL.max)} % (code {code}), "
         f"{P70:g} W at 70 degC, 4-terminal Kelvin sense", f"{cite(TOL)}; {cite(shunt.param('p70'))}"],
        ["VS bypass", "Murata GCM188R71H104KA57", "100 nF X7R 50 V 0603, at pin VS", "INA229 DS section 9 (not analysed here)"],
        ["MISO series resistor", "Panasonic ERJ-3EKF", f"{R_MISO:g} ohm 0603, at the INA229 MISO pin (placeholder)",
         "design choice; section 12"],
        ["ALERT_N pull-up", f"`{erj.mpn(R_PULLUP)}` (Panasonic ERJ-3EKF)", f"{R_PULLUP / 1e3:g} kohm 0603 to V3V3, DNP-able",
         cite(erj.param("tol"))],
        ["Calibration point (one-point gain)", "design input", f"{CAL_I:g} A at {CAL_TA:g} degC, reference +/-{CAL_REF_PCT:g} %; {CAL_I_SRC}",
         "params `cal_i_a` (basis in the params file)"],
    ]) + [""]

FINAL = HEADER + DESIGN_MD + md_summary + md
DOCS.mkdir(parents=True, exist_ok=True)
FINAL += inputs_section()  # library rows read and their `verified` stamps (zener-library lib-api.md)
write_report(MD_PATH, "\n".join(FINAL) + "\n", unverified=unverified_inputs())  # .md unless only the results date changed, then the PDF (md hash stamp)

# %% [markdown]
# ## values.json and console summary
# Key results as numbers (top level) and as pre-formatted strings (under "text"), for the board and the block spec.

# %%
VALUES = {
    "i_fs_a": I_MAX,
    "rshunt_mohm": R_NOM * 1e3,
    "tol_pct": TOL.max * 100,
    "mpn": MPN,
    "vsense_max_mv": VS_SH * 1e3,
    "vsense_round_up_mv": VSENSE_MAX_MV,
    "adc_range_mv": ADC_LIM[ADCRANGE] * 1e3,
    "headroom_pct": VS_MARGIN / ADC_LIM[ADCRANGE] * 100,
    "p_shunt_w": PW.max,
    "p_rating_w": RATED,
    "t_element_c": T_HOT_5,
    "err_fs_pct": FS_CAL_PCT,  # the judged BOL calibrated value (was the pre-solder self-heated estimate)
    "err_fs_ambient_pct": FS_AMB_PCT,
    "err_low_pct": LO_CAL_PCT,  # BOL calibrated at 100 mA (report only)
    "current_lsb_ua": CURRENT_LSB * 1e6,
    "shunt_cal": SHUNT_CAL,
    "a_per_v": 1 / R_NOM,
    "bus_err_40v_mv": BUS_40 * 1e3,
    "bus_err_12v_mv": BUS_12 * 1e3,
    "vbus_lsb_uv": LSB_VBUS * 1e6,
    "req_pct": ERR_LIMIT_PCT,
    "req_status": err_pf(FS_CAL_PCT),
    "err_fs_cal_pct": FS_CAL_PCT,
    "err_low_cal_pct": LO_CAL_PCT,
    "err_fs_uncal_pct": FS_UNCAL_PCT,
    "err_low_uncal_pct": LO_UNCAL_PCT,
    "cal_ref_pct": CAL_REF_PCT,
    "top_contrib_fs_cal": TOP_CA_FS,
    "top_contrib_fs_uncal": TOP_UN_FS,
}
TEXT = {
    "i_fs_a": f"{I_MAX:g} A",
    "rshunt_mohm": f"{R_NOM * 1e3:g} mohm",
    "tol_pct": f"+/-{TOL.max * 100:g} %",
    "mpn": MPN,
    "vsense_max_mv": f"{VS_SH * 1e3:.2f} mV (self-heated worst, ESTIMATE)",
    "vsense_round_up_mv": f"{VSENSE_MAX_MV:.1f} mV",
    "adc_range_mv": f"+/-{ADC_LIM[ADCRANGE] * 1e3:.2f} mV",
    "headroom_pct": f"{VS_MARGIN / ADC_LIM[ADCRANGE] * 100:.1f} %",
    "p_shunt_w": f"{PW.max:.3f} W ({TA.max:g} degC)",
    "p_rating_w": f"{RATED:.3f} W",
    "t_element_c": f"{T_HOT_5:.1f} degC (ESTIMATE)",
    "err_fs_pct": f"{FS_CAL_PCT:.3f} % (BOL calibrated, self-heated, judged; same as err_fs_cal_pct)",
    "err_fs_ambient_pct": f"{FS_AMB_PCT:.3f} %",
    "err_low_pct": f"{LO_CAL_PCT:.3f} % ({I_LOW * 1e3:g} mA, BOL calibrated, report only)",
    "current_lsb_ua": f"{CURRENT_LSB * 1e6:.4f} uA",
    "shunt_cal": f"{SHUNT_CAL}",
    "a_per_v": f"{1 / R_NOM:g} A/V",
    "bus_err_40v_mv": f"+/-{BUS_40 * 1e3:.1f} mV",
    "bus_err_12v_mv": f"+/-{BUS_12 * 1e3:.1f} mV",
    "vbus_lsb_uv": f"{LSB_VBUS * 1e6:.4f} uV",
    "req_pct": f"+/-{ERR_LIMIT_PCT:g} % of reading at {I_MAX:g} A",
    "req_status": err_pf(FS_CAL_PCT),
    "err_fs_cal_pct": f"{FS_CAL_PCT:.3f} % (BOL calibrated, judged)",
    "err_low_cal_pct": f"{LO_CAL_PCT:.3f} % ({I_LOW * 1e3:g} mA, BOL calibrated, report only)",
    "err_fs_uncal_pct": f"{FS_UNCAL_PCT:.3f} % (BOL uncalibrated, reported, {err_pf(FS_UNCAL_PCT)} vs {ERR_LIMIT_PCT:g} %)",
    "err_low_uncal_pct": f"{LO_UNCAL_PCT:.3f} % ({I_LOW * 1e3:g} mA, BOL uncalibrated, report only)",
    "cal_ref_pct": f"+/-{CAL_REF_PCT:g} % of reading (design input)",
    "top_contrib_fs_cal": ", ".join(TOP_CA_FS),
    "top_contrib_fs_uncal": ", ".join(TOP_UN_FS),
}
FIG_DIR.mkdir(parents=True, exist_ok=True)
(FIG_DIR / "values.json").write_text(
    json.dumps({"instance": INSTANCE, "params_file": PARAMS_REL, **VALUES, "text": TEXT}, indent=2) + "\n")
write_library_inputs(FIG_DIR)  # rows read, for the library's tools/review.py --block

print(f"Design: {R_NOM * 1e3:g} mohm, code {code}, ADCRANGE {ADCRANGE}, MPN {MPN}, full scale {I_MAX:g} A")
print(f"Vsense worst (self-heated) {mv(VS_SH)} mV, headroom {mv(VS_MARGIN)} mV ({pct(VS_MARGIN / ADC_LIM[ADCRANGE])} % of limit); "
      f"vsense_max {VSENSE_MAX_MV:.1f} mV")
print(f"Power worst {PW.max:.4g} W, rated {RATED:.4g} W, margin {PW_MARGIN:.4g} W ({pct(PW_MARGIN / RATED)} %)")
print(f"theta_est {THETA:g} degC/W; element at {I_MAX:g} A, 70 degC {T_HOT_5:.4g} degC (ESTIMATE, residual {T_RESID_5:.1e} degC); "
      f"margin to assumed {T_ELEM_LIMIT:g} degC {T_MARGIN_5:.4g} degC")
print(f"CURRENT_LSB {CURRENT_LSB:.10g} A/LSB, SHUNT_CAL raw {SHUNT_CAL_RAW:.6f} -> {SHUNT_CAL}")
print(f"BOL calibrated at {I_MAX:g} A (judged vs {ERR_LIMIT_PCT:g} %): max {FS_CAL_PCT:.3f} % -> {err_pf(FS_CAL_PCT)}; "
      f"uncalibrated max {FS_UNCAL_PCT:.3f} % (reported); pre-solder self-heated reference {FS_SH_PCT:.3f} %")
print(f"BOL at {I_LOW:g} A (report only): calibrated max {LO_CAL_PCT:.3f} %, uncalibrated max {LO_UNCAL_PCT:.3f} %")
print(f"Power error at {I_MAX:g} A: {VBUS_LO:g} V {pct(PW_5[VBUS_LO][0])} to +{pct(PW_5[VBUS_LO][1])} %, "
      f"{VBUS_HI:g} V {pct(PW_5[VBUS_HI][0])} to +{pct(PW_5[VBUS_HI][1])} %")
print(f"INA229 Tj at TA {TA.max:g}: V3V3 only {TJ_V3:.4g} degC, all power {TJ_ALL:.4g} degC (ESTIMATE, typ rtheta_ja)")
print(f"Quiescent V3V3 worst {mw(TOT_V3_WORST)} mW; typ 25 degC {mw(TOT_V3_ACTIVE_TYP)} mW")
print(f"Wrote {MD_PATH}, {FIG_DIR / 'values.json'} and charts in {FIG_DIR}")
