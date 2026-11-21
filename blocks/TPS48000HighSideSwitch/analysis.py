# %% [markdown]
# # TPS48000HighSideSwitch block: design-analysis generator
#
# This file is the SOURCE of `design-analysis.md`, the PNG charts in `design-analysis/` and `design-analysis/values.json`
# in the output folder. Those outputs are generated: never edit them by hand. One run analyses one instance:
#
#     python3 blocks/TPS48000HighSideSwitch/analysis.py --params <file.json> --out <dir> [--instance NAME]
#
# Defaults (resolved from the repo root, so the cwd does not matter): --params benches/TPS48000HighSideSwitch/params.json,
# --out benches/TPS48000HighSideSwitch/docs, --instance TPS (the bench reference instance). Exit 0 = everything regenerated.
#
# It is a jupytext "percent" file (`# %%` code cells, `# %% [markdown]` text cells): a notebook in VS Code / Jupyter and a
# plain runnable module. In a notebook the kernel's own arguments are ignored and the defaults are used.
#
# Scope: one instance as set by its params file: TPS48000QDGXQ1 + ISC035N10NM5LF2 + WSK2512-LOW shunt, high-side switch.
# Every datasheet number comes from the component library (`part.param`, calc functions) with its cite. Compositions that
# are not datasheet equations (worst-case resistance, self-heating, superposition, avalanche energy) are labelled "not a
# DS equation". Values read from curves (FET SOA, ZthJC, Rds vs Tj, shunt derating) are labelled ESTIMATE. A typical value is
# never used as a bound.

# %%
import argparse
import datetime
import itertools
import json
import math
import subprocess
import sys
from pathlib import Path


def find_repo_root():
    """The repo root: the nearest folder above this file (above the cwd in a notebook, which has no __file__) that holds
    analysis/_zl.py and blocks/."""
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

from analysis import _zl  # noqa: F401  (side effect: puts zener-library on sys.path; keeps its version warning)
from analysis._zl import ZL, ZL_VERSION, zl_git, zl_released
from lib import Param, inputs_section, load as load_part, series, unverified_inputs, wca
from analysis.md_pdf import write_report
from analysis._export import write_library_inputs

import numpy as np
import matplotlib.pyplot as plt  # imported after lib, which selects the Agg backend


