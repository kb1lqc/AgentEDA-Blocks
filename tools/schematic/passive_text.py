"""Passive component text stacks (Fox-1-MPPT style), shared by every bench's arrange_sheet.py.

Every resistor and capacitor shows, one field per line left-aligned beside the symbol: the reference (the symbol's
own field), then value / package / [rating + dielectric] / tolerance (a text item). Resistors: `4mΩ`, `2512`, `0.5%`;
capacitors in their natural unit: `100nF`, `0603`, `50V X7R`, `10%`. The MPN Value field is hidden.

Every field is resolved from the library catalog for the part's MPN (component.toml, catalog.csv, the wrapper's _MPN
table), never typed; the generator stops (SystemExit) when a field cannot be resolved or when the decoded value
differs from the analysis input it is given. `check_stacks` recomputes the stacks and compares them with a sheet
(no writes); benches expose it as `arrange_sheet.py --check`, which tools/schematic/check_sync.sh runs.

Library checkout: $ZENER_LIBRARY, else ../zener-library (next to this repo; the v0.4.0 release checkout that
analysis/_zl.py loads).
Helpers (place.py, kicad_sexpr.py): $ZL_TOOLS, default ../zener-library/tools/schematic.
"""
import csv
import os
import re
import sys
import tomllib
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ZL = os.environ.get("ZL_TOOLS", str(REPO.parent / "zener-library" / "tools" / "schematic"))
if ZL not in sys.path:
    sys.path.insert(0, ZL)
import kicad_sexpr as kx  # noqa: E402
import place  # noqa: E402

PITCH = 2.03  # line pitch of 1.27 mm text (1.6 x size)
TOPOFF = 0.65  # free text "left top" anchor sits this far above the centre of its first line


def zl_root():
    for c in (os.environ.get("ZENER_LIBRARY"), REPO.parent / "zener-library"):
        if c and (Path(c) / "components").is_dir():
            return Path(c)
    raise SystemExit("zener-library checkout not found (set ZENER_LIBRARY)")


def fmt_r(ohm):
    if ohm < 1:
        return "%gmΩ" % round(ohm * 1e3, 6)
    if ohm < 1e3:
        return "%gΩ" % round(ohm, 6)
    if ohm < 1e6:
        return "%gkΩ" % round(ohm / 1e3, 6)
    return "%gMΩ" % round(ohm / 1e6, 6)


def fmt_c(f):
    if f < 1e-9 * (1 - 1e-9):
        return "%gpF" % round(f * 1e12, 6)
    if f < 1e-6 * (1 - 1e-9):
        return "%gnF" % round(f * 1e9, 6)
    return "%guF" % round(f * 1e6, 6)


def _pkg(txt):
    m = re.search(r"\((\d{4})\)", txt)
    return m.group(1) if m else txt.split()[0]


_CACHE = {}


def passive_fields(wrapper, mpn):
    """(kind, [value, package, rating + dielectric ('' for a resistor), tolerance], numeric value in ohm or F) for a
    placed passive, from the library entry of `wrapper` (component.toml, catalog.csv, the generated _MPN table).
    SystemExit when any field cannot be resolved."""
    key = (wrapper, mpn)
    if key in _CACHE:
        return _CACHE[key]
    hits = list((zl_root() / "components").glob("*/%s/component.toml" % wrapper))
    if len(hits) != 1:
        raise SystemExit("passive %s: no unique library entry %s" % (mpn, wrapper))
    d = hits[0].parent
    toml = tomllib.loads(hits[0].read_text())
    part = toml["part"]
    pkg = _pkg(part["package"])
    pct = lambda p: "%g%%" % abs(toml["param"][p]["max"])
    if part.get("series") == "resistor":
        zen = (d / (wrapper + ".zen")).read_text()
        if wrapper.startswith("WSK2512"):
            m = re.search(r'\((\d+), "([A-Z])"\): "%s"' % re.escape(mpn), zen)
            if not m:
                raise SystemExit("passive %s: not in the %s _MPN table" % (mpn, wrapper))
            ohm, tol = int(m.group(1)) / 1e6, pct(part["series_tolerances"][m.group(2)]["tol"])
        else:
            m = re.search(r'(\d+): "%s"' % re.escape(mpn), zen)
            if not m:
                raise SystemExit("passive %s: not in the %s _MPN table" % (mpn, wrapper))
            ohm, tol = int(m.group(1)) / 1e6, pct(part["series_tol"])
        out = ("R", [fmt_r(ohm), pkg, tol], ohm)
    else:
        letters = set([part["package_default"]] + list(part.get("package_alternates", [])))
        rows = [r for r in csv.DictReader(open(d / "catalog.csv", encoding="utf-8"))
                if mpn.startswith(r["value"]) and mpn[len(r["value"]):] in letters]
        if len(rows) != 1:
            raise SystemExit("passive %s: %d catalog rows in %s" % (mpn, len(rows), wrapper))
        r = rows[0]
        m = re.fullmatch(r"([0-9,.]+)\s*(pF|nF|[μµu]F)", r["capacitance"].strip())
        if not m:
            raise SystemExit("passive %s: capacitance %r" % (mpn, r["capacitance"]))
        f = float(m.group(1).replace(",", "")) * {"pF": 1e-12, "nF": 1e-9}.get(m.group(2), 1e-6)
        v = re.fullmatch(r"([0-9.]+)\s*Vdc", r["rated_voltage"].strip())
        t = re.fullmatch(r"±\s*([0-9.]+)\s*%", r["tolerance"].strip())
        if not (v and t and r["dielectric"].strip()):
            raise SystemExit("passive %s: unresolved rating/tolerance in %s" % (mpn, wrapper))
        out = ("C", [fmt_c(f), pkg, "%sV %s" % (v.group(1), r["dielectric"].strip()), "%s%%" % t.group(1)], f)
    _CACHE[key] = out
    return out