def analyze(params_path=None, out=None, instance="TPS"):
    """Analyse one instance: params file in, values dict out; writes design-analysis.md, the charts and values.json to `out`.
    No work happens at import. Defaults: the bench params and docs folder."""
    PARAMS_PATH = Path(params_path).resolve() if params_path else REPO / "benches" / "TPS48000HighSideSwitch" / "params.json"
    OUT = Path(out).resolve() if out else REPO / "benches" / "TPS48000HighSideSwitch" / "docs"
    INSTANCE = instance


    def rel_to_repo(path):
        try:
            return str(Path(path).relative_to(REPO))
        except ValueError:
            return str(path)


    PARAMS_REL = rel_to_repo(PARAMS_PATH)
    DOCS = OUT
    FIG_DIR = DOCS / "design-analysis"
    MD_PATH = DOCS / "design-analysis.md"
    RUN_DATE = datetime.date.today().isoformat()


    git = zl_git


    if zl_released():
        LIB_TEXT = f"zener-library {ZL_VERSION} (`analysis/_zl.py`)"
    else:
        LIB_TEXT = (f"{git('branch', '--show-current') or 'detached'} @ {git('rev-parse', '--short', 'HEAD')}, "
                    f"to be re-pinned to the release tag (`analysis/_zl.py` ZL_VERSION {ZL_VERSION}; checkout `{ZL.name}`)")

    PARAM_KEYS = {
        "i_design_a", "ta_min_c", "ta_max_c", "t_ref_c", "derate", "vbus_lo_v", "vbus_hi_v", "vbus_bench_v", "vbus_min_v",
        "uvlo_margin_v", "ov_min_v", "i_div_min_ua", "i_div_max_ua", "v3v3_nom_v", "v3v3_tol_frac", "rshunt_ohm",
        "rshunt_tol_code", "riscp_ohm", "ctmr_f", "rtmr_ohm", "r1_ohm", "c1_f", "r2_ohm", "c_small_dielectric",
        "c_small_tolerance", "c_small_rated_v", "cbst_f", "cbst_rated_v", "cbst_dielectric", "cbst_tolerance", "cbyp_f",
        "cbyp_rated_v", "dv_bst_v", "cload_f", "i_fault_a", "l_lead_uh", "t_elem_limit_c",
        "r_top_ohm", "r_mid_ohm", "r_bot_ohm", "r_fault_pullup_ohm", "r_pgood_pullup_ohm",
    }


    def load_params(path):
        raw = json.loads(Path(path).read_text())
        keys = {k for k in raw if not k.endswith("_basis")}
        missing, extra = PARAM_KEYS - keys, keys - PARAM_KEYS
        if missing or extra:
            raise SystemExit(f"{path}: missing keys {sorted(missing)}, unknown keys {sorted(extra)}")
        return raw


    P = load_params(PARAMS_PATH)

    # %% [markdown]
    # ## Design inputs
    # Every constant is read from the params file (basis in the file). Nothing here is a datasheet value.

    # %%
    I_DES = P["i_design_a"]
    TA = Param(min=P["ta_min_c"], max=P["ta_max_c"], unit="degC")
    TA_MIN, TA_MAX = TA.min, TA.max
    T_REF = P["t_ref_c"]
    DERATE = P["derate"]
    VBUS_LO, VBUS_HI, VBUS_BENCH, VBUS_MIN = P["vbus_lo_v"], P["vbus_hi_v"], P["vbus_bench_v"], P["vbus_min_v"]
    UVLO_MARGIN, OV_MIN = P["uvlo_margin_v"], P["ov_min_v"]
    UVLO_LIMIT = VBUS_MIN - UVLO_MARGIN  # V, the worst-case UVLO rising trip must not exceed this
    RT_LO, RT_HI = VBUS_BENCH / (P["i_div_max_ua"] * 1e-6), VBUS_BENCH / (P["i_div_min_ua"] * 1e-6)  # ohm, total ladder
    V3V3_NOM, V3V3_TOL = P["v3v3_nom_v"], P["v3v3_tol_frac"]
    V3V3_MAX, V3V3_MIN = V3V3_NOM * (1 + V3V3_TOL), V3V3_NOM * (1 - V3V3_TOL)
    R0 = P["rshunt_ohm"]
    CODE = P["rshunt_tol_code"]
    RISCP, CTMR, RTMR, R1G, C1G, R2G = P["riscp_ohm"], P["ctmr_f"], P["rtmr_ohm"], P["r1_ohm"], P["c1_f"], P["r2_ohm"]
    CBST = P["cbst_f"]  # F, bootstrap capacitor BST to SRC: one part (designer decision D14)
    CBYP = P["cbyp_f"]  # F, VS and SRC bypass capacitors
    DV_BST = P["dv_bst_v"]
    CL = P["cload_f"]
    I_FAULT = P["i_fault_a"]
    L_LEAD = [x * 1e-6 for x in P["l_lead_uh"]]
    T_ELEM_LIMIT = P["t_elem_limit_c"]
    R_DIV = (P["r_top_ohm"], P["r_mid_ohm"], P["r_bot_ohm"])
    R_FPU, R_PPU = P["r_fault_pullup_ohm"], P["r_pgood_pullup_ohm"]

    # %% [markdown]
    # ## Library
    # Parts are loaded through the zener-library `lib`. Every number below is a library row; `cite` is its printed cite.

    # %%
    tps = load_part("TexasInstruments/TPS48000QDGXQ1")
    fet = load_part("Infineon/ISC035N10NM5LF2")
    shl = load_part("Vishay/WSK2512-LOW")
    erj = load_part("Panasonic/ERJ-3EKF")
    gcm = load_part("Murata/GCM188")
    gcb = load_part("Murata/GCM21B")
    calc = tps.calc


    def cite(p):
        return p.cite


    def close(a, b, tol=1e-9):
        assert abs(a - b) <= tol * max(1.0, abs(b)), (a, b)


    # known-value assertions: library stores percents as fractions, uA as A, mV as V
    close(fet.param("rds_on_10v").max, 3.5e-3)
    close(fet.param("tj_tstg").max, 175.0)
    close(fet.param("rth_ja").max, 50.0)
    close(fet.param("id_pulse").max, 656.0)
    close(fet.param("eas").max, 0.398)
    close(shl.param("tol_d").max, 0.005)
    close(shl.param("tcr_3m_4m9").max, 75e-6)
    close(shl.param("perf_offset_ohm").max, 0.0005)
    close(shl.param("perf_solder_heat").max, 0.005)
    close(shl.param("p70").max, 1.0)
    close(tps.param("v_scp_r10k5").max, 0.0489)
    close(tps.param("i_tmr_src_cb").min, 65e-6)
    close(tps.param("abs_cs_pair").max, 100.0)
    close(erj.param("tol").max, 0.01)
    close(erj.param("tcr_10r_1m").max, 100e-6)
    close(erj.mpn(100e3).count("ERJ3EKF"), 1)
    close(calc.vin_uvlo_from_divider(470e3, 28.7e3, 10.4e3, 1.23), 16.016, 2e-3)
    close(calc.vscp_from_riscp(10.5e3), 0.040, 1e-3)

    # DS-printed numbers used only for the 8.2.2 walk-through, as printed in the TPS4800-Q1 DS (p.23-24). They are not in
    # component.toml (the library EXAMPLES carry the ones that reproduce an equation); adding them there is a library change.
    DS_EX = {
        "isc_a": 60.0, "rds_mohm": 2.3, "riscp_printed_kohm": 50.5, "riscp_std_kohm": 51.0, "cbst_std_nf": 100.0,
        "ctmr_std_nf": 3.3, "r1_std_kohm": 470.0, "r2_std_kohm": 29.4, "r3_std_kohm": 10.0, "vinuvlo": 16.0, "vinovp": 60.0,
        "tsc_us": 50.0, "qg_nc": 85.0,
    }
    DS_RISCP_ISC = calc.riscp_for_isc(DS_EX["isc_a"], DS_EX["rds_mohm"] * 1e-3)  # eq. 5 with the DS example inputs


    def ds_example(fn, **match):
        """The library EXAMPLES row of a calc function (DS example inputs and printed result)."""
        for name, kw, expected, rtol, cite_text in calc.EXAMPLES:
            if name == fn and all(kw.get(k) == v for k, v in match.items()):
                return kw, expected, cite_text
        raise KeyError(fn)


    # ---- capacitors: 6.8 nF (C1, CTMR) and the bootstrap capacitor ----
    def pick_small_cap(value):
        """GCM188 catalog selection for a small capacitor: the preferred dielectric and tolerance if listed, else X7R.
        Rated voltage from the params (100 V for C1 and CTMR, designer decision D13)."""
        try:
            return gcm.select(value, P["c_small_rated_v"], P["c_small_dielectric"], P["c_small_tolerance"]), True
        except ValueError:
            return gcm.select(value, P["c_small_rated_v"], "X7R"), False


    SEL_C1, C_PREF_OK = pick_small_cap(C1G)
    SEL_CT, _ = pick_small_cap(CTMR)
    SEL_BST = gcb.select(CBST, P["cbst_rated_v"], P["cbst_dielectric"], P["cbst_tolerance"])  # GCM21B, one part (D14)
    SEL_BYP = gcm.select(CBYP, P["cbyp_rated_v"], "X7R")  # VS and SRC bypass
    SMALL_DIEL = SEL_C1.dielectric
    assert SEL_CT.dielectric == SMALL_DIEL


    def cap_factors(sel):
        """(low, high) capacitance factors of a catalog part over the ambient range: tolerance x temperature. Not a DS equation.
        X7R: tolerance and the full-range tc (+/-15 %, DS p.2) as fractions (library capacitance_worst_pct). C0G: tolerance and
        the 25 to 125 degC coefficient (library capacitance_worst_ppm, dt = 0 to 15 for TA up to 40 degC); below 25 degC the
        coefficient is not printed, so the Table A drift row at -10 degC bounds the 5 to 25 degC segment (ESTIMATE: monotonic
        drift between the printed points assumed)."""
        tol, tc = sel.part.param("tolerance"), sel.part.param("tc")
        if sel.dielectric == "X7R":
            lo = calc_cap(sel).capacitance_worst_pct(1.0, tol.min, tc.min)
            hi = calc_cap(sel).capacitance_worst_pct(1.0, tol.max, tc.max)
            return lo, hi, "tolerance and tc (DS p.2, no DC-bias or aging loss)"
        hot_lo = calc_cap(sel).capacitance_worst_ppm(1.0, tol.min, tc.min, TA_MAX - 25.0)
        hot_hi = calc_cap(sel).capacitance_worst_ppm(1.0, tol.max, tc.max, TA_MAX - 25.0)
        d = sel.part.param("capdrift_m10_c0g")
        lo = min(hot_lo, (1 + tol.min) * (1 + d.min))
        hi = max(hot_hi, (1 + tol.max) * (1 + d.max))
        return lo, hi, "tolerance, 30 ppm/degC to 40 degC and the -10 degC Table A drift row for 5 to 25 degC (ESTIMATE bound)"


    def calc_cap(sel):
        return sel.part.calc  # the selected catalog series (GCM188 or GCM21B)


    C1_LO, C1_HI, C_TXT = cap_factors(SEL_C1)
    CT_LO, CT_HI, _ = cap_factors(SEL_CT)
    CB_LO, CB_HI, CB_TXT = cap_factors(SEL_BST)
    C1_TOL = SEL_C1.part.param("tolerance")
    CAP_SMALL_TXT = (f"GCM188 {SMALL_DIEL} {SEL_C1.tolerance} (`{SEL_C1.mpn}`)"
                     + ("" if C_PREF_OK else f"; the preferred {P['c_small_dielectric']} {P['c_small_tolerance']} is NOT in the "
                        "GCM188 catalog, X7R used instead"))

    # ---- resistors (ERJ-3EKF): relative worst-case factors over the ambient range ----
    ERJ_TREF_TOL, ERJ_TREF_TCR = 20.0, 25.0  # degC: the tolerance row is measured at 20 degC, the TCR test starts at 25 degC


    def erj_r_worst(r0, tol, tcr, t_c):
        """Not a DS equation: ERJ-3EKF worst-case resistance: tolerance referenced to 20 degC, TCR referenced to 25 degC."""
        return erj.calc.resistance_worst(r0, tol, 0.0, t_c, ERJ_TREF_TOL) * (1.0 + tcr * (t_c - ERJ_TREF_TCR))


    _rf = wca(erj_r_worst, unit="ohm", r0=1.0, tol=erj.param("tol"), tcr=erj.param("tcr_10r_1m"), t_c=TA)
    RF_LO, RF_HI = _rf.min, _rf.max  # relative factors (ohm per ohm nominal), tolerance + TCR over TA 5 to 40 degC
    BOL_ERJ = erj.param("perf_soldering_heat").max  # +/-1 % test limit: sensitivity only (section 4)
    # total ladder resistance window: the divider current at the bench bus stays inside its window over the resistor tolerance
    RT_LO = VBUS_BENCH / (P["i_div_max_ua"] * 1e-6 * RF_LO)
    RT_HI = VBUS_BENCH / (P["i_div_min_ua"] * 1e-6 * RF_HI)

    # ---- shunt model ----
    TOL_SH = shl.tolerance(CODE)  # Param, fraction
    TCR_SH = shl.tcr(R0)  # Param, 1/degC (row whose resistance range holds the value)
    BOLP = shl.param("perf_solder_heat").max
    BOLO = shl.param("perf_offset_ohm").max
    P70 = shl.param("p70").max
    THETA = (shl.param("derate_zero").typ - shl.param("derate_knee").typ) / P70  # K/W, ESTIMATE from the derating graph
    assert shl.param("res_range").min <= R0 <= shl.param("res_range").max, "rshunt outside the WSK2512-LOW range"
    MPN_SHUNT = shl.calc.mpn(R0, CODE)
    RATED_SH = shl.calc.rated_power(ta_c=TA_MAX, **shl.inputs("rated_power")) * DERATE


    def r_hi(t, bolp=BOLP, bolo=BOLO):
        """Highest shunt resistance at element temperature t: tolerance, TCR (sign chosen to maximise), solder BOL. Not a DS equation."""
        s = 1.0 if t >= T_REF else -1.0
        return shl.calc.resistance_worst(R0, TOL_SH.max, s * TCR_SH.max, t, T_REF) + R0 * bolp + bolo


    def r_lo(t, bolp=BOLP, bolo=BOLO):
        s = 1.0 if t >= T_REF else -1.0
        return shl.calc.resistance_worst(R0, TOL_SH.min, s * TCR_SH.min, t, T_REF) - R0 * bolp - bolo


    def sh_elem(i, ta, rfun=None):
        """Element temperature at current i and ambient ta: x = THETA I^2 R(ta + x), fixed point (ESTIMATE, THETA from the curve)."""
        rf = rfun or r_hi
        x = 0.0
        for _ in range(400):
            xn = THETA * i * i * rf(ta + x)
            if abs(xn - x) < 1e-9:
                x = xn
                break
            x = xn
        return ta + x


    def trip_i_min(vmin, ta):
        """Lowest current at which V(CS) can reach V(SCP) min: highest R at the self-heated element."""
        i = vmin / r_hi(ta)
        for _ in range(400):
            i_new = vmin / r_hi(sh_elem(i, ta))
            if abs(i_new - i) < 1e-12:
                break
            i = i_new
        return i


    def trip_i_max(vmax, ta):
        """Highest current at which V(CS) stays below V(SCP) max: lowest R at its self-heated element (low-R heating)."""
        i = vmax / r_lo(ta)
        for _ in range(400):
            i_new = vmax / r_lo(sh_elem(i, ta, r_lo))
            if abs(i_new - i) < 1e-12:
                break
            i = i_new
        return i


    # ---- FET model (ESTIMATE where noted) ----
    TJMAX = fet.param("tj_tstg").max
    RTHJA = fet.param("rth_ja").max  # K/W at 6 cm2 copper (a LAYOUT REQUIREMENT, DS p.4)
    RTHJC = fet.param("rth_jc_bottom").max
    RDS10 = fet.param("rds_on_10v").max  # bounded, 25 degC, VGS 10 V, 50 A
    VGS_ON_MIN = tps.param("v_bst_src_on").min  # V, minimum gate drive
    NORM = [(25.0, fet.param("norm_rds_25c").typ), (75.0, fet.param("norm_rds_75c").typ), (100.0, fet.param("norm_rds_100c").typ),
            (125.0, fet.param("norm_rds_125c").typ), (150.0, fet.param("norm_rds_150c").typ), (175.0, fet.param("norm_rds_175c").typ)]
    G25 = fet.param("rds_tj25_vgs8p24v_id50a").typ / fet.param("rds_tj25_vgs10v_id50a").typ
    G175 = fet.param("rds_tj175_vgs8p24v_id50a").typ / fet.param("rds_tj175_vgs10v_id50a").typ
    ZT_T = np.array([1e-5, 1e-4, 1e-3, 1e-2, 1e-1])
    ZT_Z = np.array([fet.param(f"zthjc_single_{k}").typ for k in ("10us", "100us", "1ms", "10ms", "100ms")])
    assert abs(fet.param("vgs_th").typ - 3.15) < 1e-9


    def rds8(tj):
        """Rds(on) at the minimum gate drive 8.24 V and junction temperature tj. ESTIMATE, not a DS equation: the bounded 10 V
        row `rds_on_10v` max x the Tj curve (Diagram 10, typical) x the typical-ratio g = Rds(8.24 V)/Rds(10 V) interpolated
        between 25 and 175 degC. Only the 10 V maximum is a bound."""
        tc = min(max(tj, 25.0), 175.0)
        g = G25 + (G175 - G25) * (tc - 25.0) / 150.0
        xs, ys = zip(*NORM)
        return RDS10 * float(np.interp(tj, xs, ys)) * g


    def tj_steady(i, ta):
        """Self-consistent Tj = ta + RthJA I^2 Rds8(Tj) (bisection). ESTIMATE through rds8."""
        f = lambda t: ta + RTHJA * i * i * rds8(t) - t
        lo, hi = ta, 1000.0
        if f(lo) <= 0:
            return lo
        for _ in range(200):
            mid = 0.5 * (lo + hi)
            lo, hi = (mid, hi) if f(mid) > 0 else (lo, mid)
        return 0.5 * (lo + hi)


    def zth_v(t):
        """ZthJC step response (ESTIMATE, DS Diagram 5 single-pulse curve rows): log-log between rows; below 10 us the 10 us
        value; from 100 ms the RthJC max. Vectorised."""
        t = np.asarray(t, float)
        out = np.zeros_like(t)
        pos = t > 0
        tt = np.clip(t[pos], ZT_T[0], None)
        z = np.exp(np.interp(np.log(tt), np.log(ZT_T), np.log(ZT_Z)))
        out[pos] = np.where(tt >= ZT_T[-1], RTHJC, z)
        return out


    # ---- unit helpers ----
    def mv(x):
        return f"{x * 1e3:.4g}"


    def pct(x):
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


    def est_status(margin):
        return "ESTIMATE, " + ("PASS" if margin >= 0 else "FAIL")


    SUMMARY, VALUES, TEXT = [], {}, {}


    def summ(key, label, value, result, limit, margin, stat):
        """One summary row and its values.json entry: key -> numeric value (or None), text -> pre-formatted result."""
        SUMMARY.append([f"`{key}`", label, result, limit, margin, stat])
        VALUES[key] = value
        TEXT[key] = result


    # %% [markdown]
    # ## Chart styling
    # Palette: the dataviz reference categorical palette (same as the INA229HighSideMonitor report).

    # %%
    INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e4e0"
    BLUE, ORANGE, GREEN, RED = "#2a78d6", "#eb6834", "#008300", "#e34948"


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
        fig.savefig(FIG_DIR / name, dpi=130, facecolor="white")
        plt.close(fig)


    # %% [markdown]
    # ## 4. UVLO/OV divider
    # The ladder is BUS_IN - r_top - EN/UVLO - r_mid - OV - r_bot - GND (DS Fig 7-5 p.16). Trips come from the library
    # equations 12 and 13 solved with the resistor ladder and the EN/UVLO and OV pin leakage (a nodal solve, not a DS
    # equation, because the leakage is part of the node currents). All corners: threshold min and max, each resistor at its
    # tolerance + TCR limit, each leakage at both signs.

    # %%
    UVR, UVF, OVR, OVF = (tps.param(k) for k in ("v_uvlor", "v_uvlof", "v_ovr", "v_ovf"))
    I_EN, I_OV = tps.param("i_en").max, tps.param("i_ov").max  # no MIN printed: used at both signs and at all VIN (conservative)


    def nodes(vin, r1, r2, r3, iu, io):
        """KCL at the EN/UVLO and OV nodes (iu, io: current drawn out of the node into the pin). Arrays allowed."""
        a, b, c = 1 / r1, 1 / r2, 1 / r3
        m11, m12, m21, m22 = a + b, -b, b, -(b + c)
        q1, q2 = a * vin - iu, io
        det = m11 * m22 - m12 * m21
        return (q1 * m22 - m12 * q2) / det, (m11 * q2 - m21 * q1) / det


    def trip_vin(vth, node, r, iu, io):
        v0, v1 = nodes(0.0, *r, iu, io), nodes(1.0, *r, iu, io)
        k = 0 if node == "u" else 1
        return (vth - v0[k]) / (v1[k] - v0[k])


    def windows(r1, r2, r3, f_lo=None, f_hi=None, bol=None):
        """(min, max) of the four trips over all corners; r1..r3 may be numpy arrays (candidates)."""
        bol = BOL_ERJ if bol is None else bol  # ERJ-3EKF solder-heat BOL (both signs) is part of every judged window
        f_lo = (RF_LO - bol) if f_lo is None else f_lo
        f_hi = (RF_HI + bol) if f_hi is None else f_hi
        r1, r2, r3 = (np.asarray(x, float) for x in (r1, r2, r3))
        acc = {k: [None, None] for k in ("uvr", "uvf", "ovr", "ovf")}
        ths = {"uvr": ("u", UVR), "uvf": ("u", UVF), "ovr": ("o", OVR), "ovf": ("o", OVF)}
        for f in itertools.product((f_lo, f_hi), repeat=3):
            r = (r1 * f[0], r2 * f[1], r3 * f[2])
            for su, so in itertools.product((-1, 1), repeat=2):
                for name, (node, par) in ths.items():
                    for vth in (par.min, par.max):
                        v = trip_vin(vth, node, r, su * I_EN, so * I_OV)
                        lo, hi = acc[name]
                        acc[name][0] = v if lo is None else np.minimum(lo, v)
                        acc[name][1] = v if hi is None else np.maximum(hi, v)
        return acc


    def e96_values(lo, hi):
        out = set()
        for d in range(-1, 8):
            for m in series.E96:
                v = round(m * 10.0 ** d, 6)
                if lo <= v <= hi:
                    out.add(v)
        return np.array(sorted(out))


    def select_divider():
        """E96 triple (r_top, r_mid, r_bot): feasible = UVLO rising max <= UVLO_LIMIT and OV rising min >= OV_MIN over all
        corners, total R giving 10 to 20 uA at the bench bus. Rule: maximise the UVLO falling minimum (turn-off as close below
        the turn-on as the hysteresis allows, compared at 0.1 V resolution), then minimise the OV rising maximum. The nominal-trip window below only prunes
        candidates that cannot be feasible (nominal UVLO rising above the limit or nominal OV rising below OV_MIN)."""
        vals = e96_values(1e3, 1e6)
        cands = []
        for r3 in vals:
            r2g, r1g = np.meshgrid(vals, vals, indexing="ij")
            rt = r1g + r2g + r3
            uvr_nom = UVR.typ * rt / (r2g + r3)
            ov_nom = OVR.typ * rt / r3
            m = (rt >= RT_LO) & (rt <= RT_HI) & (uvr_nom <= UVLO_LIMIT) & (uvr_nom >= UVLO_LIMIT - 3.0) & (ov_nom >= OV_MIN) \
                & (ov_nom <= OV_MIN + 4.0)
            for i, j in zip(*np.nonzero(m)):
                cands.append((r1g[i, j], r2g[i, j], r3))
        arr = np.array(cands)
        w = windows(arr[:, 0], arr[:, 1], arr[:, 2])
        ok = (w["uvr"][1] <= UVLO_LIMIT) & (w["ovr"][0] >= OV_MIN)
        idx = np.nonzero(ok)[0]
        if len(idx) == 0:
            k = int(np.argmax(np.minimum(UVLO_LIMIT - w['uvr'][1], w['ovr'][0] - OV_MIN)))
            raise SystemExit(f'no E96 triple meets the limits; closest {arr[k]}: UVLO rising max {w["uvr"][1][k]:.3f} V, OV rising min {w["ovr"][0][k]:.3f} V')
        order = sorted(idx, key=lambda i: (-round(float(w["uvf"][0][i]), 1), float(w["ovr"][1][i])))
        best = arr[order[0]]
        return tuple(float(x) for x in best), len(arr), len(idx)


    SEL_DIV, N_CAND, N_FEAS = select_divider()
    DIV_MATCH = all(abs(a - b) < 1e-6 * b for a, b in zip(R_DIV, SEL_DIV))
    RT_DIV = sum(R_DIV)
    W = {k: (float(v[0][0]), float(v[1][0])) for k, v in windows(*[[x] for x in R_DIV]).items()}
    W_BOL = {k: (float(v[0][0]), float(v[1][0])) for k, v in windows(*[[x] for x in R_DIV], bol=0.0).items()}  # WITHOUT BOL: information
    UVR_MAX, OVR_MIN = W["uvr"][1], W["ovr"][0]
    UVLO_MARGIN_RES = UVLO_LIMIT - UVR_MAX  # margin to the 10.6 V limit
    OV_MARGIN_RES = OVR_MIN - OV_MIN
    TURN_ON_MARGIN = VBUS_MIN - UVR_MAX  # margin to the 10.8 V minimum bus (required >= UVLO_MARGIN)
    I_DIV_NOM = VBUS_BENCH / RT_DIV
    I_DIV_LO, I_DIV_HI = VBUS_BENCH / (RT_DIV * RF_HI), VBUS_BENCH / (RT_DIV * RF_LO)


    def pin_voltages(vbus):
        """EN/UVLO and OV node voltages and R_top dissipation at a bus voltage, over all corners and leakage signs."""
        vu, vo, pr = [], [], []
        for f in itertools.product((RF_LO, RF_HI), repeat=3):
            r = tuple(x * y for x, y in zip(R_DIV, f))
            for su, so in itertools.product((-1, 1), repeat=2):
                a, b = nodes(vbus, *r, su * I_EN, so * I_OV)
                vu.append(a)
                vo.append(b)
                pr.append(((vbus - a) ** 2 / r[0], vbus - a))
        return (min(vu), max(vu)), (min(vo), max(vo)), max(p[0] for p in pr), max(p[1] for p in pr)


    PIN40 = pin_voltages(VBUS_HI)
    PIN12 = pin_voltages(VBUS_BENCH)

    # %% [markdown]
    # ## 5. Trip window and soft shorts

    # %%
    RISCP_ROWS = {500.0: "v_scp_r500", 10500.0: "v_scp_r10k5", 28000.0: "v_scp_r28k", 140500.0: "v_scp_r140k5"}
    assert RISCP in RISCP_ROWS, "riscp_ohm has no library V(SCP) row (500, 10.5k, 28k, 140.5k ohm)"
    VSCP = tps.param(RISCP_ROWS[RISCP])
    # V: V(SCP) slope 2 uA x R (DS eq. 5) x the resistor error, each side with its own corner; not a bound on the row
    VSCP_MIN = VSCP.min - 2e-6 * RISCP * (1.0 - RF_LO)
    VSCP_MAX = VSCP.max + 2e-6 * RISCP * (RF_HI - 1.0)
    DV_RISCP = max(VSCP.min - VSCP_MIN, VSCP_MAX - VSCP.max)  # V, the larger side, for the report text
    TA_GRID = [TA_MIN + (TA_MAX - TA_MIN) * k / 14 for k in range(15)]
    TRIP_MIN = min(trip_i_min(VSCP_MIN, ta) for ta in TA_GRID)
    TRIP_MIN_TA = min(TA_GRID, key=lambda ta: trip_i_min(VSCP_MIN, ta))
    TRIP_MAX = max(trip_i_max(VSCP_MAX, ta) for ta in TA_GRID)
    TRIP_MAX_TA = max(TA_GRID, key=lambda ta: trip_i_max(VSCP_MAX, ta))
    TRIP_MIN_ELEM = sh_elem(TRIP_MIN, TRIP_MIN_TA)
    TRIP_MAX_ELEM = sh_elem(TRIP_MAX, TRIP_MAX_TA, r_lo)
    TRIP_NOM = VSCP.typ / R0  # not a bound: nominal
    TRIP_MARGIN = TRIP_MIN - I_DES
    # highest non-tripping current: the highest current that some unit does not trip on (low-R corner, V(SCP) max)
    I_NT = TRIP_MAX
    TE_NT = sh_elem(I_NT, TA_MAX)
    P_NT_JUDGED = I_NT ** 2 * r_hi(TE_NT)  # worst-case R_hi at the self-heated element (a bound mixing corners)
    TE_NT_LO = sh_elem(I_NT, TA_MAX, r_lo)
    P_NT_SAME = I_NT ** 2 * r_lo(TE_NT_LO)  # one unit: the low-R unit that does not trip at I_NT
    TJ_NT = tj_steady(I_NT, TA_MAX)
    RDS_NT = rds8(TJ_NT)
    SOFT_P_MARGIN = RATED_SH - P_NT_JUDGED
    SOFT_TJ_MARGIN = TJMAX - TJ_NT
    TE_NT_MARGIN = T_ELEM_LIMIT - TE_NT

    # %% [markdown]
    # ## 6. Steady state at the design current

    # %%
    TJ_DES = {ta: tj_steady(I_DES, ta) for ta in (TA_MIN, TA_MAX)}
    RDS_DES = rds8(TJ_DES[TA_MAX])
    P_FET_DES = I_DES ** 2 * RDS_DES
    TC_DES = TJ_DES[TA_MAX] - P_FET_DES * RTHJC  # case temperature at the steady point (max ambient)
    TE_DES = {ta: sh_elem(I_DES, ta) for ta in (TA_MIN, TA_MAX)}
    R_SH_DES = {ta: r_hi(TE_DES[ta]) for ta in (TA_MIN, TA_MAX)}
    P_SH_DES = {ta: I_DES ** 2 * R_SH_DES[ta] for ta in (TA_MIN, TA_MAX)}
    V_SH_DES_MAX = max(I_DES * R_SH_DES[ta] for ta in R_SH_DES)
    MWV = shl.calc.max_working_voltage(RATED_SH, r_lo(TA_MAX))  # lowest rated working voltage (lowest R)
    DROP = {ta: I_DES * (R_SH_DES[ta] + rds8(TJ_DES[ta])) for ta in (TA_MIN, TA_MAX)}
    TE_DES_MARGIN = T_ELEM_LIMIT - TE_DES[TA_MAX]
    P_SH_MARGIN = RATED_SH - P_SH_DES[TA_MAX]
    TJ_DES_MARGIN = TJMAX - TJ_DES[TA_MAX]

    # TPS quiescent power, junction temperature (rth_ja typical only: ESTIMATE)
    IGND = tps.param("i_gnd_full")
    ISHDN = tps.param("i_shdn")
    RTHJA_TPS = tps.param("rth_ja").typ
    TJ_ABS_TPS = tps.param("tj_abs").max
    P_TPS = VBUS_HI * IGND.max + tps.param("v_scp_test_h").max * 0.0  # W at the highest bus; FLT sink below
    R_FPU_LO, R_FPU_HI = R_FPU * RF_LO, R_FPU * RF_HI
    R_PPU_LO = R_PPU * RF_LO
    I_FPU_MAX, I_PPU_MAX = V3V3_MAX / R_FPU_LO, V3V3_MAX / R_PPU_LO
    VOL_FLT = tps.param("r_flt_pd").max * I_FPU_MAX  # V, FLT low level (R_pd max x sink current)
    P_TPS_TOT = P_TPS + VOL_FLT * I_FPU_MAX + tps.param("r_flt_pd").max * I_PPU_MAX ** 2
    TJ_TPS = TA_MAX + P_TPS_TOT * RTHJA_TPS
    TJ_TPS_MARGIN = TJ_ABS_TPS - TJ_TPS

    # %% [markdown]
    # ## 7. Turn-on

    # %%
    VBST = Param(min=tps.param("v_bst_src_on").min, typ=tps.param("v_bst_src_on").typ, max=tps.param("v_bst_src_off").max, unit="V")  # full-scope charge-pump window: ON min to OFF max (DS p.6); `v_bst_src_3v5` is a VS = 3.5 V test row, not used
    R1_LO, R1_HI = R1G * RF_LO, R1G * RF_HI


    def slew_rate(vbst, c1, r1):
        """Gate ramp slew rate dV/dt of BUS_OUT: DS eq. 3 solved for IINRUSH / CLOAD: 0.63 V(BST-SRC) / (R1 C1)."""
        return 0.63 * vbst / (r1 * c1)


    C1_N, C1_MIN, C1_MAX = C1G, C1G * C1_LO, C1G * C1_HI
    SLEW = {"slow": slew_rate(VBST.min, C1_MAX, R1_HI), "typ": slew_rate(VBST.typ, C1_N, R1G), "fast": slew_rate(VBST.max, C1_MIN, R1_LO)}
    INRUSH = {k: CL * s for k, s in SLEW.items()}
    INRUSH_NOTOL = {"min": CL * slew_rate(VBST.min, C1_N, R1G), "typ": CL * slew_rate(VBST.typ, C1_N, R1G),
                    "max": CL * slew_rate(VBST.max, C1_N, R1G)}

    SOA_LAY125 = [(1e-6, "1us", [12, 20, 40, 50, 80]), (1e-5, "10us", [20, 40, 80]), (1e-4, "100us", [12, 40, 50, 80]),
                  (1e-3, "1ms", [12, 40, 50, 80]), (1e-2, "10ms", [12, 20, 40, 50, 80]), (1e-1, "100ms", [12, 20, 40, 50, 80]),
                  (math.inf, "dc", [12, 20, 40, 50, 80])]
    SOA_LAY25 = [(1e-6, "1us", [12, 20, 40, 50, 80]), (1e-5, "10us", [50, 80]), (1e-4, "100us", [20, 40, 50, 80]),
                 (1e-3, "1ms", [20, 40, 50, 80]), (1e-2, "10ms", [20, 40, 50, 80])]
    SOA125 = {lab: [(float(v), fet.param(f"soa_tc125_{lab}_{v}v").typ) for v in vs] for _, lab, vs in SOA_LAY125}
    SOA25 = {lab: [(float(v), fet.param(f"soa_tc25_{lab}_{v}v").typ) for v in vs] for _, lab, vs in SOA_LAY25}


    def loglog(pts, x):
        xs = [p[0] for p in pts]
        i = min(max(int(np.searchsorted(xs, x, side="right")) - 1, 0), len(xs) - 2)
        (x0, y0), (x1, y1) = pts[i], pts[i + 1]
        return y0 * (x / x0) ** (math.log(y1 / y0) / math.log(x1 / x0))


    def soa_rect(table, lay, vds, tp):
        """ID limit (ESTIMATE, curve rows, typical only) at VDS for pulse width tp: log-log in VDS per line, then log-log in tp
        between the bracketing lines that cover VDS. Returns (ID or None, note)."""
        avail = []
        for w, lab, _ in lay:
            pts = table.get(lab)
            if pts is None or w == math.inf or vds < pts[0][0] or vds > pts[-1][0]:
                continue
            avail.append((w, loglog(pts, vds)))
        if not avail:
            return None, "no line covers VDS"
        if tp <= avail[0][0]:
            return avail[0][1], "shorter than the first line: first line used"
        if tp >= avail[-1][0]:
            if tp > 10 * avail[-1][0]:
                return None, "pulse more than a decade longer than the longest line covering VDS: not applicable"
            return avail[-1][1], "within a decade beyond the last finite line: that line used"
        for (w0, y0), (w1, y1) in zip(avail, avail[1:]):
            if w0 <= tp <= w1:
                return loglog([(w0, y0), (w1, y1)], tp), "log-log between lines"
        raise AssertionError


    def ramp_power(vb, s, load, t):
        """Power and FET current during the ramp (vectorised in t): constant-current or resistive load from t = 0."""
        t = np.asarray(t, float)
        vout = np.minimum(s * t, vb)
        iload = I_DES * np.ones_like(t) if load == "CC" else I_DES * vout / vb
        i = iload + CL * s
        return (vb - vout) * i, i


    def run_case(vb, s, load, base, n=200, m=200):
        """Causal superposition Tj(t) = base + sum dP_k ZthJC(t - t_k) (ESTIMATE; step response = single-pulse curve). Ramp: P per
        step is the maximum of its end values. After the ramp the FET carries the design current: P = I^2 Rds8(Tj)."""
        T = vb / s
        edges = np.linspace(0.0, T, n + 1)
        p_edge, _ = ramp_power(vb, s, load, edges)
        p_step = np.maximum(p_edge[:-1], p_edge[1:])
        ts = list(edges[:-1])
        dp = list(np.diff(np.concatenate([[0.0], p_step])))
        tj_pk, pprev, tj = base, p_step[-1], base
        for k in range(n):
            tj = base + float(np.dot(dp[: k + 1], zth_v(edges[k + 1] - np.array(ts[: k + 1]))))
            tj_pk = max(tj_pk, tj)
        tprev = T
        for k in range(1, m + 1):
            t1 = T + 1e-6 * (1e6 ** (k / m))
            pss = I_DES ** 2 * rds8(tj)
            ts.append(tprev)
            dp.append(pss - pprev)
            pprev = pss
            tj = base + float(np.dot(dp, zth_v(t1 - np.array(ts))))
            tj_pk = max(tj_pk, tj)
            tprev = t1
        return tj_pk, float(p_step.max())


    def evaluate(vb, s, load, base=TA_MAX):
        T = vb / s
        tj, pmax = run_case(vb, s, load, base)
        ts_ = np.linspace(0, T, 4001)
        pk, ik = ramp_power(vb, s, load, ts_)
        pmax = max(pmax, float(pk.max()))
        ieq = pmax / vb
        r125, n125 = soa_rect(SOA125, SOA_LAY125, vb, T)
        r25, n25 = soa_rect(SOA25, SOA_LAY25, vb, T)
        energy = float(np.sum(0.5 * (pk[:-1] + pk[1:]) * np.diff(ts_)))
        ok_tj, ok_soa = tj <= TJMAX, (r125 is not None) and ieq <= r125
        return dict(vb=vb, s=s, T=T, tj=tj, pmax=pmax, ieq=ieq, r125=r125, r25=r25, ok_tj=ok_tj, ok_soa=ok_soa,
                    ok_soa25=(r25 is not None) and ieq <= r25, ipk=float(ik.max()), e=energy, ok=ok_tj and ok_soa)


    T_DRV_NOMINAL = calc.t_drv_en(CBST, tps.param("v_bst_uvlor").max, tps.param("i_bst").min)  # nominal CBST, worst UVLO and I(BST)
    # eq. 1 range: min = CBST low corner, V(BST_UVLOR) min, I(BST) max; max = CBST high corner, V(BST_UVLOR) max, I(BST) min.
    # DC-bias loss (not bounded) lowers CBST and so lowers the minimum: the minimum is not a bound (open item).
    T_DRV_MIN = calc.t_drv_en(CBST * CB_LO, tps.param("v_bst_uvlor").min, tps.param("i_bst").max)
    T_DRV_MAX = calc.t_drv_en(CBST * CB_HI, tps.param("v_bst_uvlor").max, tps.param("i_bst").min)
    T_DRV_WORST = T_DRV_MAX

    CORNERS = {"slow ramp (V(BST-SRC) min, C1 max, R1 max)": SLEW["slow"], "fast ramp (V(BST-SRC) max, C1 min, R1 min)": SLEW["fast"]}
    TURN = {}
    for load in ("CC", "R"):
        for cname, s in CORNERS.items():
            for vb in (VBUS_LO, VBUS_HI):
                TURN[(load, cname, vb)] = evaluate(vb, s, load)
    TC_HOT = TC_DES  # restart with the case at the steady-state temperature (sensitivity)
    TURN_HOT = {(load, cname, vb): evaluate(vb, s, load, TC_HOT)
                for load in ("CC", "R") for cname, s in CORNERS.items() for vb in (VBUS_LO, VBUS_HI)}
    GRID_V = [VBUS_LO + 0.5 * i for i in range(int((80.0 - VBUS_LO) / 0.5) + 1)]  # to 80 V, the highest SOA row VDS
    SWEEP = {}
    for load in ("CC", "R"):
        for cname, s in CORNERS.items():
            SWEEP[(load, cname)] = [evaluate(v, s, load) for v in GRID_V]


    def largest_pass(rows):
        """Largest grid VBUS such that every grid point from VBUS_LO up to it passes (contiguous). None if the first fails."""
        last = None
        for r in rows:
            if not r["ok"]:
                break
            last = r["vb"]
        return last


    PASS_V = {k: largest_pass(v) for k, v in SWEEP.items()}
    V_PASS_ALL = min(v for v in PASS_V.values() if v is not None) if all(v is not None for v in PASS_V.values()) else None
    MONO = all(not (r["ok"] and not prev["ok"]) for rows in SWEEP.values() for prev, r in zip(rows, rows[1:]))
    TURN_OK = all(r["ok"] for r in TURN.values())
    TURN_HOT_OK = all(r["ok"] for r in TURN_HOT.values())
    TJ_TURN_MAX = max(r["tj"] for r in TURN.values())
    SOA_RATIO = max(r["ieq"] / r["r125"] for r in TURN.values())
    TURN_TJ_MARGIN = TJMAX - TJ_TURN_MAX

    # %% [markdown]
    # ## 8. Short circuit and turn-off

    # %%
    ITMR = tps.param("i_tmr_src_cb")
    VTMR = tps.param("v_tmr_sc")


    def tsc_eq8(c, r, i):
        """Latch-off delay tSC from DS eq. 8, solved for tSC: eq. 8 is linear in tSC, so tSC = C / ctmr_latch(tSC = 1 s)."""
        return c / calc.ctmr_latch(1.0, r, i)


    def tsc_ext(c, r, i, v):
        """Not a DS equation: eq. 8 with the TMR threshold V(TMR_SC) as a variable instead of the printed 1.1 V."""
        return c * r * math.log(1.0 / (1.0 - v / (r * i)))


    TSC_CS = [(CTMR * f, RTMR * rf, i) for f in (CT_LO, CT_HI) for rf in (RF_LO, RF_HI) for i in (ITMR.min, ITMR.max)]
    TSC_EQ8 = (min(tsc_eq8(*c) for c in TSC_CS), max(tsc_eq8(*c) for c in TSC_CS))
    TSC_NOM = tsc_eq8(CTMR, RTMR, ITMR.typ)
    TSC_EXT = (min(tsc_ext(c, r, i, VTMR.min) for c, r, i in TSC_CS), max(tsc_ext(c, r, i, VTMR.max) for c, r, i in TSC_CS))
    T_OFF_DELAY = tps.param("t_sc_hard").max  # s, comparator to PD falling (C(TMR) open): turn-off delay assumed after the timer
    T_FAULT = TSC_EXT[1] + T_OFF_DELAY  # s, longest time at the fault current (bounding)
    TJ0_SOFT = TJ_NT  # FET Tj before the fault if it follows a sustained soft short (highest non-tripping current)
    TJ0_DES = TJ_DES[TA_MAX]


    def tj_fault_end(i, z, tj0):
        """Tj at the end of the fault: Tj = Tj0 + I^2 Z Rds8(Tj) (fixed point, damped). FET assumed fully enhanced (ESTIMATE)."""
        t = tj0
        for _ in range(3000):
            tn = tj0 + i * i * z * rds8(t)
            if tn > 2000.0 or abs(tn - t) < 1e-9:
                return tn
            t = 0.5 * (t + tn)
        return t


    Z_FAULT = float(zth_v([T_FAULT])[0])
    TJ_FAULT = {"design": tj_fault_end(I_FAULT, Z_FAULT, TJ0_DES), "soft": tj_fault_end(I_FAULT, Z_FAULT, TJ0_SOFT)}


    def bisect_max(pred, lo=0.0, hi=5000.0):
        for _ in range(100):
            mid = 0.5 * (lo + hi)
            lo, hi = (mid, hi) if pred(mid) else (lo, mid)
        return lo


    I_FAULT_LIMIT = bisect_max(lambda i: tj_fault_end(i, Z_FAULT, TJ0_SOFT) <= TJMAX)
    IDP = fet.param("id_pulse").max
    IDP_MARGIN = IDP - I_FAULT
    R_SH_FAULT = r_hi(TA_MAX)  # no self-heating during the fault, highest R at 40 degC
    P_SH_FAULT = I_FAULT ** 2 * R_SH_FAULT
    E_SH_FAULT = P_SH_FAULT * T_FAULT
    V_CS_FAULT = I_FAULT * R_SH_FAULT
    CS_PAIR = tps.param("abs_cs_pair")
    VBR = fet.param("vbr_dss").min
    EAS = fet.param("eas").max
    VDS_RATING = fet.param("vds_rating").max


    def e_aval(l, i, vb):
        """Not a DS equation: ideal-clamp avalanche energy E = 0.5 L I^2 x V(BR)/(V(BR) - VBUS) (clamp at V(BR) min, loop inductance
        L only; energy stored in the load and PSU capacitance excluded)."""
        return 0.5 * l * i * i * VBR / (VBR - vb)


    E_AV = {(l, i, vb): e_aval(l, i, vb) for l in L_LEAD for i in (I_FAULT, TRIP_MAX) for vb in (VBUS_BENCH, VBUS_HI)}
    E_AV_MAX = max(E_AV.values())
    I_SAT = fet.param("ioutput_tj25_vgs8v_vds5v").typ  # typical only

    # %% [markdown]
    # ## 9. Gate drive and 10. Ratings

    # %%
    VG_ON = tps.param("v_bst_src_on")
    VG_GOOD = tps.param("v_g_good")
    GATE_OVERLAP = VG_GOOD.max - VG_ON.min  # V; positive = the corners overlap
    VBST_OFF = tps.param("v_bst_src_off")
    VBST_REG_MAX = max(VBST_OFF.max, VG_ON.max, VBST.max)  # highest charge-pump voltage in any row
    # C1 returns to GND (DS Fig 7-4 p.15): the gate sits at SRC + V(BST-SRC) when enhanced, so C1 sees the bus maximum
    # plus the charge-pump maximum (eq. 3 / eq. 4 sizing, DS p.16). Not a DS equation: a worst-case sum.
    V_C1_MAX = VBUS_HI + VBST_OFF.max  # V, gate to GND
    # C1 discharge at turn-off or on a hard short (not a DS equation): C1 at V_C1_MAX discharges through R2 into PD when the
    # gate is pulled to SRC. The PD peak sink is printed only as 2 A typical (i_pd_peak, DS p.7): not a bound, so reported.
    R2_MIN = R2G * RF_LO  # ohm, lowest R2 (highest peak current)
    C1_DUMP_PEAK = V_C1_MAX / R2_MIN  # A
    C1_DUMP_PW = V_C1_MAX ** 2 / R2_MIN  # W, peak in R2 (single pulse; ERJ-3EKF has no pulse row: NOT BOUNDED)
    C1_DUMP_E = 0.5 * C1_MAX * V_C1_MAX ** 2  # J, energy in C1 at its maximum capacitance
    C1_DUMP_TAU = R2G * RF_HI * C1_MAX  # s, longest R2 C1 time constant


    def rating_row(name, lo, hi, rating, key, s, u, cite_text=None):
        """Margin = min(lo - rating.min, rating.max - hi); a missing rating bound is unlimited. Returns (row, margin)."""
        r_lo_ = -float("inf") if rating.min is None else rating.min
        r_hi_ = float("inf") if rating.max is None else rating.max
        lo_margin = float("inf") if (lo == 0.0 and r_lo_ == 0.0) else lo - r_lo_  # a GND-referenced 0 V is not a margin
        margin = min(lo_margin, r_hi_ - hi)
        shown = (f"{'-inf' if r_lo_ == -float('inf') else f'{r_lo_ * s:.4g}'} to "
                 f"{'inf' if r_hi_ == float('inf') else f'{r_hi_ * s:.4g}'} {u}")
        return [name, f"{lo * s:.4g} to {hi * s:.4g} {u}", f"`{key}`: {shown}", f"{margin * s:.4g} {u}", status(margin),
                cite_text or cite(rating)], margin


    # %%
    CAP_RATED = SEL_BYP.part.param("rated_v")  # VS and SRC bypass
    CAP_RATED_C1 = SEL_C1.part.param("rated_v")
    CAP_RATED_BST = SEL_BST.part.param("rated_v")
    CBST_MIN_NOM = calc.cbst_min(fet.param("qg").max, DV_BST, C1G)
    CBST_MIN_WC = calc.cbst_min(fet.param("qg").max, DV_BST, C1G * C1_HI)
    CBST_LOW = CBST * CB_LO  # F, lowest value over tolerance and temperature, NO DC-bias or aging loss
    CBST_KEEP = CBST_MIN_WC / CBST_LOW  # fraction of CBST_LOW that must survive DC bias and aging
    CBST_REC = tps.param("rec_cap_bst_src").min
    CBST_ALLOW = 1.0 - CBST_KEEP  # largest DC-bias and aging loss fraction the CBST low corner still tolerates (not a DS equation)
    EXTRA_ROWS = []
    RATINGS = []


    def add_rating(*a, **k):
        row, margin = rating_row(*a, **k)
        RATINGS.append((row, margin))
        return margin


    VS_LO_APPLIED = min(VBUS_MIN, VBUS_LO)
    add_rating(f"BUS_IN at VS, rec (applied {VS_LO_APPLIED:g} to {VBUS_HI:g} V)", VS_LO_APPLIED, VBUS_HI, tps.param("rec_vs"), "rec_vs", 1, "V")
    add_rating("BUS_IN at VS, abs (VS > 0)", VS_LO_APPLIED, VBUS_HI, tps.param("abs_input_vs_pos"), "abs_input_vs_pos", 1, "V")
    add_rating("VS, CS+, CS- to GND, abs", VS_LO_APPLIED, VBUS_HI, tps.param("abs_vs_cs_gnd"), "abs_vs_cs_gnd", 1, "V")
    add_rating("SRC (BUS_OUT) to GND, abs", 0.0, VBUS_HI, tps.param("abs_src_gnd"), "abs_src_gnd", 1, "V")
    add_rating(f"CS+ to CS-, normal ({I_DES:g} A, worst R)", 0.0, V_SH_DES_MAX, CS_PAIR, "abs_cs_pair", 1, "V")
    add_rating(f"CS+ to CS-, fault (+/-{I_FAULT:g} A)", -V_CS_FAULT, V_CS_FAULT, CS_PAIR, "abs_cs_pair", 1, "V")
    add_rating(f"EN/UVLO pin at {VBUS_HI:g} V bus (worst corner), rec", PIN40[0][0], PIN40[0][1], tps.param("rec_en_inp_cs_sel"), "rec_en_inp_cs_sel", 1, "V")
    add_rating(f"EN/UVLO pin at {VBUS_HI:g} V bus, abs", PIN40[0][0], PIN40[0][1], tps.param("abs_input_vs_pos"), "abs_input_vs_pos", 1, "V")
    add_rating(f"OV pin at {VBUS_HI:g} V bus, abs (no OV rec row)", PIN40[1][0], PIN40[1][1], tps.param("abs_input_vs_pos"), "abs_input_vs_pos", 1, "V")
    add_rating(f"EN (INP) logic high (V3V3 max {V3V3_MAX:.3f} V), rec", 0.0, V3V3_MAX, tps.param("rec_en_inp_cs_sel"), "rec_en_inp_cs_sel", 1, "V")
    add_rating(f"FAULT_N / PGOOD_N pins at V3V3 max, rec", 0.0, V3V3_MAX, tps.param("rec_flt"), "rec_flt", 1, "V")
    add_rating(f"FAULT_N / PGOOD_N pins at V3V3 max, abs", 0.0, V3V3_MAX, tps.param("abs_flt_gnd"), "abs_flt_gnd", 1, "V")
    add_rating("FLT sink current (asserted, V3V3 max, pull-up min)", 0.0, max(I_FPU_MAX, I_PPU_MAX), tps.param("abs_i_flt_sink"), "abs_i_flt_sink", 1e3, "mA")
    add_rating("ISCP pin (I(ISCP) max x RISCP max)", 0.0, tps.param("i_iscp").max * RISCP * RF_HI, tps.param("abs_iscp_tmr_scp_test"), "abs_iscp_tmr_scp_test", 1, "V")
    add_rating("TMR pin (V(TMR_SC) max, timer clamp)", 0.0, VTMR.max, tps.param("abs_iscp_tmr_scp_test"), "abs_iscp_tmr_scp_test", 1, "V")
    add_rating("V(BST-SRC) and gate to SRC (highest charge-pump row)", 0.0, VBST_REG_MAX, tps.param("abs_gate_bst_src"), "abs_gate_bst_src", 1, "V")
    add_rating("FET VGS (highest charge-pump row)", 0.0, VBST_REG_MAX, fet.param("vgs"), "vgs", 1, "V")
    add_rating(f"BST, PU, PD to GND ({VBUS_HI:g} V + charge pump)", 0.0, VBUS_HI + VBST_REG_MAX, tps.param("abs_gate_out_gnd"), "abs_gate_out_gnd", 1, "V")
    add_rating(f"FET VDS static ({VBUS_HI:g} V bus)", 0.0, VBUS_HI, fet.param("vds_rating"), "vds_rating", 1, "V")
    add_rating("TA ambient (grade 1)", TA_MIN, TA_MAX, tps.param("ta_grade_1"), "ta_grade_1", 1, "degC")
    add_rating(f"FET drain current, continuous ({I_DES:g} A, PCB rating at 25 degC)", 0.0, I_DES, fet.param("id_pcb"), "id_pcb", 1, "A")
    add_rating(f"FET drain current, fault ({I_FAULT:g} A)", 0.0, I_FAULT, fet.param("id_pulse"), "id_pulse", 1, "A")
    add_rating(f"6.8 nF C1, gate to GND at {V_C1_MAX:g} V (bus max + V(BST-SRC) max)", 0.0, V_C1_MAX, CAP_RATED_C1, "rated_v", 1, "V")
    add_rating(f"470 nF CBST, BST to SRC at {VBST_REG_MAX:g} V (highest charge-pump row)", 0.0, VBST_REG_MAX, CAP_RATED_BST, "rated_v", 1, "V")
    add_rating(f"VS and SRC bypass capacitors ({VBUS_HI:g} V, DC bias loss not bounded)", 0.0, VBUS_HI, CAP_RATED, "rated_v", 1, "V")
    add_rating(f"ERJ-3EKF, R_top voltage at {VBUS_HI:g} V (limiting element voltage)", 0.0, PIN40[3], erj.param("v_limit"), "v_limit", 1, "V")
    add_rating(f"ERJ-3EKF, R_top power at {VBUS_HI:g} V (70 degC rating)", 0.0, PIN40[2], erj.param("p70"), "p70", 1e3, "mW")
    RATINGS.append((["Junction temperature, TPS48000 (ESTIMATE, rth_ja typical)", f"{TJ_TPS:.4g} degC", f"`tj_abs` {TJ_ABS_TPS:g} degC max",
                     f"{TJ_TPS_MARGIN:.4g} degC", est_status(TJ_TPS_MARGIN), cite(tps.param("tj_abs"))], TJ_TPS_MARGIN))
    RATINGS.append((["Junction temperature, FET at the design current (ESTIMATE)", f"{TJ_DES[TA_MAX]:.4g} degC", f"`tj_tstg` {TJMAX:g} degC max",
                     f"{TJ_DES_MARGIN:.4g} degC", est_status(TJ_DES_MARGIN), cite(fet.param("tj_tstg"))], TJ_DES_MARGIN))
    RATINGS.append((["Shunt dissipation at the design current vs rating (ESTIMATE element)", f"{P_SH_DES[TA_MAX]:.4g} W", f"p70 x derate: {RATED_SH:.4g} W",
                     f"{P_SH_MARGIN:.4g} W", status(P_SH_MARGIN), cite(shl.param("p70"))], P_SH_MARGIN))
    RATINGS.append((["Shunt sense voltage vs (P x R)^1/2 (lowest R)", f"{V_SH_DES_MAX * 1e3:.4g} mV", f"(P x R)^1/2 = {MWV * 1e3:.4g} mV",
                     f"{(MWV - V_SH_DES_MAX) * 1e3:.4g} mV", status(MWV - V_SH_DES_MAX), cite(shl.param("max_working_voltage"))], MWV - V_SH_DES_MAX))
    RATINGS.append((["V(BST-SRC) ON min vs V(G_GOOD) max (gate-drive overlap, accepted residual risk)", f"{VG_ON.min:.4g} V (min)", f"`v_g_good` max {VG_GOOD.max:.4g} V",
                     f"{-GATE_OVERLAP:.4g} V", "FAIL (accepted residual risk, OPEN ITEM)", f"{cite(VG_ON)}; {cite(VG_GOOD)}"], -GATE_OVERLAP))
    RATING_MIN = min(m for row, m in RATINGS if "overlap" not in row[0])
    RATING_FAIL = [row[0] for row, m in RATINGS if m < 0 and "overlap" not in row[0]]

    # %% [markdown]
    # ## Summary values

    # %%
    summ("uvlo_rise_max_v", "UVLO rising trip max (turn-on guaranteed below)", UVR_MAX, f"{UVR_MAX:.3f} V", f"<= {UVLO_LIMIT:g} V ({VBUS_MIN:g} - {UVLO_MARGIN:g})",
         f"{UVLO_MARGIN_RES:+.3f} V", status(UVLO_MARGIN_RES))
    summ("uvlo_turnon_margin_v", "Turn-on margin to the minimum bus", TURN_ON_MARGIN, f"{TURN_ON_MARGIN:.3f} V", f">= {UVLO_MARGIN:g} V", f"{TURN_ON_MARGIN - UVLO_MARGIN:+.3f} V",
         status(TURN_ON_MARGIN - UVLO_MARGIN))
    summ("ov_rise_min_v", "OV rising trip min", OVR_MIN, f"{OVR_MIN:.3f} V", f">= {OV_MIN:g} V", f"{OV_MARGIN_RES:+.3f} V", status(OV_MARGIN_RES))
    summ("divider_current_ua", "Divider current at the bench bus (worst-case range)", I_DIV_NOM * 1e6, f"{I_DIV_LO * 1e6:.2f} to {I_DIV_HI * 1e6:.2f} uA",
         f"{P['i_div_min_ua']:g} to {P['i_div_max_ua']:g} uA", f"{min(I_DIV_LO * 1e6 - P['i_div_min_ua'], P['i_div_max_ua'] - I_DIV_HI * 1e6):+.2f} uA", status(min(I_DIV_LO * 1e6 - P['i_div_min_ua'], P['i_div_max_ua'] - I_DIV_HI * 1e6)))
    summ("en_pin_40v_v", f"EN/UVLO pin at {VBUS_HI:g} V bus (max)", PIN40[0][1], f"{PIN40[0][1]:.3f} V", f"<= {tps.param('rec_en_inp_cs_sel').max:g} V rec",
         f"{tps.param('rec_en_inp_cs_sel').max - PIN40[0][1]:+.2f} V", status(tps.param('rec_en_inp_cs_sel').max - PIN40[0][1]))
    summ("ov_pin_40v_v", f"OV pin at {VBUS_HI:g} V bus (max)", PIN40[1][1], f"{PIN40[1][1]:.3f} V", f"<= {tps.param('abs_input_vs_pos').max:g} V abs",
         f"{tps.param('abs_input_vs_pos').max - PIN40[1][1]:+.2f} V", status(tps.param('abs_input_vs_pos').max - PIN40[1][1]))
    summ("trip_min_a", "Short-circuit trip, minimum (self-heated, tol, TCR, solder BOL)", TRIP_MIN, f"{TRIP_MIN:.2f} A", f"> {I_DES:g} A design current",
         f"{TRIP_MARGIN:+.2f} A ({TRIP_MARGIN / I_DES * 100:+.1f} %)", est_status(TRIP_MARGIN))
    summ("trip_max_a", "Highest non-tripping current (trip maximum)", TRIP_MAX, f"{TRIP_MAX:.2f} A", "report", "-", "REPORT ONLY")
    summ("soft_shunt_w", "Shunt power at the highest non-tripping current, 40 degC", P_NT_JUDGED, f"{P_NT_JUDGED:.3f} W", f"<= {RATED_SH:g} W",
         f"{SOFT_P_MARGIN:+.3f} W", est_status(SOFT_P_MARGIN))
    summ("soft_fet_tj_c", "FET Tj at the highest non-tripping current, 40 degC", TJ_NT, f"{TJ_NT:.1f} degC", f"<= {TJMAX:g} degC", f"{SOFT_TJ_MARGIN:+.1f} K", est_status(SOFT_TJ_MARGIN))
    summ("fet_tj_des_c", f"FET Tj at {I_DES:g} A, 40 degC", TJ_DES[TA_MAX], f"{TJ_DES[TA_MAX]:.1f} degC", f"<= {TJMAX:g} degC", f"{TJ_DES_MARGIN:+.1f} K", est_status(TJ_DES_MARGIN))
    summ("shunt_p_des_w", f"Shunt power at {I_DES:g} A, 40 degC", P_SH_DES[TA_MAX], f"{P_SH_DES[TA_MAX]:.3f} W", f"<= {RATED_SH:g} W", f"{P_SH_MARGIN:+.3f} W", est_status(P_SH_MARGIN))
    summ("shunt_elem_des_c", f"Shunt element temperature at {I_DES:g} A, 40 degC", TE_DES[TA_MAX], f"{TE_DES[TA_MAX]:.1f} degC", f"<= {T_ELEM_LIMIT:g} degC (ASSUMED)",
         f"{TE_DES_MARGIN:+.1f} K", "ESTIMATE, " + ("PASS" if TE_DES_MARGIN >= 0 else "FAIL") + " only if the element limit is the assumed one")
    summ("tps_tj_c", "TPS48000 Tj at 40 degC (rth_ja typical)", TJ_TPS, f"{TJ_TPS:.2f} degC", f"<= {TJ_ABS_TPS:g} degC", f"{TJ_TPS_MARGIN:+.1f} K", est_status(TJ_TPS_MARGIN))
    summ("drop_des_mv", f"BUS_IN to BUS_OUT drop at {I_DES:g} A (max over ambient)", max(DROP.values()) * 1e3, f"{max(DROP.values()) * 1e3:.1f} mV", "report", "-", "REPORT ONLY (ESTIMATE)")
    summ("inrush_min_a", "Inrush current into the reference load, minimum", INRUSH["slow"], f"{INRUSH['slow']:.2f} A", "report", "-", "REPORT ONLY")
    summ("inrush_max_a", "Inrush current into the reference load, maximum", INRUSH["fast"], f"{INRUSH['fast']:.2f} A", "report", "-", "REPORT ONLY")
    summ("ramp_time_12v_ms", "Ramp time at the bench bus (slowest corner)", VBUS_BENCH / SLEW["slow"] * 1e3, f"{VBUS_BENCH / SLEW['slow'] * 1e3:.2f} ms", "report", "-", "REPORT ONLY")
    summ("ramp_time_40v_ms", f"Ramp time at {VBUS_HI:g} V (slowest corner)", VBUS_HI / SLEW["slow"] * 1e3, f"{VBUS_HI / SLEW['slow'] * 1e3:.2f} ms", "report", "-", "REPORT ONLY")
    summ("turnon_tj_c", "FET Tj peak during turn-on, 12 and 40 V, both loads, both ramp corners", TJ_TURN_MAX, f"{TJ_TURN_MAX:.1f} degC", f"<= {TJMAX:g} degC",
         f"{TURN_TJ_MARGIN:+.1f} K", est_status(TURN_TJ_MARGIN))
    summ("turnon_soa_ratio", "Turn-on I_eq / SOA limit (TC 125 degC curve), worst", SOA_RATIO, f"{SOA_RATIO:.3f}", "<= 1", f"{1 - SOA_RATIO:+.3f}", est_status(1 - SOA_RATIO))
    summ("turnon_max_pass_v", "Largest passing bus voltage at turn-on (grid 0.5 V, to 80 V)", V_PASS_ALL,
         "none" if V_PASS_ALL is None else f"{V_PASS_ALL:g} V", f">= {VBUS_HI:g} V rated",
         "-" if V_PASS_ALL is None else f"{V_PASS_ALL - VBUS_HI:+.1f} V", "ESTIMATE, " + ("PASS" if V_PASS_ALL is not None and V_PASS_ALL >= VBUS_HI else "FAIL"))
    summ("tsc_min_us", "Latch-off delay tSC, eq. 8, minimum", TSC_EQ8[0] * 1e6, f"{TSC_EQ8[0] * 1e6:.1f} us", "report", "-", "REPORT ONLY")
    summ("tsc_max_us", "Latch-off delay tSC, eq. 8, maximum", TSC_EQ8[1] * 1e6, f"{TSC_EQ8[1] * 1e6:.1f} us", "report", "-", "REPORT ONLY")
    summ("tsc_max_ext_us", "Latch-off delay tSC maximum with V(TMR_SC) max (not a DS equation)", TSC_EXT[1] * 1e6, f"{TSC_EXT[1] * 1e6:.1f} us", "report", "-", "REPORT ONLY")
    summ("fault_tj_c", f"FET Tj after {I_FAULT:g} A for tSC max, from the soft-short state", TJ_FAULT["soft"], f"{TJ_FAULT['soft']:.1f} degC", f"<= {TJMAX:g} degC",
         f"{TJMAX - TJ_FAULT['soft']:+.1f} K", est_status(TJMAX - TJ_FAULT["soft"]))
    summ("fault_i_limit_a", "Fault current with Tj at the limit (tSC max, soft-short start)", I_FAULT_LIMIT, f"{I_FAULT_LIMIT:.0f} A", f">= {I_FAULT:g} A",
         f"{I_FAULT_LIMIT - I_FAULT:+.0f} A", est_status(I_FAULT_LIMIT - I_FAULT))
    summ("fault_id_pulse_margin_a", "Fault current vs pulsed drain current", IDP_MARGIN, f"{I_FAULT:g} A", f"<= {IDP:g} A (`id_pulse`)", f"{IDP_MARGIN:+.0f} A", status(IDP_MARGIN))
    summ("fault_shunt_energy_mj", "Shunt energy in the fault (no DS bound)", E_SH_FAULT * 1e3, f"{E_SH_FAULT * 1e3:.2f} mJ", "not bounded by the DS", "-", "REPORT ONLY (UNBOUNDED)")
    summ("fault_cs_v", "CS+ to CS- in the fault", V_CS_FAULT, f"{V_CS_FAULT:.3f} V", f"<= {CS_PAIR.max:g} V abs", f"{CS_PAIR.max - V_CS_FAULT:+.1f} V", status(CS_PAIR.max - V_CS_FAULT))
    summ("avalanche_max_mj", f"Avalanche energy, worst of the L sweep ({L_LEAD[0] * 1e6:g}-{L_LEAD[-1] * 1e6:g} uH)", E_AV_MAX * 1e3, f"{E_AV_MAX * 1e3:.1f} mJ",
         f"`eas` {EAS * 1e3:.0f} mJ at 50 A only", f"{EAS / E_AV_MAX:.1f}x", "ESTIMATE (eas not valid above 50 A)")
    summ("cbst_min_nf", "Bootstrap capacitor minimum needed (eq. 4, C1 max)", CBST_MIN_WC * 1e9, f"{CBST_MIN_WC * 1e9:.1f} nF", f"<= {CBST_LOW * 1e9:.0f} nF (low corner, no DC bias loss)",
         f"{(CBST_LOW - CBST_MIN_WC) * 1e9:+.0f} nF", "ESTIMATE: DC-bias loss not bounded in the library")
    summ("t_drv_en_worst_ms", "Initial gate-driver enable delay t_drv_en, worst", T_DRV_WORST * 1e3, f"{T_DRV_WORST * 1e3:.1f} ms", "report", "-", "REPORT ONLY")
    VBR_TVS_MARGIN = tps.param("abs_vs_cs_gnd").max - VBR
    summ("turnoff_transient_no_tvs", "Turn-off through lead inductance, no TVS: avalanche clamps BUS_IN at V(BR)DSS min or above vs VS / CS+ abs max", VBR,
         f">= {VBR:g} V", f"<= {tps.param('abs_vs_cs_gnd').max:g} V (`abs_vs_cs_gnd`)", f"{VBR_TVS_MARGIN:+.0f} V (V(BR) is a minimum, so the clamp can be higher)",
         "FAIL (risk: unprotected transient; TVS deferred by the designer; DS p.27-28 recommends a TVS)")
    summ("gate_overlap_v", "V(G_GOOD) max above V(BST-SRC) ON min", GATE_OVERLAP, f"{GATE_OVERLAP:.2f} V", "<= 0 V (no overlap)", f"{-GATE_OVERLAP:+.2f} V", "FAIL (accepted residual risk, OPEN ITEM)")
    summ("v_c1_max_v", "C1 gate to GND, worst case (bus max + V(BST-SRC_OFF) max)", V_C1_MAX, f"{V_C1_MAX:.2f} V",
         f"<= {CAP_RATED_C1.max:g} V (C0G rated_v, derate 1)", f"{CAP_RATED_C1.max - V_C1_MAX:+.2f} V", status(CAP_RATED_C1.max - V_C1_MAX))
    summ("cbst_dc_bias_allow_frac", "Largest DC-bias and aging loss of CBST that still meets eq. 4 at the low corner (not a DS equation)",
         CBST_ALLOW, f"{CBST_ALLOW * 100:.1f} % allowed loss", "loss NOT BOUNDED in the library", "-", "OPEN ITEM: DC-bias loss not bounded")
    summ("c1_dump_peak_a", "C1 dump peak current into PD through R2 (v_c1_max_v / R2 min, not a DS equation)", C1_DUMP_PEAK,
         f"{C1_DUMP_PEAK:.2f} A", "PD peak sink not bounded (i_pd_peak: 2 A typ only)", "-", "OPEN ITEM: PD sink and R2 pulse rating not bounded")
    summ("ratings_min_margin", "Smallest rating margin (table in section 10, normalised: see table)", RATING_MIN, f"{len(RATINGS)} rows", "all margins >= 0",
         f"{len(RATING_FAIL)} failing" if RATING_FAIL else "none failing", "PASS" if not RATING_FAIL else "FAIL")

    # %% [markdown]
    # ## Charts

    # %%
    # 5. FET Tj and shunt power vs current, trip window shaded
    I_SW = np.linspace(0.2, 1.06 * TRIP_MAX, 70)
    TJ_SW = [tj_steady(i, TA_MAX) for i in I_SW]
    PSH_SW = [i * i * r_hi(sh_elem(i, TA_MAX)) for i in I_SW]
    fig, axes = plt.subplots(2, 1, figsize=(8.6, 7.2), sharex=True)
    for ax, y, col, lim, lab, yl in ((axes[0], TJ_SW, BLUE, TJMAX, f"Tj limit {TJMAX:g} degC", "FET Tj (degC), ESTIMATE"),
                                     (axes[1], PSH_SW, ORANGE, RATED_SH, f"shunt rating {RATED_SH:g} W", "Shunt power (W), ESTIMATE")):
        ax.plot(I_SW, y, color=col, lw=2)
        ax.axvspan(TRIP_MIN, TRIP_MAX, color=GRID, alpha=0.8, lw=0)
        ax.axhline(lim, color=RED, lw=1, ls=(0, (4, 3)))
        ax.text(0.3, lim, lab, color=RED, fontsize=8, va="bottom")
        ax.axvline(I_DES, color=INK, lw=0.8, ls=(0, (1, 2)))
        style_axes(ax, "", yl)
    axes[0].text(I_DES, axes[0].get_ylim()[1] * 0.97, f" {I_DES:g} A design", fontsize=8, va="top")
    axes[0].text((TRIP_MIN + TRIP_MAX) / 2, axes[0].get_ylim()[0] + 8, f"trip window\n{TRIP_MIN:.2f} to {TRIP_MAX:.2f} A", ha="center", fontsize=8.5, color=INK)
    style_axes(axes[1], "Load current (A), continuous at 40 degC ambient", "Shunt power (W), ESTIMATE")
    fig.suptitle(f"Soft shorts: FET and {R0 * 1e3:g} mohm shunt vs current (self-heated, 40 degC); shaded = trip window", color=INK, fontsize=10.5)
    fig.tight_layout()
    save(fig, "soft_shorts.png")

    # divider trip windows
    fig, ax = plt.subplots(figsize=(8.6, 3.8))
    names = [("uvr", "UVLO rising (turn-on)"), ("uvf", "UVLO falling (turn-off)"), ("ovr", "OV rising (cut-off)"), ("ovf", "OV falling (recovery)")]
    for k, (key, lab) in enumerate(names[::-1]):
        lo, hi = W[key]
        ax.barh(k, hi - lo, left=lo, height=0.5, color=BLUE if key.startswith("uv") else ORANGE)
        ax.text(lo - 0.15, k, f"{lo:.2f}", ha="right", va="center", fontsize=8)
        ax.text(hi + 0.15, k, f"{hi:.2f}", ha="left", va="center", fontsize=8)
    ax.set_yticks(range(4))
    ax.set_yticklabels([n[1] for n in names[::-1]], fontsize=9, color=INK)
    ax.axvline(UVLO_LIMIT, color=RED, lw=1, ls=(0, (4, 3)))
    ax.axvline(OV_MIN, color=RED, lw=1, ls=(0, (4, 3)))
    ax.text(UVLO_LIMIT, 3.45, f" turn-on limit {UVLO_LIMIT:g} V", color=RED, fontsize=8, ha="right")
    ax.text(OV_MIN, 3.45, f" OV floor {OV_MIN:g} V", color=RED, fontsize=8, ha="left")
    ax.set_xlim(7, 18)
    style_axes(ax, "BUS_IN trip voltage (V), all corners", "")
    ax.set_title(f"UVLO/OV trip windows: {R_DIV[0] / 1e3:g}k / {R_DIV[1] / 1e3:g}k / {R_DIV[2] / 1e3:g}k", color=INK, fontsize=10)
    fig.tight_layout()
    save(fig, "divider_windows.png")

    # turn-on: Tj and SOA ratio vs bus voltage
    fig, axes = plt.subplots(1, 2, figsize=(11.2, 4.4))
    cols = {("CC", 0): BLUE, ("CC", 1): GREEN, ("R", 0): ORANGE, ("R", 1): RED}
    for (load, cname), rows in SWEEP.items():
        k = 0 if cname.startswith("slow") else 1
        rr = [r for r in rows if r["vb"] <= 60.0]
        lab = f"{'constant ' + format(I_DES, 'g') + ' A' if load == 'CC' else 'resistive'}, {cname.split(' (')[0]}"
        axes[0].plot([r["vb"] for r in rr], [r["tj"] for r in rr], color=cols[(load, k)], lw=1.8, label=lab)
        axes[1].plot([r["vb"] for r in rr], [r["ieq"] / r["r125"] if r["r125"] else np.nan for r in rr], color=cols[(load, k)], lw=1.8, label=lab)
    axes[0].axhline(TJMAX, color=RED, lw=1, ls=(0, (4, 3)))
    axes[1].axhline(1.0, color=RED, lw=1, ls=(0, (4, 3)))
    for ax in axes:
        ax.axvline(VBUS_HI, color=INK, lw=0.8, ls=(0, (1, 2)))
    style_axes(axes[0], "Bus voltage (V)", "FET Tj peak (degC), ESTIMATE")
    style_axes(axes[1], "Bus voltage (V)", "I_eq / SOA limit (TC 125 degC curve), ESTIMATE")
    axes[1].legend(fontsize=7.5, frameon=False, loc="upper left")
    fig.suptitle(f"Turn-on into {CL * 1e6:g} uF from t = 0 with the load drawing current (dotted: {VBUS_HI:g} V rating)", color=INK, fontsize=10.5)
    fig.tight_layout()
    save(fig, "turn_on.png")

    # fault: Tj vs fault current and avalanche energy vs lead inductance
    fig, axes = plt.subplots(1, 2, figsize=(11.2, 4.4))
    IF = np.geomspace(10.0, 700.0, 60)
    axes[0].plot(IF, [tj_fault_end(i, Z_FAULT, TJ0_SOFT) for i in IF], color=BLUE, lw=2, label="from the soft-short state")
    axes[0].plot(IF, [tj_fault_end(i, Z_FAULT, TJ0_DES) for i in IF], color=GREEN, lw=2, label=f"from the {I_DES:g} A steady state")
    axes[0].axhline(TJMAX, color=RED, lw=1, ls=(0, (4, 3)))
    axes[0].axvline(I_FAULT, color=INK, lw=0.8, ls=(0, (1, 2)))
    axes[0].axvline(IDP, color=INK, lw=0.8, ls=(0, (4, 3)))
    axes[0].text(IDP, TJMAX * 0.5, " id_pulse", fontsize=8, rotation=90, va="center")
    axes[0].set_ylim(0, 400)
    axes[0].set_xscale("log")
    style_axes(axes[0], f"Fault current (A), {T_FAULT * 1e6:.0f} us", "FET Tj at the end of the fault (degC), ESTIMATE")
    axes[0].legend(fontsize=8, frameon=False, loc="upper left")
    Ls = np.geomspace(L_LEAD[0], L_LEAD[-1], 30)
    for (i_, vb_), col, ls_ in (((I_FAULT, VBUS_HI), BLUE, "-"), ((I_FAULT, VBUS_BENCH), BLUE, (0, (3, 2))),
                                ((TRIP_MAX, VBUS_HI), ORANGE, "-"), ((TRIP_MAX, VBUS_BENCH), ORANGE, (0, (3, 2)))):
        axes[1].plot(Ls * 1e6, [e_aval(l, i_, vb_) * 1e3 for l in Ls], color=col, lw=1.8, ls=ls_, label=f"{i_:.1f} A, {vb_:g} V bus")
    axes[1].axhline(EAS * 1e3, color=RED, lw=1, ls=(0, (4, 3)))
    axes[1].text(L_LEAD[0] * 1e6, EAS * 1e3, " eas 398 mJ (valid at 50 A only)", color=RED, fontsize=8, va="bottom")
    axes[1].set_xscale("log")
    axes[1].set_yscale("log")
    style_axes(axes[1], "Lead inductance (uH)", "Avalanche energy at turn-off (mJ), ESTIMATE")
    axes[1].legend(fontsize=8, frameon=False, loc="lower right")
    fig.suptitle("Short circuit and turn-off", color=INK, fontsize=10.5)
    fig.tight_layout()
    save(fig, "fault.png")

    # %% [markdown]
    # ## Report text

    # %%
    def fmt_ohm(r):
        return f"{r / 1e3:g} kohm" if r >= 1e3 else f"{r:g} ohm"


    OVR_NOM = calc.vin_ovp_from_divider(*R_DIV, OVR.typ)
    UVR_NOM = calc.vin_uvlo_from_divider(*R_DIV, UVR.typ)
    UVF_NOM = calc.vin_uvlo_from_divider(*R_DIV, UVF.typ)
    OVF_NOM = calc.vin_ovp_from_divider(*R_DIV, OVF.typ)
    passives = {CBYP: "100 nF", CBST: "470 nF", C1G: "6.8 nF", R1G: fmt_ohm(R1G), R_FPU: fmt_ohm(R_FPU), RISCP: fmt_ohm(RISCP), R2G: fmt_ohm(R2G),
                R_DIV[0]: fmt_ohm(R_DIV[0]), R_DIV[1]: fmt_ohm(R_DIV[1]), R_DIV[2]: fmt_ohm(R_DIV[2])}
    N_BOM = 3 + len({round(k, 15) for k in passives})  # two ICs + shunt + distinct passive values (CTMR = C1, RTMR = R1, R_PPU = R_FPU assumed equal)
    assert CTMR == C1G and RTMR == R1G and R_PPU == R_FPU
    md_all = []
    md_ops = [
        "## 2. Operating conditions and inputs", "",
        "Every input is in the params file with its basis (`<key>_basis`). Design intent comes from `blocks/TPS48000HighSideSwitch/spec.md`.", "",
    ]
    md_ops += mdtable(["Input", "Value", "Basis"], [
        ["Design maximum continuous current", f"{I_DES:g} A", "designer decision 2026-11-15 (`i_design_a_basis`)"],
        ["Ambient", f"{TA_MIN:g} to {TA_MAX:g} degC", "laboratory, IEC 61010-1 cl. 1.4.1 (`ta_basis`); derate " + f"{DERATE:g}"],
        ["Bus, rated", f"{VBUS_LO:g} to {VBUS_HI:g} V DC", "designer decision 2026-11-15"],
        ["Bus, bench instance", f"{VBUS_BENCH:g} V nominal, minimum {VBUS_MIN:g} V", "12 V -10 % (spec.md)"],
        ["UVLO turn-on", f"guaranteed at or below {UVLO_LIMIT:g} V ({VBUS_MIN:g} - {UVLO_MARGIN:g} V margin)", "spec.md"],
        ["OV", f"never trips below {OV_MIN:g} V", "12 V +10 % (spec.md)"],
        ["Logic rail V3V3", f"{V3V3_NOM:g} V +/-{V3V3_TOL * 100:g} % ({V3V3_MIN:.3f} to {V3V3_MAX:.3f} V)", "board rail (spec.md)"],
        ["Shunt", f"`{MPN_SHUNT}`: {R0 * 1e3:g} mohm, code {CODE} (+/-{TOL_SH.max * 100:g} %), TCR +/-{TCR_SH.max * 1e6:g} ppm/degC", f"{cite(TOL_SH)}; {cite(TCR_SH)}"],
        ["Shunt TCR reference temperature", f"{T_REF:g} degC", "ASSUMPTION: the DS states none"],
        ["Shunt thermal resistance (element to ambient)", f"{THETA:g} K/W", "ESTIMATE: (derate_zero - derate_knee) / p70 from the DS p.3 derating graph"],
        ["Shunt element limit", f"{T_ELEM_LIMIT:g} degC", "ASSUMPTION (the derating curve's zero point), not `t_oper`"],
        ["Shunt BOL (solder heat)", f"+/-(R x {BOLP * 100:g} % + {BOLO * 1e3:g} mohm)", f"{cite(shl.param('perf_solder_heat'))}; test limit used as a bound"],
        ["FET thermal", f"RthJA {RTHJA:g} K/W (6 cm2 copper: LAYOUT REQUIREMENT), RthJC {RTHJC:g} K/W, Tj max {TJMAX:g} degC", f"{cite(fet.param('rth_ja'))}"],
        ["FET Rds(on) at 8.24 V gate drive", f"{RDS10 * 1e3:g} mohm (10 V max) x Tj curve x typical VGS ratio", "ESTIMATE: only the 10 V maximum is a bound"],
        ["ZthJC", f"{ZT_Z[0]:g} K/W at 10 us to {RTHJC:g} K/W (100 ms and longer)", "ESTIMATE: DS Diagram 5 single-pulse curve rows, typical"],
        ["Reference load capacitance", f"{CL * 1e6:g} uF", "designer decision 2026-11-15"],
        ["Prospective fault current", f"{I_FAULT:g} A", "system requirement on the PSU current limit or a per-channel fuse (designer 2026-11-15)"],
        ["ERJ-3EKF resistors", f"{(RF_LO - 1) * 100:.3f} to +{(RF_HI - 1) * 100:.3f} % over TA", f"tolerance {cite(erj.param('tol'))}, TCR {cite(erj.param('tcr_10r_1m'))}; tolerance referenced to 20 degC, TCR to 25 degC"],
        ["6.8 nF capacitors (C1, CTMR), 100 V", f"{CAP_SMALL_TXT}: {C1_LO:.4f} to {C1_HI:.4f} x nominal", C_TXT],
        ["Bootstrap capacitor", f"1 x {CBST * 1e9:g} nF (`{SEL_BST.mpn}`, X7R 50 V 0805) = {CBST * 1e9:g} nF nominal", f"{CB_LO:.4f} to {CB_HI:.4f} x nominal: {CB_TXT}"],
        ["VS and SRC bypass capacitors", f"2 x {CBYP * 1e9:g} nF (`{SEL_BYP.mpn}`, X7R 50 V)", f"catalog tolerance {SEL_BYP.tolerance}; DC-bias loss not bounded"],
        ["Lead inductance (turn-off avalanche)", ", ".join(f"{x * 1e6:g}" for x in L_LEAD) + " uH", "DESIGN INPUT sweep, the real value is not known"],
    ])
    md_ops += [
        "",
        f"**6.8 nF capacitor choice (C1 and CTMR, 100 V).** The GCM188 catalog lists C0G 6.8 nF 100 V at +/-5 % (`{SEL_C1.mpn}`, "
        f"also +/-2 % `GCM1885C2A682GE02`): {'C0G +/-5 % is used' if C_PREF_OK else 'NOT listed, X7R used'}.",
        "",
        "Design definition (parts and values as built into the analysis):", "",
    ]
    md_ops += mdtable(["Function", "Part / MPN", "Value", "Basis"], [
        ["Controller", f"TI TPS48000QDGXQ1 (`{tps.info.get('mpn')}`)", "VSSOP-19", "designer choice 2026-11-15"],
        ["High-side FET", "Infineon ISC035N10NM5LF2", f"{VDS_RATING:g} V, {RDS10 * 1e3:g} mohm max at 10 V", cite(fet.param("rds_on_10v"))],
        ["Shunt", f"Vishay `{MPN_SHUNT}`", f"{R0 * 1e3:g} mohm", cite(TOL_SH)],
        ["Gate ramp R1 / latch-off RTMR", f"`{erj.mpn(R1G)}`", fmt_ohm(R1G), "ERJ-3EKF; one BOM line"],
        ["Gate damping R2", f"`{erj.mpn(R2G)}`", fmt_ohm(R2G), "DS p.16"],
        ["Gate ramp C1 (gate to GND, DS Fig 7-4) / timer CTMR", f"`{SEL_C1.mpn}` (100 V)", f"{C1G * 1e9:g} nF", CAP_SMALL_TXT],
        ["Bootstrap CBST", f"`{SEL_BST.mpn}`", f"{CBST * 1e9:g} nF", "designer decision D14 (one part)"],
        ["VS and SRC bypass", f"`{SEL_BYP.mpn}` x 2", f"{CBYP * 1e9:g} nF", "DS p.5 (external capacitor, min 22 nF); DS p.28"],
        ["ISCP resistor RISCP", f"`{erj.mpn(RISCP)}`", fmt_ohm(RISCP), cite(VSCP)],
        ["UVLO/OV ladder", f"`{erj.mpn(R_DIV[0])}`, `{erj.mpn(R_DIV[1])}`, `{erj.mpn(R_DIV[2])}`", " / ".join(fmt_ohm(r) for r in R_DIV), "section 4"],
        ["FAULT_N / PGOOD_N pull-ups", f"`{erj.mpn(R_FPU)}`", fmt_ohm(R_FPU), "designer choice"],
        ["BOM lines", f"{N_BOM}", f"2 ICs, 1 shunt and {N_BOM - 3} distinct passive values", "100 nF (VS, SRC), 470 nF (CBST), 6.8 nF (C1, CTMR), 100 kohm (R1, RTMR), 10 kohm (pull-ups)"],
    ])
    md_all += md_ops + [""]

    # ---- section 3: DS 8.2.2 walk-through ----
    kw_cb, res_cb, cite_cb = ds_example("cbst_min")
    kw_ct, res_ct, cite_ct = ds_example("ctmr_for_tsc")
    kw_ov, res_ov, cite_ov = ds_example("vin_ovp_from_divider")
    kw_uv, res_uv, cite_uv = ds_example("vin_uvlo_from_divider")
    DS_R1 = kw_ov["r1"]
    a_ov, b_uv = DS_EX["vinuvlo"], DS_EX["vinovp"]
    # DS procedure applied forward for the design (eq. 12 and 13), nominal targets = the nominal trips of the selected design, 0.1 V
    TG_UV, TG_OV = round(UVR_NOM, 1), round(OVR_NOM, 1)
    b_ = UVR.typ / TG_UV
    a_ = OVR.typ / TG_OV
    RT_F = R_DIV[0] / (1 - b_)
    R3_F, R2_F = a_ * RT_F, (b_ - a_) * RT_F
    RT_DS = DS_R1 / (1 - kw_uv["vuvlo"] / DS_EX["vinuvlo"])  # DS example: total from R1 and eq. 13
    R3_DS = kw_ov["vovr"] / DS_EX["vinovp"] * RT_DS
    R2_DS = kw_uv["vuvlo"] / DS_EX["vinuvlo"] * RT_DS - R3_DS
    I_TRIP_NOM_TARGET = TRIP_NOM
    RISCP_EQ5 = calc.riscp_for_isc(TRIP_NOM, R0)
    TSC_RETRY_FOR_C = CTMR * VTMR.typ / ITMR.typ  # eq. 6/11 solved for tSC with I(TMR_SRC_CB) typ (printed 1.1 V as VTMR.typ)
    md3 = ["## 3. DS design-procedure walk-through (TPS4800-Q1 8.2.2, p.23-25)", "",
           "Each step repeats the datasheet's procedure in the datasheet's order. The DS example is a MOSFET VDS-sensed, auto-retry design "
           "(VIN 16 to 60 V, IOUT 20 A, ISC 60 A); this design senses a shunt and latches off. The library `EXAMPLES` already prove that "
           "each equation reproduces the DS example; the DS-printed numbers that are not in `component.toml` are marked `DS`.", ""]
    md3 += mdtable(["Step (DS order)", "Equation and cite", "DS example inputs -> result", "This design: inputs", "Nominal", "Worst case", "Chosen standard value"], [
        ["MOSFET Q1", "selection, DS p.23", "IAUS200N08S5N023: 80 V, +/-20 V VGS, 2.3 mohm typ at 10 V, Qg 85 nC typ (`DS`)",
         f"ISC035N10NM5LF2: {VDS_RATING:g} V, +/-{fet.param('vgs').max:g} V VGS, {RDS10 * 1e3:g} mohm max at 10 V, Qg {fet.param('qg').typ * 1e9:g} typ / {fet.param('qg').max * 1e9:g} nC max",
         "-", f"VGS limit {fet.param('vgs').max:g} V vs charge pump {VBST_REG_MAX:g} V max; VDS {VDS_RATING:g} V vs bus {VBUS_HI:g} V", "ISC035N10NM5LF2"],
        ["Bootstrap capacitor CBST", f"eq. 9 (eq. 4 with C1 = 0): CBST = Qg / 1 V; eq. 4 adds 10 x C1; {cite(tps.param('rec_cap_bst_src'))}",
         f"Qg {kw_cb['qg_total'] * 1e9:g} nC / {kw_cb['dv_bst']:g} V -> {res_cb * 1e9:g} nF ({cite_cb})",
         f"Qg max {fet.param('qg').max * 1e9:g} nC, dV {DV_BST:g} V, C1 {C1G * 1e9:g} nF",
         f"eq. 9: {calc.cbst_min(fet.param('qg').max, DV_BST) * 1e9:.1f} nF; eq. 4: {CBST_MIN_NOM * 1e9:.1f} nF",
         f"eq. 4 with C1 max ({C1G * C1_HI * 1e9:.2f} nF): {CBST_MIN_WC * 1e9:.1f} nF",
         f"DS: {DS_EX['cbst_std_nf']:g} nF 10 %. Here: 1 x {CBST * 1e9:g} nF X7R 50 V 0805 (GCM21B) = {CBST * 1e9:g} nF nominal, {CBST_LOW * 1e9:.1f} nF low corner (no DC-bias loss)"],
        ["Short-circuit threshold RISCP", f"eq. 10 = eq. 5: RISCP = (ISC x RSNS - 19 mV) / 2 uA; {cite(VSCP)}",
         f"ISC {DS_EX['isc_a']:g} A, RDS(ON) {DS_EX['rds_mohm']:g} mohm -> eq. 5 gives {DS_RISCP_ISC / 1e3:.1f} kohm; the DS prints {DS_EX['riscp_printed_kohm']:g} kohm (DS inconsistency, library decision log) (`DS`)",
         f"RSNS = shunt {R0 * 1e3:g} mohm; nominal trip ISC = V(SCP) typ {VSCP.typ * 1e3:g} mV / RSNS",
         f"ISC {TRIP_NOM:.2f} A -> RISCP {RISCP_EQ5 / 1e3:.2f} kohm",
         f"trip window {TRIP_MIN:.2f} to {TRIP_MAX:.2f} A (section 5, table rows {VSCP.min * 1e3:g} / {VSCP.max * 1e3:g} mV)",
         f"DS: {DS_EX['riscp_std_kohm']:g} kohm 1 %. Here: {RISCP / 1e3:g} kohm 1 % (E96), the exact row value of `{RISCP_ROWS[RISCP]}`"],
        ["Fault timer CTMR", f"eq. 11 (= eq. 6, auto-retry): CTMR = ITMR x tSC / 1.1; eq. 8 (latch-off): {cite(ITMR)}",
         f"{kw_ct['i_tmr'] * 1e6:g} uA x {kw_ct['tsc'] * 1e6:g} us / {kw_ct['v_tmr']:g} V -> {res_ct * 1e9:.3f} nF ({cite_ct})",
         f"latch-off with RTMR {RTMR / 1e3:g} kohm across CTMR {CTMR * 1e9:g} nF ({SMALL_DIEL}), I(TMR_SRC_CB) {ITMR.min * 1e6:g}/{ITMR.typ * 1e6:g}/{ITMR.max * 1e6:g} uA",
         f"eq. 8: tSC = {TSC_NOM * 1e6:.1f} us (typ current). Note eq. 11 for the same CTMR would give {TSC_RETRY_FOR_C * 1e6:.1f} us (auto-retry, not used)",
         f"eq. 8: {TSC_EQ8[0] * 1e6:.1f} to {TSC_EQ8[1] * 1e6:.1f} us (C tolerance, RTMR tolerance, I min/max)",
         f"DS: {DS_EX['ctmr_std_nf']:g} nF 10 %. Here: {CTMR * 1e9:g} nF {SEL_CT.tolerance} (shares the C1 BOM line)"],
        ["UVLO and OV divider", "eq. 12: V(OVR) = R3 / (R1+R2+R3) x VINOVP; eq. 13: V(UVLOR) = (R2+R3) / (R1+R2+R3) x VINUVLO; I(R123) >= 20 x leakage",
         f"VINUVLO {DS_EX['vinuvlo']:g} V, VINOVP {DS_EX['vinovp']:g} V, V(OVR) {kw_ov['vovr']:g} V, V(UVLOR) {kw_uv['vuvlo']:g} V, R1 {DS_R1 / 1e3:g} kohm -> R2 {R2_DS / 1e3:.1f}, R3 {R3_DS / 1e3:.1f} kohm ({cite_ov})",
         f"nominal targets {TG_UV:g} V UVLO / {TG_OV:g} V OV (the nominal trips of the selected design, rounded to 0.1 V; the design itself is selected on worst case, section 4), R1 = {R_DIV[0] / 1e3:g} kohm, V(OVR) typ {OVR.typ:g} V, V(UVLOR) typ {UVR.typ:g} V",
         f"R2 {R2_F / 1e3:.2f} kohm, R3 {R3_F / 1e3:.2f} kohm (continuous solution)",
         f"UVLO rising {W['uvr'][0]:.2f} to {W['uvr'][1]:.2f} V, OV rising {W['ovr'][0]:.2f} to {W['ovr'][1]:.2f} V; I(R123) {I_DIV_LO * 1e6:.1f} uA min vs 20 x leakage {20 * max(I_EN, I_OV) * 1e6:.1f} uA",
         f"DS: {DS_EX['r1_std_kohm']:g} / {DS_EX['r2_std_kohm']:g} / {DS_EX['r3_std_kohm']:g} kohm 1 %. Here: {' / '.join(fmt_ohm(r) for r in R_DIV)} (E96, 1 %)"],
        ["Inrush", f"eq. 2: IINRUSH = CLOAD x VBATT / Tcharge; eq. 3: C1 = 0.63 x V(BST-SRC) x CLOAD / (R1 x IINRUSH), DS p.15-16",
         "no inrush step in the 8.2.2 example; eq. 3 text uses V(BST-SRC) = 11 V",
         f"CLOAD {CL * 1e6:g} uF, R1 {R1G / 1e3:g} kohm, C1 {C1G * 1e9:g} nF {SMALL_DIEL}; V(BST-SRC) {VBST.min:g} / {VBST.typ:g} / {VBST.max:g} V (`v_bst_src_on` min / typ, `v_bst_src_off` max)",
         f"eq. 3 solved for IINRUSH at typ: {INRUSH_NOTOL['typ']:.2f} A; ramp {calc.i_inrush(CL, VBUS_HI, 1.0) / INRUSH_NOTOL['typ'] * 1e3:.2f} ms at {VBUS_HI:g} V, {calc.i_inrush(CL, VBUS_BENCH, 1.0) / INRUSH_NOTOL['typ'] * 1e3:.2f} ms at {VBUS_BENCH:g} V (eq. 2)",
         f"{INRUSH['slow']:.2f} to {INRUSH['fast']:.2f} A with V(BST-SRC), C1 and R1 tolerances; ramp {VBUS_HI / SLEW['fast'] * 1e3:.2f} to {VBUS_HI / SLEW['slow'] * 1e3:.2f} ms at {VBUS_HI:g} V",
         f"C1 = {C1G * 1e9:g} nF {SMALL_DIEL} (designer choice; not derived from a target inrush)"],
    ])
    md3 += ["",
            f"DS check of the printed example inputs against the equations: eq. 12 with V(OVR) = {kw_ov['vovr']:g} V gives {res_ov:g} V for the DS divider "
            f"(R1 {kw_ov['r1'] / 1e3:g} k, R2 {kw_ov['r2'] / 1e3:g} k, R3 {kw_ov['r3'] / 1e3:g} k); eq. 13 gives {calc.vin_uvlo_from_divider(**{k: v for k, v in kw_uv.items()}):.3f} V. "
            "The DS example solves with V(OVR) = 1.225 V, which is not a table row (the table is 1.17 / 1.23 / 1.28 V); this design uses the table rows. "
            "The DS eq. 6 text uses ITMR = 80 uA while eq. 11 and the table use 87 uA typ; the analysis uses the table row bounds. "
            "V(TMR_SC) in the DS equations is the printed 1.1 V; the row is 0.87 / 1.1 / 1.26 V (section 8).", ""]
    md_all += md3

    # ---- section 4: divider ----
    md4 = ["## 4. UVLO/OV divider", "",
           f"Selection rule (stated, applied by this script on every run): E96 ERJ-3EKF 1 % resistors from 1 kohm to 1 Mohm; total resistance "
           f"{RT_LO / 1e3:.0f} to {RT_HI / 1e3:.0f} kohm so the divider draws {P['i_div_min_ua']:g} to {P['i_div_max_ua']:g} uA at {VBUS_BENCH:g} V over the resistor tolerance; "
           f"feasible when the UVLO rising trip maximum is at or below {UVLO_LIMIT:g} V ({VBUS_MIN:g} V - {UVLO_MARGIN:g} V margin) and the OV rising trip "
           f"minimum is at or above {OV_MIN:g} V over every corner; among feasible triples the one with the highest UVLO falling minimum (turn-off "
           f"as close below the turn-on as the hysteresis allows; compared at 0.1 V resolution, as finer differences carry no meaning), then the lowest OV rising maximum. {N_CAND} candidates were screened (a nominal "
           f"window only removes triples that cannot be feasible), {N_FEAS} were feasible. Corners: V(UVLOR/F) and V(OVR/F) min and max ({cite(UVR)}; {cite(OVR)}), "
           f"each resistor at the tolerance + TCR limit over TA plus the solder-heat BOL ({(RF_LO - 1) * 100:.3f} / +{(RF_HI - 1) * 100:.3f} %), I(EN/UVLO) = {I_EN * 1e9:g} nA and I(OV) = {I_OV * 1e9:g} nA "
           "at both signs (no MIN printed; the 70 V maximum applied at every bus voltage: conservative).", "",
           f"**Selected:** r_top = {fmt_ohm(R_DIV[0])}, r_mid = {fmt_ohm(R_DIV[1])}, r_bot = {fmt_ohm(R_DIV[2])} (`{erj.mpn(R_DIV[0])}`, `{erj.mpn(R_DIV[1])}`, `{erj.mpn(R_DIV[2])}`). "
           f"The params file {'holds exactly this selection' if DIV_MATCH else 'DIFFERS from the selection (' + ' / '.join(fmt_ohm(r) for r in SEL_DIV) + '): update it'}.", ""]
    md4 += mdtable(["Trip", "Nominal (typ threshold)", "Min (worst case)", "Max (worst case)", "Limit", "Margin", "Status"], [
        ["UVLO rising (turn-on)", f"{UVR_NOM:.3f} V", f"{W['uvr'][0]:.3f} V", f"{W['uvr'][1]:.3f} V", f"max <= {UVLO_LIMIT:g} V", f"{UVLO_MARGIN_RES:+.3f} V", status(UVLO_MARGIN_RES)],
        ["UVLO falling (turn-off)", f"{UVF_NOM:.3f} V", f"{W['uvf'][0]:.3f} V", f"{W['uvf'][1]:.3f} V", f"max below the minimum bus {VBUS_MIN:g} V", f"{VBUS_MIN - W['uvf'][1]:+.3f} V", status(VBUS_MIN - W['uvf'][1])],
        ["OV rising (cut-off)", f"{OVR_NOM:.3f} V", f"{W['ovr'][0]:.3f} V", f"{W['ovr'][1]:.3f} V", f"min >= {OV_MIN:g} V", f"{OV_MARGIN_RES:+.3f} V", status(OV_MARGIN_RES)],
        ["OV falling (recovery)", f"{OVF_NOM:.3f} V", f"{W['ovf'][0]:.3f} V", f"{W['ovf'][1]:.3f} V", "report", "-", "REPORT ONLY"],
    ])
    md4 += ["",
            f"Turn-on margin to the {VBUS_MIN:g} V minimum bus: {TURN_ON_MARGIN:.3f} V (required >= {UVLO_MARGIN:g} V): **{status(TURN_ON_MARGIN - UVLO_MARGIN)}**. "
            f"The windows INCLUDE the ERJ-3EKF solder-heat BOL (+/-{BOL_ERJ * 100:g} % on every resistor, both signs, test limit used as a bound, {cite(erj.param('perf_soldering_heat'))}) on top of tolerance and TCR. "
            f"Information, without BOL: UVLO rising maximum {W_BOL['uvr'][1]:.3f} V, OV rising minimum {W_BOL['ovr'][0]:.3f} V.", "",
            f"Divider current at {VBUS_BENCH:g} V: {I_DIV_NOM * 1e6:.2f} uA nominal ({I_DIV_LO * 1e6:.2f} to {I_DIV_HI * 1e6:.2f} uA over resistor tolerance); "
            f"the DS asks for at least 20 x the pin leakage ({20 * max(I_EN, I_OV) * 1e6:.1f} uA for the larger of I(EN/UVLO) and I(OV)): "
            f"{'met' if I_DIV_LO >= 20 * max(I_EN, I_OV) else 'NOT met (leakage is already in the trip windows)'}.", ""]
    md4 += mdtable(["At a bus of", "EN/UVLO pin (min to max)", "OV pin (min to max)", "R_top voltage / power (max)", "Status"], [
        [f"{VBUS_HI:g} V", f"{PIN40[0][0]:.3f} to {PIN40[0][1]:.3f} V (rec 0 to {tps.param('rec_en_inp_cs_sel').max:g} V; abs {tps.param('abs_input_vs_pos').max:g} V)",
         f"{PIN40[1][0]:.3f} to {PIN40[1][1]:.3f} V (abs {tps.param('abs_input_vs_pos').max:g} V; no OV rec row)", f"{PIN40[3]:.1f} V / {PIN40[2] * 1e3:.2f} mW (limits {erj.param('v_limit').max:g} V / {erj.param('p70').max * 1e3:g} mW)",
         status(min(tps.param('rec_en_inp_cs_sel').max - PIN40[0][1], tps.param('abs_input_vs_pos').max - PIN40[1][1], erj.param('v_limit').max - PIN40[3], erj.param('p70').max - PIN40[2]))],
        [f"{VBUS_BENCH:g} V", f"{PIN12[0][0]:.3f} to {PIN12[0][1]:.3f} V", f"{PIN12[1][0]:.3f} to {PIN12[1][1]:.3f} V", f"{PIN12[3]:.1f} V / {PIN12[2] * 1e3:.3f} mW", "PASS"],
    ])
    md4 += ["", "![UVLO/OV trip windows](design-analysis/divider_windows.png)", "",
            "Figure 4. The four trip windows over all corners against the turn-on limit and the OV floor.", "",
            f"The ladder is selected for the bench bus ({VBUS_BENCH:g} V nominal). A board on a different nominal bus reselects it by running this analysis with its own params; "
            f"above the OV rising trip ({W['ovr'][0]:.1f} to {W['ovr'][1]:.1f} V) this bench instance is cut off by design, so the {VBUS_HI:g} V pin voltages are a pin-rating check, not an operating point.", ""]
    md_all += md4

    # ---- section 5: trip window and soft shorts ----
    md5 = ["## 5. Trip window and soft shorts", "",
           f"Short-circuit trip = V(CS+ - CS-) reaching V(SCP). RISCP = {fmt_ohm(RISCP)}: V(SCP) {VSCP.min * 1e3:g} / {VSCP.typ * 1e3:g} / {VSCP.max * 1e3:g} mV ({cite(VSCP)}); "
           f"the RISCP tolerance and TCR add +/-{DV_RISCP * 1e3:.3f} mV (slope 2 uA x RISCP from eq. 5, not a bound on the row). Shunt: {R0 * 1e3:g} mohm code {CODE}, resistance "
           f"R_hi / R_lo = R0 (1 +/- tol) (1 +/- TCR |T - {T_REF:g}|) +/- (R0 x {BOLP * 100:g} % + {BOLO * 1e3:g} mohm) (not a DS equation; `resistance_worst` plus the solder-heat BOL term), "
           f"at the self-heated element temperature (theta {THETA:g} K/W, ESTIMATE), over TA {TA_MIN:g} to {TA_MAX:g} degC.", ""]
    md5 += mdtable(["Quantity", "Value", "Corner", "Note"], [
        ["Trip minimum", f"**{TRIP_MIN:.3f} A**", f"V(SCP) min {VSCP_MIN * 1e3:.2f} mV, R_hi = {r_hi(TRIP_MIN_ELEM) * 1e3:.3f} mohm, TA {TRIP_MIN_TA:g} degC, element {TRIP_MIN_ELEM:.1f} degC", "ESTIMATE (element temperature)"],
        ["Trip maximum = highest non-tripping current", f"**{TRIP_MAX:.3f} A**", f"V(SCP) max {VSCP_MAX * 1e3:.2f} mV, R_lo = {r_lo(TRIP_MAX_ELEM) * 1e3:.3f} mohm, TA {TRIP_MAX_TA:g} degC, element {TRIP_MAX_ELEM:.1f} degC (low-R self-heating)", "ESTIMATE (element temperature)"],
        ["Nominal (information)", f"{TRIP_NOM:.2f} A", f"V(SCP) typ {VSCP.typ * 1e3:g} mV / {R0 * 1e3:g} mohm", "typical, not a bound"],
        [f"Trip minimum margin over {I_DES:g} A", f"{TRIP_MARGIN:+.3f} A ({TRIP_MARGIN / I_DES * 100:+.1f} %)", "", est_status(TRIP_MARGIN)],
    ])
    md5 += ["",
            f"Soft shorts (a load current above {I_DES:g} A and below the trip maximum never trips: the TPS48000 has only the short-circuit comparator). "
            f"Requirement: every current up to the trip maximum is either tripped or survived continuously at {TA_MAX:g} degC by the FET (Tj) and the shunt (power).", ""]
    md5 += mdtable(["At the highest non-tripping current", "Result", "Limit", "Margin", "Status"], [
        ["Current", f"{I_NT:.3f} A", "-", "-", "REPORT ONLY"],
        ["FET Tj (RthJA 50 K/W, Rds at 8.24 V gate, ESTIMATE)", f"{TJ_NT:.1f} degC (Rds {RDS_NT * 1e3:.2f} mohm)", f"{TJMAX:g} degC", f"{SOFT_TJ_MARGIN:.1f} K", est_status(SOFT_TJ_MARGIN)],
        ["Shunt power, judged: I^2 x R_hi at its self-heated element (corners mixed: a bound)", f"{P_NT_JUDGED:.3f} W (element {TE_NT:.1f} degC)", f"{RATED_SH:g} W", f"{SOFT_P_MARGIN:.3f} W", est_status(SOFT_P_MARGIN)],
        ["Shunt power, one unit: I^2 x R_lo (the low-R unit that does not trip), information", f"{P_NT_SAME:.3f} W (element {TE_NT_LO:.1f} degC)", f"{RATED_SH:g} W", f"{RATED_SH - P_NT_SAME:.3f} W", est_status(RATED_SH - P_NT_SAME)],
        ["Shunt element temperature (judged case), element limit ASSUMED", f"{TE_NT:.1f} degC", f"{T_ELEM_LIMIT:g} degC", f"{TE_NT_MARGIN:.1f} K", "ESTIMATE, " + ("PASS" if TE_NT_MARGIN >= 0 else "FAIL") + " only under the assumed limit"],
    ])
    soft_ok = SOFT_P_MARGIN >= 0 and SOFT_TJ_MARGIN >= 0
    md5 += ["",
            f"**Soft-short requirement: {'PASS' if soft_ok else 'FAIL'}** (ESTIMATE: it rests on the shunt thermal resistance read from the derating curve, the FET Rds model at 8.24 V "
            f"and RthJA at 6 cm2 of copper, a layout requirement). The shunt power margin is {SOFT_P_MARGIN / RATED_SH * 100:.1f} % of the rating: thin and estimate-based, flagged. "
            "Calibration cannot move the trip (bounded discrete V(SCP) rows over temperature), so none is credited.", "",
            "![Soft shorts](design-analysis/soft_shorts.png)", "",
            "Figure 5. FET Tj (top) and shunt power (bottom) against continuous current at 40 degC ambient; the shaded band is the trip window.", ""]
    md_all += md5

    # ---- section 6: steady state ----
    md6 = [f"## 6. Steady state at {I_DES:g} A", "",
           f"FET: Tj = TA + RthJA x I^2 x Rds8(Tj), solved self-consistently (residual below 1e-9 K); Rds8 is the ESTIMATE above. Shunt: R_hi at the self-heated element (ESTIMATE). ", ""]
    md6 += mdtable(["Quantity", f"TA {TA_MIN:g} degC", f"TA {TA_MAX:g} degC", "Limit", "Margin (at 40 degC)", "Status"], [
        ["FET Tj", f"{TJ_DES[TA_MIN]:.1f} degC", f"{TJ_DES[TA_MAX]:.1f} degC", f"{TJMAX:g} degC", f"{TJ_DES_MARGIN:.1f} K", est_status(TJ_DES_MARGIN)],
        ["FET Rds(on) at 8.24 V gate and Tj", f"{rds8(TJ_DES[TA_MIN]) * 1e3:.2f} mohm", f"{RDS_DES * 1e3:.2f} mohm", "-", "-", "ESTIMATE"],
        ["FET dissipation", f"{I_DES ** 2 * rds8(TJ_DES[TA_MIN]):.3f} W", f"{P_FET_DES:.3f} W", f"{fet.param('ptot_pcb').max:g} W (`ptot_pcb`, 25 degC)", f"{fet.param('ptot_pcb').max - P_FET_DES:.2f} W", status(fet.param('ptot_pcb').max - P_FET_DES)],
        ["Shunt element temperature", f"{TE_DES[TA_MIN]:.1f} degC", f"{TE_DES[TA_MAX]:.1f} degC", f"{T_ELEM_LIMIT:g} degC (ASSUMED)", f"{TE_DES_MARGIN:.1f} K", "ESTIMATE under the assumed limit"],
        ["Shunt resistance, worst high (BOL)", f"{R_SH_DES[TA_MIN] * 1e3:.3f} mohm", f"{R_SH_DES[TA_MAX] * 1e3:.3f} mohm", "-", "-", "-"],
        ["Shunt power", f"{P_SH_DES[TA_MIN]:.3f} W", f"{P_SH_DES[TA_MAX]:.3f} W", f"{RATED_SH:g} W", f"{P_SH_MARGIN:.3f} W", est_status(P_SH_MARGIN)],
        ["BUS_IN to BUS_OUT drop (shunt R_hi + FET)", f"{DROP[TA_MIN] * 1e3:.1f} mV", f"{DROP[TA_MAX] * 1e3:.1f} mV", "report", "-", "REPORT ONLY (ESTIMATE)"],
    ])
    md6 += ["",
            f"Shunt rated working voltage (P x R)^1/2 at the lowest R: {MWV * 1e3:.1f} mV against {V_SH_DES_MAX * 1e3:.1f} mV at {I_DES:g} A ({cite(shl.param('max_working_voltage'))}). "
            f"Case temperature at the steady point (Tj - P x RthJC): {TC_DES:.1f} degC.", "",
            f"TPS48000 junction temperature: Tj = TA + rth_ja x P, rth_ja = {RTHJA_TPS:g} K/W is TYPICAL ONLY (no JEDEC board stated), so this is an ESTIMATE: "
            f"P = {P_TPS_TOT * 1e3:.2f} mW ({VBUS_HI:g} V x i_gnd_full max {IGND.max * 1e6:g} uA plus the FLT/PGOOD sinks), Tj = {TJ_TPS:.2f} degC at {TA_MAX:g} degC, "
            f"{TJ_TPS_MARGIN:.1f} K below `tj_abs` {TJ_ABS_TPS:g} degC ({cite(tps.param('tj_abs'))}). Gate-drive switching power is negligible (a few ms of charge pump per turn-on).", ""]
    I_BUS = lambda v: (IGND.max, ISHDN.max, v / (RT_DIV * RF_LO), v / (RT_DIV * RF_HI))
    md6 += ["Quiescent current and power budget:", ""]
    bud_rows = []
    for v in (VBUS_BENCH, VBUS_HI):
        ig, ish, idv_hi, idv_lo = I_BUS(v)
        bud_rows.append([f"BUS_IN at {v:g} V, enabled (`i_gnd_full` max)", f"{ig * 1e6:.1f} uA + ladder {idv_lo * 1e6:.1f} to {idv_hi * 1e6:.1f} uA", f"{v * (ig + idv_hi) * 1e3:.3f} mW", cite(IGND)])
        bud_rows.append([f"BUS_IN at {v:g} V, shutdown (EN/UVLO low, `i_shdn` max)", f"{ish * 1e6:.1f} uA + ladder {idv_lo * 1e6:.1f} to {idv_hi * 1e6:.1f} uA", f"{v * (ish + idv_hi) * 1e3:.3f} mW", cite(ISHDN)])
    bud_rows.append([f"V3V3, FAULT_N asserted (pull-up {fmt_ohm(R_FPU)}, V3V3 max, R min)", f"{I_FPU_MAX * 1e3:.3f} mA", f"{V3V3_MAX * I_FPU_MAX * 1e3:.3f} mW", "pull-up from the ERJ-3EKF rows"])
    bud_rows.append([f"V3V3, PGOOD_N asserted (pull-up {fmt_ohm(R_PPU)})", f"{I_PPU_MAX * 1e3:.3f} mA", f"{V3V3_MAX * I_PPU_MAX * 1e3:.3f} mW", "pull-up from the ERJ-3EKF rows"])
    bud_rows.append(["FLT / FLT_GD low level (R_pd max x sink current)", f"{VOL_FLT * 1e3:.1f} mV", "-", cite(tps.param("r_flt_pd"))])
    md6 += mdtable(["Supply and state", "Current", "Power", "Basis"], bud_rows)
    md6 += ["", "The ladder current flows at all times (it is the cost of the UVLO/OV monitor); `i_gnd_full` is a full-range bound that covers the charge pump (its -40 to 85 degC subset `i_gnd_m40_85` is covered by it).", ""]
    md_all += md6

    # ---- section 7: turn-on ----
    md7 = ["## 7. Turn-on", "",
           f"Gate network (DS Fig 7-4 p.15): PU -> R1 -> gate, PD tied to the gate, gate -> R2 -> C1 -> GND (designer correction D12, C1 not to SRC). Gate ramp: slew = 0.63 x V(BST-SRC) / (R1 x C1) (DS eq. 3 solved for IINRUSH / CLOAD; R2 = {fmt_ohm(R2G)} only damps). Corners: slow = V(BST-SRC) min {VBST.min:g} V, C1 max "
           f"({C1_MAX * 1e9:.3f} nF), R1 max; fast = V(BST-SRC) max {VBST.max:g} V, C1 min ({C1_MIN * 1e9:.3f} nF), R1 min. Initial delay before the ramp: t_drv_en (section 11).", ""]
    md7 += mdtable(["Quantity", "Slow corner", "Typical (information)", "Fast corner"], [
        ["V(BST-SRC) (`v_bst_src_on` min / typ, `v_bst_src_off` max)", f"{VBST.min:g} V", f"{VBST.typ:g} V", f"{VBST.max:g} V"],
        ["Slew rate", f"{SLEW['slow'] / 1e3:.3f} V/ms", f"{SLEW['typ'] / 1e3:.3f} V/ms", f"{SLEW['fast'] / 1e3:.3f} V/ms"],
        [f"Inrush into {CL * 1e6:g} uF (CLOAD x slew)", f"{INRUSH['slow']:.3f} A", f"{INRUSH['typ']:.3f} A", f"{INRUSH['fast']:.3f} A"],
        [f"Ramp time at {VBUS_BENCH:g} V", f"{VBUS_BENCH / SLEW['slow'] * 1e3:.3f} ms", f"{VBUS_BENCH / SLEW['typ'] * 1e3:.3f} ms", f"{VBUS_BENCH / SLEW['fast'] * 1e3:.3f} ms"],
        [f"Ramp time at {VBUS_HI:g} V", f"{VBUS_HI / SLEW['slow'] * 1e3:.3f} ms", f"{VBUS_HI / SLEW['typ'] * 1e3:.3f} ms", f"{VBUS_HI / SLEW['fast'] * 1e3:.3f} ms"],
    ])
    md7 += ["",
            f"FET check with the load drawing current from t = 0 (no PGOOD wait): constant {I_DES:g} A, and resistive ({VBUS_HI:g} V / {I_DES:g} A scaled to each bus voltage so the current is {I_DES:g} A at full output). "
            f"The FET carries load current plus CLOAD x slew while it drops the bus. Tj = {TA_MAX:g} degC case + superposition of ZthJC step responses over the power waveform, then the steady {I_DES:g} A "
            f"dissipation (ESTIMATE, section 2). SOA: I_eq = P_peak / VBUS against the TC 125 degC curve rows (log-log in VDS and pulse width, typical curve reads, ESTIMATE); the TC 25 degC curve is shown for information. "
            f"Pass = Tj <= {TJMAX:g} degC and I_eq <= the SOA current.", ""]
    rows7 = []
    for (load, cname, vb), r in TURN.items():
        rh = TURN_HOT[(load, cname, vb)]
        rows7.append([f"{'const. ' + format(I_DES, 'g') + ' A' if load == 'CC' else 'resistive'}", cname.split(" (")[0], f"{vb:g}", f"{r['T'] * 1e3:.2f}", f"{r['ipk']:.2f}", f"{r['pmax']:.0f}",
                      f"{r['tj']:.1f}", f"{r['ieq']:.2f} / {r['r125']:.2f}" if r["r125"] else f"{r['ieq']:.2f} / -", f"{r['r25']:.2f}" if r["r25"] else "-", f"{rh['tj']:.1f}",
                      est_status(1.0) if r["ok"] else "ESTIMATE, FAIL"])
    md7 += mdtable(["Load", "Ramp corner", "VBUS (V)", "Ramp (ms)", "Peak FET I (A)", "Peak P (W)", "Tj peak (degC)", "I_eq / SOA TC125 (A)", "SOA TC25 (A, info)", f"Tj peak, case at {TC_HOT:.0f} degC (restart, info)", "Status"], rows7)
    md7 += ["",
            f"Worst Tj {TJ_TURN_MAX:.1f} degC ({TURN_TJ_MARGIN:.1f} K margin); worst I_eq / SOA {SOA_RATIO:.3f}. Restart sensitivity (case at the {I_DES:g} A steady-state temperature {TC_HOT:.1f} degC instead of {TA_MAX:g} degC): "
            f"{'all cases still pass' if TURN_HOT_OK else 'at least one case FAILS'}.", "",
            f"**Largest passing bus voltage (required output):** sweep {VBUS_LO:g} to {GRID_V[-1]:g} V in 0.5 V steps (pass is contiguous from {VBUS_LO:g} V; monotonic over the sweep: {MONO}):", ""]
    md7 += mdtable(["Load", "Ramp corner", "Largest passing VBUS (V)", "First failing VBUS"],
                   [[f"{'const. ' + format(I_DES, 'g') + ' A' if load == 'CC' else 'resistive'}", cname.split(" (")[0], f"{PASS_V[(load, cname)]:g}",
                     next((f"{r['vb']:g} V: " + ("Tj" if not r['ok_tj'] else "SOA") for r in SWEEP[(load, cname)] if not r['ok']), f"none up to {GRID_V[-1]:g} V")] for load, cname in SWEEP])
    md7 += ["",
            f"**Result: {V_PASS_ALL:g} V** (the lowest of the four), against the {VBUS_HI:g} V rating: **{'PASS' if V_PASS_ALL >= VBUS_HI else 'FAIL'}**, margin {V_PASS_ALL - VBUS_HI:+.1f} V ({(V_PASS_ALL / VBUS_HI - 1) * 100:.0f} % above the rating). "
            "This is a FET SOA and Tj statement for the turn-on ramp; the block's bus rating is also limited by the controller pins (section 10) and the instance's OV trip. "
            f"The SOA curve rows are typical only, so the margin is an ESTIMATE; the largest passing voltage moves with the ramp corners: the constant-current load is the limiting case. "
            f"The peak FET current in the ramp is {max(r['ipk'] for r in TURN.values()):.2f} A, below the trip minimum {TRIP_MIN:.2f} A (SCP is also blanked until V(G_GOOD), DS p.17). "
            f"The {I_DES:g} A design current is the rating: above it the rating does not hold (spec Load turn-on rating).", "",
            "![Turn-on](design-analysis/turn_on.png)", "",
            f"Figure 7. FET Tj peak (left) and I_eq / SOA limit (right) against bus voltage for the two loads and the two ramp corners. Dotted line: {VBUS_HI:g} V rating.", ""]
    md_all += md7

    # ---- section 8: short circuit and turn-off ----
    rows_ts = [
        ["C", f"{CTMR * 1e9:g} nF {SMALL_DIEL} {SEL_CT.tolerance}: {CT_LO:.4f} to {CT_HI:.4f} x", C_TXT],
        ["RTMR", f"{fmt_ohm(RTMR)}: {RF_LO:.4f} to {RF_HI:.4f} x", "ERJ-3EKF tolerance + TCR over TA"],
        ["I(TMR_SRC_CB)", f"{ITMR.min * 1e6:g} / {ITMR.typ * 1e6:g} / {ITMR.max * 1e6:g} uA", cite(ITMR)],
        ["V(TMR_SC)", f"{VTMR.min:g} / {VTMR.typ:g} / {VTMR.max:g} V", cite(VTMR)],
    ]
    md8 = ["## 8. Short circuit and turn-off", "",
           "Latch-off delay (blanking time before the latch, DS p.19 eq. 8): tSC = C / [RTMR x ln(1 / (1 - 1.1 / (RTMR x I)))]; eq. 8 is linear in tSC, so the library function `ctmr_latch` evaluated at tSC = 1 s gives the factor. "
           "Inputs:", ""]
    md8 += mdtable(["Input", "Range", "Basis"], rows_ts)
    md8 += ["",
            f"- tSC per eq. 8 as printed (1.1 V constant): **{TSC_EQ8[0] * 1e6:.1f} to {TSC_EQ8[1] * 1e6:.1f} us** (nominal {TSC_NOM * 1e6:.1f} us at I typ and nominal C); minimum = C min, I max, R max; maximum = C max, I min, R min.",
            f"- With the TMR threshold V(TMR_SC) as a variable (not a DS equation: eq. 8 prints 1.1 V and the table spans {VTMR.min:g} to {VTMR.max:g} V): {TSC_EXT[0] * 1e6:.1f} to {TSC_EXT[1] * 1e6:.1f} us. "
            "This wider bound is used for the fault energy below.",
            f"- Time at the fault current used below: tSC max + `t_sc_hard` max ({T_OFF_DELAY * 1e6:g} us, comparator to PD falling with CTMR open; the same delay is ASSUMED after the timer) = {T_FAULT * 1e6:.1f} us.", "",
            f"**FET, prospective fault current {I_FAULT:g} A** (the FET is assumed fully enhanced: V(DS) = I x Rds, the source is current-limited at {I_FAULT:g} A; a stiff voltage source across a linear-mode FET is not covered). "
            f"Tj = Tj0 + I^2 x ZthJC({T_FAULT * 1e6:.0f} us) x Rds8(Tj) (ESTIMATE; ZthJC {Z_FAULT:.4f} K/W from the single-pulse curve). Tj0 is the state before the fault.", ""]
    md8 += mdtable(["Quantity", "Result", "Limit", "Margin", "Status"], [
        [f"FET Tj at the end of the fault, from the {I_DES:g} A steady state (Tj0 {TJ0_DES:.1f} degC)", f"{TJ_FAULT['design']:.1f} degC", f"{TJMAX:g} degC", f"{TJMAX - TJ_FAULT['design']:.1f} K", est_status(TJMAX - TJ_FAULT['design'])],
        [f"FET Tj at the end of the fault, from the soft-short state (Tj0 {TJ0_SOFT:.1f} degC), judged", f"{TJ_FAULT['soft']:.1f} degC", f"{TJMAX:g} degC", f"{TJMAX - TJ_FAULT['soft']:.1f} K", est_status(TJMAX - TJ_FAULT['soft'])],
        ["Fault current at which Tj reaches the limit (soft-short start)", f"{I_FAULT_LIMIT:.0f} A", f">= {I_FAULT:g} A", f"{I_FAULT_LIMIT - I_FAULT:+.0f} A", est_status(I_FAULT_LIMIT - I_FAULT)],
        [f"Pulsed drain current (`id_pulse`, {cite(fet.param('id_pulse'))})", f"{I_FAULT:g} A", f"{IDP:g} A", f"{IDP_MARGIN:.0f} A", status(IDP_MARGIN)],
        [f"Shunt in the fault: power {P_SH_FAULT:.0f} W (R_hi {R_SH_FAULT * 1e3:.2f} mohm), energy", f"{E_SH_FAULT * 1e3:.2f} mJ", "no DS bound: `perf_short_time_overload` gives +/-0.5 % + 0.5 mohm at an unprinted overload level", "-", "REPORT ONLY (UNBOUNDED)"],
        ["CS+ to CS- in the fault (I x R_hi)", f"{V_CS_FAULT:.3f} V", f"{CS_PAIR.max:g} V (`abs_cs_pair`, {cite(CS_PAIR)})", f"{CS_PAIR.max - V_CS_FAULT:.1f} V", status(CS_PAIR.max - V_CS_FAULT)],
    ])
    md8 += ["",
            f"Saturation current at VGS 8 V, VDS 5 V is {I_SAT:g} A TYPICAL (`ioutput_tj25_vgs8v_vds5v`, Tj 25 degC, curve): the saturation current at the 8.24 V minimum drive and at hot Tj is not bounded, "
            f"so the assumption that the FET stays ohmic at {I_FAULT:g} A cannot be proven from the library. ", "",
            f"**Turn-off avalanche** (no TVS, no output diode; designer decision): E = 0.5 L I^2 x V(BR) / (V(BR) - VBUS), the energy of the lead inductance into an ideal clamp at V(BR) min = {VBR:g} V "
            f"({cite(fet.param('vbr_dss'))}); not a DS equation. The `eas` row ({EAS * 1e3:g} mJ, {cite(fet.param('eas'))}) is valid only at 50 A and RGS = 25 ohm, so it is compared for orientation, not as a bound above 50 A. "
            "The energy of the load and supply capacitance is excluded.", ""]
    rows_av = []
    for l in L_LEAD:
        rows_av.append([f"{l * 1e6:g}"] + [f"{E_AV[(l, i, vb)] * 1e3:.2f}" for i in (I_FAULT, TRIP_MAX) for vb in (VBUS_BENCH, VBUS_HI)])
    md8 += mdtable(["L (uH)", f"{I_FAULT:g} A, {VBUS_BENCH:g} V (mJ)", f"{I_FAULT:g} A, {VBUS_HI:g} V (mJ)", f"{TRIP_MAX:.1f} A, {VBUS_BENCH:g} V (mJ)", f"{TRIP_MAX:.1f} A, {VBUS_HI:g} V (mJ)"], rows_av)
    md8 += ["",
            f"Worst of the sweep {E_AV_MAX * 1e3:.1f} mJ, {EAS / E_AV_MAX:.1f} x below the 50 A `eas` row: **ESTIMATE** (the row does not cover these currents). "
            f"Open item, not judged: without a TVS the clamp level of the input node during turn-off is the FET avalanche (V(BR) min {VBR:g} V, not a maximum), which is at or above the controller's `abs_vs_cs_gnd` "
            f"{tps.param('abs_vs_cs_gnd').max:g} V (VS, CS+, CS- to GND; the 70 V `abs_input_vs_pos` row covers EN/UVLO, OV, INP and CS_SEL, which see BUS_IN only through the divider or logic) for a long enough input lead inductance with no local bulk capacitance; DS p.27-28 recommends a TVS. The static VDS check is in section 10.", "",
            "![Short circuit and turn-off](design-analysis/fault.png)", "",
            f"Figure 8. Left: FET Tj at the end of a {T_FAULT * 1e6:.0f} us fault against fault current. Right: avalanche energy against lead inductance.", ""]
    md_all += md8

    # ---- section 9: gate drive ----
    md9 = ["## 9. Gate drive", "",
           f"V(BST-SRC) ON min {VG_ON.min:g} V ({cite(VG_ON)}) against V(G_GOOD) max {VG_GOOD.max:g} V ({cite(VG_GOOD)}). The corners overlap by {GATE_OVERLAP:.2f} V: on a worst-case part the gate "
           "drive can be below the power-good threshold, so PGOOD_N may not assert or may chatter, and short-circuit protection (monitored only after G_GOOD, DS p.17) may be intermittently blanked. "
           "Designer decision 2026-11-15: no TI query; carried as an ACCEPTED RESIDUAL RISK, verified on built units by measuring V(BST-SRC) minimum ripple and FLT_GD behaviour. "
           f"Status: **FAIL as a corner check (OPEN ITEM, accepted)**, margin {-GATE_OVERLAP:+.2f} V.", ""]
    md9 += mdtable(["Check", "Applied", "Limit", "Margin", "Status"], [
        ["V(BST-SRC) / gate to SRC, highest charge-pump row", f"{VBST_REG_MAX:g} V", f"`abs_gate_bst_src` {tps.param('abs_gate_bst_src').max:g} V", f"{tps.param('abs_gate_bst_src').max - VBST_REG_MAX:.2f} V", status(tps.param('abs_gate_bst_src').max - VBST_REG_MAX)],
        ["FET VGS, same value", f"{VBST_REG_MAX:g} V", f"`vgs` {fet.param('vgs').max:g} V", f"{fet.param('vgs').max - VBST_REG_MAX:.2f} V", status(fet.param('vgs').max - VBST_REG_MAX)],
        ["BST, PU, PD to GND", f"{VBUS_HI + VBST_REG_MAX:g} V", f"`abs_gate_out_gnd` {tps.param('abs_gate_out_gnd').max:g} V", f"{tps.param('abs_gate_out_gnd').max - VBUS_HI - VBST_REG_MAX:.2f} V", status(tps.param('abs_gate_out_gnd').max - VBUS_HI - VBST_REG_MAX)],
        ["Gate drive at the minimum for the Rds(on) estimate", f"{VGS_ON_MIN:g} V", "Rds only bounded at 10 V", "-", "ESTIMATE (typical VGS ratio)"],
    ])
    md9 += ["",
            f"**Bootstrap capacitor.** Needed: {CBST_MIN_WC * 1e9:.1f} nF (eq. 4, Qg max, C1 max; nominal C1 {CBST_MIN_NOM * 1e9:.1f} nF). Fitted one {CBST * 1e9:g} nF X7R 50 V 0805 (GCM21B, designer decision D14) = {CBST * 1e9:g} nF nominal; "
            f"low corner over tolerance and temperature {CBST_LOW * 1e9:.1f} nF ({CB_TXT}); recommended minimum {CBST_REC * 1e9:g} nF ({cite(tps.param('rec_cap_bst_src'))}). "
            f"The DC-bias capacitance loss of the GCM21B X7R at about {VBST_REG_MAX:g} V is **NOT BOUNDED in the library** (DS p.13 shows an example curve only; `dc_bias`: not specified), and aging loss likewise. "
            f"It cannot be stated here: the capacitor keeps the design only if at least {CBST_KEEP * 100:.0f} % of the low-corner capacitance survives DC bias and aging (allowed loss {CBST_ALLOW * 100:.0f} %, `cbst_dc_bias_allow_frac`). "
            "Verify on a built unit (BST ripple) or from the manufacturer's DC-bias tool. The same holds for the VS and SRC bypass capacitors at 40 V (DS minimum 22 nF).", ""]
    md_all += md9

    # ---- section 10: ratings ----
    md10 = ["## 10. Ratings (absolute maximum and recommended operating)", "",
            "Applied values are the worst case of the declared voltages and the sections above. Margin = min(applied - rating min, rating max - applied).", ""]
    md10 += mdtable(["Check", "Applied (worst case)", "Rating (library key)", "Margin", "Result", "Cite"], [r for r, m in RATINGS])
    md10 += ["",
             f"The gate-drive overlap row is the accepted residual risk of section 9; every other row passes: {'yes' if not RATING_FAIL else 'NO: ' + ', '.join(RATING_FAIL)}. The ESD and storage-temperature ratings are rating-only (owner: assembly). "
             "OV has no recommended-operating row in the library (the DS recommended table covers EN/UVLO, INP and CS_SEL): the absolute row is used. Transient limits (hot-plug, turn-off inductive spike) are not covered by DC rows: see section 8 and 12.", ""]
    md_all += md10

    # ---- section 11: firmware ----
    T_EN_OFF = tps.param("t_pd_en_off")
    md11 = ["## 11. Firmware and host notes", "",
            "- **Fault latch.** After a short-circuit trip the switch is latched off and FAULT_N is low. The latch is reset by toggling EN low then high (spec.md, DS p.19); the DS prints no minimum low time, so a minimum EN-low time is an OPEN ITEM (measure on the bench).",
            f"- **EN levels.** EN drives INP: V(INP_H) max {tps.param('v_inp_h').max:g} V (a high level at or above it turns the FET on) and V(INP_L) min {tps.param('v_inp_l').min:g} V (a low level at or below it turns the FET off), {cite(tps.param('v_inp_h'))}. The {V3V3_MIN:.2f} V rail minimum high level satisfies the high threshold; the host's low level must be at or below {tps.param('v_inp_l').min:g} V.",
            f"- **PGOOD_N** is a status output for the host (FET fully enhanced, FLT_GD low when V(PD-SRC) exceeds V(G_GOOD)), not a load interlock. Because of the gate-drive overlap (section 9) it may not assert on a worst-case part: do not use it to gate safety functions.",
            f"- **FAULT_N** is low on short circuit, UVLO, OV or SCP diagnosis (DS p.3).",
            f"- **Timing.** First enable after power-up or UVLO: t_drv_en (initial gate-driver enable delay, eq. 1) = {T_DRV_NOMINAL * 1e3:.1f} ms at the nominal {CBST * 1e9:g} nF, range {T_DRV_MIN * 1e3:.1f} to **{T_DRV_WORST * 1e3:.1f} ms worst case** (max: CBST at its high corner {CBST * CB_HI * 1e9:.0f} nF, V(BST_UVLOR) max {tps.param('v_bst_uvlor').max:g} V, I(BST) min {tps.param('i_bst').min * 1e6:g} uA; min: CBST low corner, V(BST_UVLOR) min, I(BST) max; {cite(tps.param('i_bst'))}). "
            f"Then the ramp: {VBUS_BENCH / SLEW['fast'] * 1e3:.2f} to {VBUS_BENCH / SLEW['slow'] * 1e3:.2f} ms at {VBUS_BENCH:g} V ({VBUS_HI / SLEW['fast'] * 1e3:.2f} to {VBUS_HI / SLEW['slow'] * 1e3:.2f} ms at {VBUS_HI:g} V), "
            f"then FLT_GD (PGOOD_N) low after t_pd_flt_gd_on {tps.param('t_pd_flt_gd_on').typ * 1e6:g} us typ (no max printed). A later EN rising edge with BST already charged: INP to PU in {tps.param('t_pu_inp_h').max * 1e6:g} us max. "
            f"Turn-off: EN falling to PD falling {T_EN_OFF.max * 1e6:g} us max ({cite(T_EN_OFF)}). A host should wait at least the worst-case t_drv_en plus the longest ramp before reporting a start-up timeout (`TIMEOUT` = {T_DRV_WORST * 1e3 + VBUS_HI / SLEW['slow'] * 1e3:.0f} ms at {VBUS_HI:g} V).",
            f"- **Latch-off delay** tSC = {TSC_EQ8[0] * 1e6:.0f} to {TSC_EQ8[1] * 1e6:.0f} us (eq. 8; up to {TSC_EXT[1] * 1e6:.0f} us with V(TMR_SC) max): overcurrent shorter than the minimum is ridden through.",
            f"- **Trip window** {TRIP_MIN:.2f} to {TRIP_MAX:.2f} A: a load between {I_DES:g} A and the trip maximum may not trip and is not protected by a latch (the soft-short requirement, section 5, is met by survival).", ""]
    md_all += md11

    # ---- section 12: limits ----
    md12 = ["## 12. Limits of this analysis", "",
            "- DC and single-event worst case: no board-level transient simulation (hot-plug, supply ripple, load transients), no EMI. The turn-off inductive spike and the input-node clamp are not analysed beyond the avalanche energy estimate (section 8).",
            f"- Curve-read values are ESTIMATES: the FET SOA rows (typical, TC 25 and 125 degC), ZthJC single-pulse rows, the Rds(on) temperature curve and the Rds(8.24 V)/Rds(10 V) ratio. Only `rds_on_10v` max is a bound. RthJA {RTHJA:g} K/W holds only with 6 cm2 of copper (a layout requirement, not started).",
            f"- Shunt: the thermal resistance ({THETA:g} K/W) is read from the derating graph (ESTIMATE); the element limit ({T_ELEM_LIMIT:g} degC) is an ASSUMPTION; the TCR reference temperature ({T_REF:g} degC) and its validity span are not in the DS. The solder-heat BOL term is a qualification test limit used as a bound. EOL drift (load life, humidity) is out of scope.",
            "- The trip window applies the V(SCP) table row for the exact RISCP value; the CS input offset and the comparator delay are not separate terms (the row is a threshold at the CS pins). The RISCP effect uses the eq. 5 slope of 2 uA, a library equation, not a bound.",
            f"- Soft-short shunt power mixes corners (R_hi at the current of the low-R trip maximum): a bound, so conservative; the one-unit value is reported beside it.",
            "- tSC uses the library equation (1.1 V printed) and, separately, the V(TMR_SC) row bounds (not a DS equation). The turn-off delay after the timer (`t_sc_hard`) is an ASSUMPTION.",
            "- The fault Tj assumes the FET fully enhanced at the fault current; the saturation current at the minimum gate drive and at high Tj is typical-only (not bounded). The fault is a current-limited source, not a stiff voltage source across a linear-mode FET.",
            "- Avalanche energy: ideal clamp at V(BR) min, loop inductance only; the `eas` row is valid at 50 A only; the avalanche curve rows (`iav_*`) are typical and stop at 50 A.",
            f"- The shunt short-time overload (`perf_short_time_overload`) states no overload level: the fault energy in the shunt ({E_SH_FAULT * 1e3:.1f} mJ) is reported, not judged.",
            f"- DC-bias and aging capacitance loss of the X7R parts (CBST, GCM21B 470 nF; VS and SRC bypass, GCM188 100 nF) is not bounded in the library (section 9). The C0G parts (C1, CTMR) have no DC-bias loss, but the C0G temperature drift below 25 degC is an estimate from the -10 degC drift row.",
            f"- C1 dump (not a DS equation): C1 at {V_C1_MAX:.2f} V discharges through R2 into PD when the gate is pulled to SRC: peak {C1_DUMP_PEAK:.2f} A (R2 min), {C1_DUMP_PW:.0f} W peak in R2, energy {C1_DUMP_E * 1e6:.2f} uJ, tau {C1_DUMP_TAU * 1e9:.0f} ns. The PD peak sink is printed only as 2 A typical (`i_pd_peak`, DS p.7): NOT BOUNDED. The R2 single-pulse rating is NOT BOUNDED (the ERJ-3EKF library has no pulse row): open item.",
            "- TPS48000 junction temperature uses `rth_ja` typical only (ESTIMATE). EN/UVLO and OV pin leakage has no MIN printed: used at both signs and at the 70 V maximum for all bus voltages.",
            "- ERJ-3EKF tolerance is referenced to 20 degC and the TCR to 25 degC (library notes); BOL (soldering heat +/-1 %) is a sensitivity only (section 4).",
            "- No reverse-polarity blocking, no input TVS, no output diode (designer decision): DS p.27-28 recommends them; open items in the spec.",
            "- The V(G_GOOD) / V(BST-SRC) ON overlap is an accepted residual risk (section 9).",
            "- The DS example numbers marked `DS` in section 3 are quoted from the datasheet text, not from `component.toml` (adding them to the library is a library change).",
            f"- Library: {LIB_TEXT}. The FET curve rows (`soa_*`, `zthjc_*`, `rds_*`, `ioutput_*`) "
            + ("are in this release." if zl_released() else
               "exist only on the unreleased branch; results must be regenerated after the release tag is pinned."), ""]
    md_all += md12

    # %% [markdown]
    # ## Assemble and write

    # %%
    HEADER = [
        "# TPS48000HighSideSwitch block: design analysis", "",
        "> GENERATED by `python3 blocks/TPS48000HighSideSwitch/analysis.py` (see its header for --params and --out). Do not edit this file; change the script or the params and re-run.", "",
        f"- Library: {LIB_TEXT}.",
        f"- Results date: {RUN_DATE}.",
        f"- Instance `{INSTANCE}`, params `{PARAMS_REL}`. Key results: `design-analysis/values.json` (generated by the same run).", "",
        f"High-side electronic switch: TPS48000QDGXQ1 + ISC035N10NM5LF2 + {R0 * 1e3:g} mohm WSK2512-LOW shunt, {I_DES:g} A design current, "
        f"bus {VBUS_LO:g} to {VBUS_HI:g} V (bench {VBUS_BENCH:g} V), TA {TA_MIN:g} to {TA_MAX:g} degC.", "",
        "Conventions: PASS / FAIL against a datasheet, `spec.md` or the designer's requirement; ESTIMATE for curve-based, typical-based or assumption-based results (the status then says PASS or FAIL of the estimate); "
        "REPORT ONLY where no limit is set; OPEN ITEM for zero margin or an accepted risk; UNBOUNDED where the datasheet gives no bound.", "",
        "## Contents", "",
        "1 summary; 2 operating conditions and inputs; 3 DS 8.2.2 design-procedure walk-through; 4 UVLO/OV divider; 5 trip window and soft shorts; 6 steady state; "
        "7 turn-on; 8 short circuit and turn-off; 9 gate drive; 10 ratings; 11 firmware and host notes; 12 limits of this analysis.", "",
    ]
    md1 = ["## 1. Summary of key results", ""]
    md1 += mdtable(["Key", "Result", "Value (worst case)", "Limit", "Margin", "Status"], SUMMARY)
    md1 += ["", f"Divider selected for the bench (params): r_top {fmt_ohm(R_DIV[0])}, r_mid {fmt_ohm(R_DIV[1])}, r_bot {fmt_ohm(R_DIV[2])}.", ""]
    FINAL = HEADER + md1 + md_all
    DOCS.mkdir(parents=True, exist_ok=True)
    FINAL += inputs_section()  # library rows read and their `verified` stamps (zener-library lib-api.md)
    write_report(MD_PATH, "\n".join(FINAL) + "\n", unverified=unverified_inputs())  # .md unless only the results date changed, then the PDF (md hash stamp)

    LIB_PINS = ("Released: Zener deps ISC035N10NM5LF2 v0.4.0 and TPS48000QDGXQ1 v0.4.0; Python library zener-library "
                f"{ZL_VERSION} "
                "(ZL_VERSION in analysis/_zl.py). The pin-pair v_<key> configs and FET curve rows are in these releases "
                "(before the release: dev commit f563e7e and feat/isc035-soa @ 0426982).")
    VSENSE_MAX = math.ceil(round(I_NT * r_hi(TE_NT) * 1e4, 6)) / 1e4  # V, rounded UP to 0.1 mV
    IR125 = 10e9  # ohm: `ir_125c_c0g` 'more than 10000 Mohm' of the series reference part (row restricted to GCM1885C1H101GA16): ASSUMED for the 6.8 nF part
    I_LEAK_C1 = V_C1_MAX / IR125
    V_RDAMP_DC = max(1e-7, math.ceil(I_LEAK_C1 * R2G * 1e7) / 1e7)  # V
    R_REV_I = 1.0 / R_SH_FAULT  # A at which a reverse CS pair reaches abs min -1 V
    VALUES.update({
        "v_vgs_range": [0.0, VBST_REG_MAX], "v_cs_pair_range": [-V_CS_FAULT, V_CS_FAULT], "v_vds_range": [-fet.param("vsd").max, VBUS_HI], "vsense_max": VSENSE_MAX, "vsense_op_max": math.ceil(V_SH_DES_MAX * 1e4) / 1e4, "v_rdamp_dc": V_RDAMP_DC,
        "i_design_a": I_DES, "rshunt_mohm": R0 * 1e3, "mpn_shunt": MPN_SHUNT, "mpn_controller": tps.info.get("mpn"),
        "r_top_ohm": R_DIV[0], "r_mid_ohm": R_DIV[1], "r_bot_ohm": R_DIV[2], "riscp_ohm": RISCP, "ctmr_nf": CTMR * 1e9, "rtmr_ohm": RTMR,
        "c1_nf": C1G * 1e9, "r1_ohm": R1G, "r2_ohm": R2G, "cbst_nf_nominal": CBST * 1e9, "cbst_min_nominal_c1_nf": CBST_MIN_NOM * 1e9,
        "cbst_low_corner_nf": CBST_LOW * 1e9, "cbst_dc_bias": "NOT BOUNDED in the library",
        "cbst_nf": CBST * 1e9, "cbyp_nf": CBYP * 1e9, "vscp_mv_min": VSCP.min * 1e3, "vscp_mv_max": VSCP.max * 1e3, "vscp_adj_mv_min": VSCP_MIN * 1e3, "vscp_adj_mv_max": VSCP_MAX * 1e3,
        "tdrv_en_min_ms": T_DRV_MIN * 1e3, "tdrv_en_max_ms": T_DRV_MAX * 1e3, "c1_dump_energy_uj": C1_DUMP_E * 1e6,
        "c1_dump_tau_ns": C1_DUMP_TAU * 1e9, "c1_dump_peak_w": C1_DUMP_PW, "cbst_dc_bias_allow_frac": CBST_ALLOW,
        "uvlo_rise_v": list(W["uvr"]), "uvlo_fall_v": list(W["uvf"]), "ov_rise_v": list(W["ovr"]), "ov_fall_v": list(W["ovf"]),
        "trip_window_a": [TRIP_MIN, TRIP_MAX], "tsc_us": [TSC_EQ8[0] * 1e6, TSC_EQ8[1] * 1e6],
        "inrush_a": [INRUSH["slow"], INRUSH["fast"]], "ramp_ms_12v": [VBUS_BENCH / SLEW["fast"] * 1e3, VBUS_BENCH / SLEW["slow"] * 1e3],
        "ramp_ms_40v": [VBUS_HI / SLEW["fast"] * 1e3, VBUS_HI / SLEW["slow"] * 1e3], "turnon_pass_v_by_case": {f"{k[0]}|{k[1].split(' (')[0]}": v for k, v in PASS_V.items()},
        "t_drv_en_nominal_ms": T_DRV_NOMINAL * 1e3, "small_cap": CAP_SMALL_TXT, "c0g_preferred_available": C_PREF_OK, "bom_lines": N_BOM,
        "divider_matches_params": DIV_MATCH, "library": LIB_PINS,
    })
    TEXT.update({
        "v_vgs_range": f"0 to {VBST_REG_MAX:g} V: PD pulls the gate to SRC (0 V; abs min {tps.param('abs_gate_bst_src').min:g} V); the maximum is V(BST-SRC_OFF) max, the highest charge-pump row (`v_bst_src_off` {VBST_OFF.max:g} V, DS p.6), below the BST abs max {tps.param('abs_gate_bst_src').max:g} V and the FET VGS {fet.param('vgs').max:g} V",
        "v_cs_pair_range": f"{-V_CS_FAULT:.3f} to +{V_CS_FAULT:.3f} V: +/-{I_FAULT:g} A x R_hi {R_SH_FAULT * 1e3:.3f} mohm (designer decision D10: the {I_FAULT:g} A prospective fault-current requirement applies in both directions, including reverse discharge of the load capacitance into an upstream short); abs {CS_PAIR.min:g} to {CS_PAIR.max:g} V (`abs_cs_pair`)",
        "v_vds_range": f"{-fet.param('vsd').max:g} to {VBUS_HI:g} V: off-state with the output at 0 V gives the bus maximum; reverse is the body-diode drop (`vsd` max {fet.param('vsd').max:g} V, DS p.6); the turn-off avalanche transient is excluded (see `turnoff_transient_no_tvs`)",
        "vsense_op_max": f"{math.ceil(V_SH_DES_MAX * 1e4) / 10:.1f} mV: shunt voltage at the {I_DES:g} A design current, R_hi (self-heated, BOL), rounded up to 0.1 mV; Zener shunt v_across (decision D9)",
        "vsense_max": f"{VSENSE_MAX * 1e3:.1f} mV: I_nt {I_NT:.2f} A x R_hi {r_hi(TE_NT) * 1e3:.3f} mohm (self-heated, BOL), rounded up to 0.1 mV; ESTIMATE (element temperature)",
        "v_rdamp_dc": f"{V_RDAMP_DC:.1e} V: C1 blocks DC; leakage {I_LEAK_C1 * 1e9:.2f} nA ({V_C1_MAX:.2f} V gate to GND / IR 10 Gohm) x {R2G:g} ohm. IR is the series reference part's 125 degC row (`ir_125c_c0g`, more than 10000 Mohm, {gcm.raw['param']['ir_125c_c0g']['cite']}), ASSUMED for the 6.8 nF part: not bounded for it in the library","r_top_ohm": fmt_ohm(R_DIV[0]), "r_mid_ohm": fmt_ohm(R_DIV[1]), "r_bot_ohm": fmt_ohm(R_DIV[2]),
                 "trip_window_a": f"{TRIP_MIN:.2f} to {TRIP_MAX:.2f} A", "tsc_us": f"{TSC_EQ8[0] * 1e6:.0f} to {TSC_EQ8[1] * 1e6:.0f} us",
                 "uvlo_rise_v": f"{W['uvr'][0]:.2f} to {W['uvr'][1]:.2f} V", "uvlo_fall_v": f"{W['uvf'][0]:.2f} to {W['uvf'][1]:.2f} V",
                 "ov_rise_v": f"{W['ovr'][0]:.2f} to {W['ovr'][1]:.2f} V", "ov_fall_v": f"{W['ovf'][0]:.2f} to {W['ovf'][1]:.2f} V",
                 "inrush_a": f"{INRUSH['slow']:.2f} to {INRUSH['fast']:.2f} A",
                 "vscp_mv": f"{VSCP.min * 1e3:g} / {VSCP.typ * 1e3:g} / {VSCP.max * 1e3:g} mV (`{RISCP_ROWS[RISCP]}`, R(ISCP) {RISCP / 1e3:g} kohm)",
                 "tdrv_en_min_ms": f"{T_DRV_MIN * 1e3:.1f} ms (CBST low corner, V(BST_UVLOR) min, I(BST) max)",
                 "tdrv_en_max_ms": f"{T_DRV_MAX * 1e3:.1f} ms (CBST high corner, V(BST_UVLOR) max, I(BST) min)",
                 "c1_dump_energy_uj": f"{C1_DUMP_E * 1e6:.2f} uJ: 0.5 x C1 max x v_c1_max_v^2 (not a DS equation)",
                 "c1_dump_tau_ns": f"{C1_DUMP_TAU * 1e9:.0f} ns: R2 max x C1 max (not a DS equation)",
                 "c1_dump_peak_w": f"{C1_DUMP_PW:.0f} W: v_c1_max_v^2 / R2 min, single pulse (R2 pulse rating NOT BOUNDED)",
                 "cbst_dc_bias_allow_frac": f"{CBST_ALLOW * 100:.1f} % DC-bias and aging loss the 470 nF CBST still tolerates at its low corner (not a DS equation; the loss itself is NOT BOUNDED)"})
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    (FIG_DIR / "values.json").write_text(json.dumps(
        {"instance": INSTANCE, "params_file": PARAMS_REL, **VALUES,
         "summary": [{"key": r[0].strip("`"), "label": r[1], "result": r[2], "limit": r[3], "margin": r[4], "status": r[5]} for r in SUMMARY],
         "text": TEXT}, indent=2) + "\n")
    write_library_inputs(FIG_DIR)  # rows read, for the library's tools/review.py --block

    print(f"Design: {R0 * 1e3:g} mohm code {CODE} (`{MPN_SHUNT}`), {I_DES:g} A, divider {' / '.join(fmt_ohm(r) for r in R_DIV)} "
          f"({'matches the selection' if DIV_MATCH else 'DIFFERS from the selection ' + str(SEL_DIV)})")
    print(f"UVLO rising {W['uvr'][0]:.3f}-{W['uvr'][1]:.3f} V, OV rising {W['ovr'][0]:.3f}-{W['ovr'][1]:.3f} V, trip window {TRIP_MIN:.3f}-{TRIP_MAX:.3f} A")
    print(f"Soft short: shunt {P_NT_JUDGED:.3f} W, FET Tj {TJ_NT:.1f} degC; largest passing turn-on bus {V_PASS_ALL} V")
    print(f"Wrote {MD_PATH}, {FIG_DIR / 'values.json'} and charts in {FIG_DIR}")

    return {"values": VALUES, "text": TEXT, "summary": SUMMARY}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TPS48000HighSideSwitch worst-case analysis for one instance.")
    parser.add_argument("--params", help="instance params JSON (default: benches/TPS48000HighSideSwitch/params.json)")
    parser.add_argument("--out", help="output folder for design-analysis.md and design-analysis/ "
                                      "(default: benches/TPS48000HighSideSwitch/docs)")
    parser.add_argument("--instance", default="TPS", help="instance name written to the report and values.json (default: TPS)")
    ARGS, _ = parser.parse_known_args()
    analyze(ARGS.params, ARGS.out, ARGS.instance)