def refs_by_name(sh):
    """{Zener name: reference designator} from each part's Path property."""
    out = {}
    for s in sh.symbols():
        p = s._prop("Path")
        if p is not None:
            out[p.atoms()[2].value.split(".")[1]] = s.ref
    return out


def wrapper_of(s):
    return s._prop("Path").atoms()[2].value.split(".")[2]


def stack(sh, s, x, ymid, expected, add_note):
    """Write the text stack of symbol `s` (reference over value / package / [rating] / tolerance, left-aligned at x,
    centred vertically on ymid); the MPN Value field is hidden. `expected` is the analysis input (ohm or F) the
    decoded value must equal, or None when the sheet's .zen is the only source. `add_note(text, x, y, justify)` adds
    the text item. Returns the lines."""
    kind, fl, num = passive_fields(wrapper_of(s), s.value)
    if expected is not None and abs(num - expected) > 1e-6 * expected:
        raise SystemExit("%s: library value %g does not match the analysis input %g" % (s.ref, num, expected))
    lines = [s.ref] + fl
    y0 = ymid - (len(lines) - 1) * PITCH / 2
    # a part mirrored in x or rotated 180 flips the justification of its fields: store "right" to read left-aligned; a part rotated
    # 180 degrees needs a stored angle of 180 to read upright (verified in a render)
    sh.set_field(s.ref, "Reference", round(x - s.at[0], 4), round(y0 - s.at[1], 4), 180 if s.rot == 180 else 0,
                 "right" if (s.mirror == "y") != (s.rot == 180) else "left")
    s._prop("Reference").get("effects").get("font").get("size").set_nums(place.FONT, place.FONT)
    hide = s._prop("Value").get("hide")
    if hide is None:
        pr = s._prop("Value")
        node = kx.parse_snippet("(hide yes)")
        node.ws = "\n\t\t\t"
        pr.items.insert(pr.items.index(pr.get("at")) + 1, node)
    else:
        hide.atoms()[1].raw = "yes"
    add_note("\n".join(lines[1:]), x, round(y0 + PITCH - TOPOFF, 4), "left top")
    return lines


def check_stacks(path, namespace, expected):
    """Recompute every passive text stack from the library and the sheet's MPNs and compare with the sheet (no
    writes). `expected` = {Zener name: analysis input or None}. Returns a list of problems; empty = in sync."""
    sh = place.Sheet.load(path, namespace)
    ref = refs_by_name(sh)
    want, bad = Counter(), []
    for name, exp in expected.items():
        s = sh.symbol(ref[name])
        _kind, fl, num = passive_fields(wrapper_of(s), s.value)
        if exp is not None and abs(num - exp) > 1e-6 * exp:
            bad.append("%s: library value %g differs from the analysis input %g" % (name, num, exp))
        want["\n".join(fl)] += 1
        hide = s._prop("Value").get("hide")
        if hide is None or hide.atoms()[1].raw != "yes":
            bad.append("%s: MPN Value field is not hidden" % name)
        if s._prop("Reference").get("effects").get("font").get("size").nums()[0] != place.FONT:
            bad.append("%s: Reference text is not %g mm" % (name, place.FONT))
    have = Counter(t for t in (n.args()[0] if hasattr(n, "args") else None for n in sh.root.nodes("text"))
                   if isinstance(t, str) and t in want)
    for t in want:
        if have[t] != want[t]:
            bad.append("text stack %r: %d on the sheet, %d expected" % (t, have[t], want[t]))
    return bad
