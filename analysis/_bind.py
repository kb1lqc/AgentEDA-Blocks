"""Bind a block's analysis to its design (docs/spec/blocks.md "Binding"): netlist export, topology preconditions,
design values, design outputs, and the generated account in the block's spec.md. Block-independent: everything a
block needs is in blocks/<Block>/topology.toml and three calls from its analysis.py:

    from analysis import _bind
    B = _bind.load_inputs(REPO, "<Block>", PARAMS_PATH, PARAM_KEYS)   # B.params: params.json + design values from the .zen
    ...                                                               # the analysis, reading B.params
    md += _bind.preconditions_md(B, "14", mdtable)                    # report section: preconditions, design values
    _bind.write_bound(B, FIG_DIR, MD_PATH, OUTPUTS, SUMMARY_ROWS, ACCOUNT_ROWS)   # design-outputs.json, account.json

tools/bind.py checks the result; `tools/bind.py --init <Block>` writes the bench export and a draft topology.toml.
Netlist source: a Zener TestBench (benches/<B>/bind.zen, tools/bind/extract.zen) evaluates the bench and prints nets,
components (MPN, DNP, pins), net voltages and the instance configs as SI floats. pcb-native: no KiCad, no unit parsing.
"""

import ast
import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

MARK = "BIND-JSON "
FIELDS = ("min", "max", "nominal", "tolerance")
SPEC_BEGIN = "<!-- BEGIN GENERATED ACCOUNT: python3 tools/bind.py --write-spec; do not edit by hand -->"
SPEC_END = "<!-- END GENERATED ACCOUNT -->"
K0 = 273.15  # Zener stores temperatures in kelvin
REL = 1e-9  # relative match of a design output and its .zen config (outputs are written with 9 decimals of mantissa)
PREFIX = (("G", 1e9), ("M", 1e6), ("k", 1e3), ("", 1.0), ("m", 1e-3), ("u", 1e-6), ("n", 1e-9), ("p", 1e-12))


class BindError(Exception):
    """A design value or a netlist fact the binding needs is missing or ambiguous."""


# ---------------------------------------------------------------------------------------------------------- extract
def bind_zen(root, bench):
    return Path(root) / "benches" / bench / "bind.zen"


def extract(root, bench, pcb="pcb"):
    """The bench netlist export: run `pcb test benches/<bench>/bind.zen` and parse its one BIND-JSON line."""
    root = Path(root)
    zen = bind_zen(root, bench)
    if not zen.is_file():
        raise BindError(f"{zen.relative_to(root)} missing (generate it: python3 tools/bind.py --init {bench})")
    if shutil.which(pcb) is None:
        raise BindError(f"`{pcb}` not found: the design values are read from the .zen through pcb (install pcb)")
    p = subprocess.run([pcb, "test", str(zen.relative_to(root))], cwd=root, capture_output=True, text=True)
    lines = [ln[len(MARK):] for ln in (p.stdout + p.stderr).splitlines() if ln.startswith(MARK)]
    if p.returncode or len(lines) != 1:
        tail = "\n".join((p.stdout + p.stderr).strip().splitlines()[-8:])
        raise BindError(f"pcb test {zen.relative_to(root)} exit {p.returncode}, {len(lines)} BIND-JSON lines:\n{tail}")
    return json.loads(lines[0])


def topology(root, block):
    path = Path(root) / "blocks" / block / "topology.toml"
    return tomllib.loads(path.read_text()) if path.is_file() else None


def instance_of(ext):
    """The single instance the bench export carries configs for."""
    names = sorted(ext.get("configs", {}))
    if len(names) != 1:
        raise BindError(f"expected one instance in the bench export configs, found {names}")
    return names[0]


# ---------------------------------------------------------------------------------------------------------- bind.zen
CONFIG_RE = re.compile(r"^(\w+)\s*=\s*config\(\s*(\w+)", re.M)
TYPED = {"Resistance", "Voltage", "Current", "Capacitance", "Inductance", "Temperature", "Frequency", "Power", "Time"}


def bench_facts(root, block):
    """(instance name, bench config names) of benches/<block>/bench.zen; BindError when they cannot be read."""
    text = (Path(root) / "benches" / block / "bench.zen").read_text()
    m = re.search(rf'^(\w+)\s*=\s*Module\(\s*"[^"]*/blocks/{re.escape(block)}/{re.escape(block)}\.zen"\s*\)', text, re.M)
    if not m:
        raise BindError(f"benches/{block}/bench.zen: no `X = Module(\".../blocks/{block}/{block}.zen\")`")
    inst = re.search(rf'\b{m.group(1)}\(\s*name\s*=\s*"([^"]+)"', text)
    if not inst:
        raise BindError(f"benches/{block}/bench.zen: no `{m.group(1)}(name=\"...\")` instance")
    return inst.group(1), [n for n, _ in CONFIG_RE.findall(text)]


def render_bind_zen(root, block):
    """Text of benches/<block>/bind.zen: the TestBench export with every REFERENCE config passed in its declared type
    (from the block's config() declarations), and every bench config. Deterministic; tools/bind.py checks it."""
    root = Path(root)
    decl = dict(CONFIG_RE.findall((root / "blocks" / block / f"{block}.zen").read_text()))
    ref = re.findall(r'^\s*"(\w+)"\s*:', (root / "blocks" / block / "reference.zen").read_text(), re.M)
    inst, bench_cfg = bench_facts(root, block)
    units = sorted({decl[k] for k in ref if decl.get(k) in TYPED})
    lines = ['"""', "Netlist and design-value export of this bench (not a board). GENERATED: do not edit; regenerate with",
             f"`python3 tools/bind.py --init {block}`. `pcb test` prints one BIND-JSON line (tools/bind/extract.zen)",
             "for the block analysis and tools/bind.py.", '"""', ""]
    if units:
        lines.append(f'load("@stdlib/units.zen", {", ".join(repr(u).replace(chr(39), chr(34)) for u in units)})')
    lines += ['load("../../tools/bind/extract.zen", "extract")',
              f'load("github.com/kb1lqc/circuit-blocks/blocks/{block}/reference.zen", "REFERENCE")']
    if bench_cfg:
        lines.append(f'load("./bench.zen", {", ".join(chr(34) + n + chr(34) for n in bench_cfg)})')
    lines += ["", 'Bench = Module("./bench.zen")', "", "TestBench(", '    name="bind",', "    module=Bench,",
              '    test_cases={"default": {}},', "    checks=[", "        extract(", "            configs={",
              f'                "{inst}": {{']
    for k in ref:
        t = decl.get(k)
        val = f'{t}(REFERENCE["{k}"])' if t in TYPED else f'REFERENCE["{k}"]'
        lines.append(f'                    "{k}": {val},')
    lines += ["                },", "            },",
              "            bench={" + ", ".join(f'"{n}": {n}' for n in bench_cfg) + "},", "        ),", "    ],", ")", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------------------- netlist
def role_path(ext, inst, role):
    """Full component path of a role (instance-relative module name), e.g. INA.SHUNT.WSK2512; BindError unless one."""
    found = [p for p in ext["components"] if p.startswith(f"{inst}.{role}.")]
    if len(found) != 1:
        raise BindError(f"role {role}: {len(found)} components under {inst}.{role}. (need exactly 1)")
    return found[0]


def pin_net(ext, inst, pin):
    """Net name of '<role>.<pin>'."""
    role, _, name = pin.partition(".")
    comp = ext["components"][role_path(ext, inst, role)]
    if name not in comp["pins"]:
        raise BindError(f"{pin}: no pin {name} on {comp['component']} (pins {sorted(comp['pins'])})")
    return comp["pins"][name]


def _member(ext, inst, pin):
    role, _, name = pin.partition(".")
    return (role_path(ext, inst, role), name)


def _short(ext, inst, members):
    """Readable members of a net: role.pin inside the instance, the full path outside it."""
    out = []
    for path, pin in members:
        parts = path.split(".")
        out.append(f"{parts[1]}.{pin}" if parts[0] == inst and len(parts) > 2 else f"{path}.{pin}")
    return "{" + ", ".join(sorted(out)) + "}"


def _groups(spec):
    return [spec] if spec and isinstance(spec[0], str) else list(spec)


def voltage(ext, net):
    v = ext.get("voltages", {}).get(net)
    if v is None:
        raise BindError(f"net {net} declares no voltage")
    return v


# ---------------------------------------------------------------------------------------------------------- library
def library_part(component):
    """zener-library Part for a wrapper name (WSK2512, ERJ-3EKF, GCM188), at the checkout analysis/_zl.py pins."""
    from analysis import _zl  # noqa: F401  (side effect: zener-library on sys.path)
    from analysis._export import component_dir
    from lib import load
    d = component_dir(_zl.ZL, component)
    if d is None:
        raise BindError(f"library component {component}: no single components/*/{component}/")
    return load(f"{d.parent.name}/{d.name}")


def mpn_value(part, mpn):
    """(value in ohm, tolerance code) of a resistor-series MPN, by inverting part.mpn; BindError unless exactly one."""
    hits = [(v, c) for v, c in part.pairs() if part.mpn(v, c) == mpn]
    if len(hits) != 1:
        raise BindError(f"{mpn}: {len(hits)} series values of {part.info.get('value')} give this MPN (need 1)")
    return hits[0]


def catalog_row(part, mpn):
    """The capacitor catalog row of an MPN (catalog values carry no package letter); BindError unless exactly one."""
    hits = [r for r in part.catalog() if mpn.startswith(r.value) and len(mpn) - len(r.value) <= 1]
    if len(hits) != 1:
        raise BindError(f"{mpn}: {len(hits)} catalog rows of {part.info.get('value')} match (need 1)")
    return hits[0]


def part_field(component, mpn, field):
    """A field of the placed part, decoded from its netlist MPN through the library: value (ohm or F) for a resistor
    series or a capacitor catalog; rated_v (V), dielectric and tolerance (catalog text in the wrapper's ASCII form,
    '+/-10%') for a capacitor."""
    part = library_part(component)
    series = part.info.get("series")
    if series == "resistor" and field == "value":
        return mpn_value(part, mpn)[0]
    if series == "capacitor":
        row = catalog_row(part, mpn)
        from lib.capacitor import ascii_text  # the library's catalog-text-to-wrapper form ('±10%' -> '+/-10%')
        got = {"value": row.c, "rated_v": row.v, "dielectric": ascii_text(row.dielectric),
               "tolerance": ascii_text(row.tolerance)}
        if field in got:
            return got[field]
    raise BindError(f"{component} ({series or 'not a series part'}): field '{field}' cannot be decoded from an MPN")


# ---------------------------------------------------------------------------------------------------------- checks
def _pins_of(pre):
    pins = []
    for k in ("exact", "contains"):
        if k in pre:
            for g in _groups(pre[k]):
                pins += g
    for k in ("separate", "same_voltage"):
        pins += pre.get(k, [])
    return pins


def check_parts(topo, ext, inst):
    """[(id, what, detail, sections)] for the [parts] table."""
    parts = dict(topo.get("parts", {}))
    exclusive = parts.pop("exclusive", False)
    fails = []
    sec_by_role = {}
    for pre in topo.get("pre", []):
        for role in {p.partition(".")[0] for p in _pins_of(pre)} | ({pre["mpn"]} if "mpn" in pre else set()):
            sec_by_role.setdefault(role, set()).update(pre.get("sections", []))
    for role, wrapper in parts.items():
        secs = sorted(sec_by_role.get(role, {"Design definition"}), key=section_key)
        try:
            c = ext["components"][role_path(ext, inst, role)]
        except BindError as e:
            fails.append((f"PART-{role}", f"{role} is one {wrapper}", str(e), secs))
            continue
        if c["component"] != wrapper:
            fails.append((f"PART-{role}", f"{role} is one {wrapper}", f"it is {c['component']} ({c['mpn']})", secs))
        elif c["dnp"]:
            fails.append((f"PART-{role}", f"{role} is fitted", f"{role} is DNP", secs))
    if exclusive:
        extra = sorted(p for p in ext["components"] if p.startswith(f"{inst}.") and p.split(".")[1] not in parts)
        for p in extra:
            nets = sorted(set(ext["components"][p]["pins"].values()))
            fails.append(("PARTS-ONLY", "no part in the instance beyond [parts]",
                          f"unexpected {ext['components'][p]['component']} {p} on nets {', '.join(nets)}",
                          ["Design definition"]))
    return fails


def check_pre(pre, ext, inst):
    """Failure detail of one precondition, or None when it holds."""
    try:
        for kind in ("exact", "contains"):
            for group in _groups(pre.get(kind, [])):
                nets = {p: pin_net(ext, inst, p) for p in group}
                if len(set(nets.values())) != 1:
                    return "not on one net: " + ", ".join(f"{p} on {n}" for p, n in nets.items())
                net = nets[group[0]]
                have = {tuple(m) for m in ext["nets"][net]}
                want = {_member(ext, inst, p) for p in group}
                if kind == "exact" and have != want:
                    return f"{net} is {_short(ext, inst, have)}, required exactly {_short(ext, inst, want)}"
        if "separate" in pre:
            nets = {p: pin_net(ext, inst, p) for p in pre["separate"]}
            if len(set(nets.values())) != len(nets):
                return "pins share a net: " + ", ".join(f"{p} on {n}" for p, n in nets.items())
        if "same_voltage" in pre:
            vs = {p: voltage(ext, pin_net(ext, inst, p)) for p in pre["same_voltage"]}
            if len({(v["min"], v["max"]) for v in vs.values()}) != 1:
                return "different ranges: " + ", ".join(f"{p} {v['text']}" for p, v in vs.items())
        if "mpn" in pre:
            role = pre["mpn"]
            comp = ext["components"][role_path(ext, inst, role)]
            cfg = ext["configs"][inst]
            value, code = cfg[pre["value"]]["value"], cfg[pre["tolerance"]]["value"]
            want = library_part(comp["component"]).mpn(value, code)
            if comp["mpn"] != want:
                return (f"{role} MPN {comp['mpn']} but {pre['value']}={cfg[pre['value']]['text']}, "
                        f"{pre['tolerance']}={code} give {want}")
    except (BindError, ValueError, KeyError) as e:
        return f"cannot evaluate: {e}"
    return None


def check_topology(topo, ext, inst):
    """[(id, what, detail, sections)] for every broken part rule or precondition."""
    fails = check_parts(topo, ext, inst)
    for pre in topo.get("pre", []):
        detail = check_pre(pre, ext, inst)
        if detail:
            fails.append((pre["id"], pre["what"], detail, pre.get("sections", [])))
    return fails


def section_key(s):
    m = re.match(r"(\d+)([a-z]?)$", s)
    return (0, int(m.group(1)), m.group(2)) if m else (-1, 0, s)


def report_sections(md_text):
    """Section ids of a design-analysis.md: '5b' for '## 5b. ...', the heading text for an unnumbered '## ...'."""
    out = set()
    for m in re.finditer(r"^## (.+)$", md_text, re.M):
        h = m.group(1).strip()
        n = re.match(r"(\d+[a-z]?)\.\s", h)
        out.add(n.group(1) if n else h)
    return out


def sections_by_pre(topo):
    """{section: [precondition ids]} (the report lists, per section, the preconditions it rests on)."""
    out = {}
    for pre in topo.get("pre", []):
        for s in pre.get("sections", []):
            out.setdefault(s, []).append(pre["id"])
    return dict(sorted(out.items(), key=lambda kv: section_key(kv[0])))


# ---------------------------------------------------------------------------------------------------------- values
def design_values(topo, ext, inst):
    """({params key: value}, {params key: source text}) for the topology [design] table, from the bench export.
    Kinds: config = an instance config (field for a range); part = a field of a role's placed part decoded from its MPN
    (part_field); net = a voltage field of the net holding a pin; bench = a bench config. Kelvin becomes degC."""
    values, sources = {}, {}
    for key, spec in topo.get("design", {}).items():
        field = spec.get("field", "value")
        if "config" in spec:
            q = ext["configs"][inst].get(spec["config"])
            if q is None:
                raise BindError(f"{key}: instance {inst} has no config {spec['config']} in the bench export "
                                "(set it in reference.zen, or read it with part = <role>)")
            src = f"config `{spec['config']}` = `{q['text']}`"
        elif "part" in spec:
            comp = ext["components"][role_path(ext, inst, spec["part"])]
            values[key] = part_field(comp["component"], comp["mpn"], field)
            sources[key] = f"{spec['part']} MPN `{comp['mpn']}` {field} (library)"
            continue
        elif "net" in spec:
            net = pin_net(ext, inst, spec["net"])
            q = voltage(ext, net)
            src = f"net `{net}` (`{q['text']}`)"
        elif "bench" in spec:
            q = ext["bench"].get(spec["bench"])
            if q is None:
                raise BindError(f"{key}: bench config {spec['bench']} not in the bench export")
            src = f"bench config `{spec['bench']}` = `{q['text']}`"
        else:
            raise BindError(f"{key}: unknown [design] kind {sorted(spec)}")
        x = q.get(field)
        if x is None:
            raise BindError(f"{key}: {src} has no {field}")
        if q.get("unit") == "K" and field in ("value", "min", "max", "nominal"):
            x = round(x - K0, 9)  # kelvin float back to degC (the .zen text is in degC); 1 nK rounding
        values[key], sources[key] = x, src + (f" {field}" if "field" in spec else "")
    return values, sources


def check_params(topo, params_raw, params_rel):
    """Design keys repeated in params.json (a value must have one source)."""
    keys = {k for k in params_raw if not k.endswith("_basis")}
    return [(k, f"{params_rel}: `{k}` is a design value read from the .zen (topology.toml [design]); delete it and "
                f"its _basis from the params file") for k in sorted(keys & set(topo.get("design", {})))]


def zen_number(x, unit):
    """Zener text for an SI value: the largest prefix that keeps the mantissa at or above 1, mantissa rounded to 9
    decimals (27.3mV, 536kohm, 100nV, -454.45225mV)."""
    if x == 0:
        return f"0{unit}"
    for p, s in PREFIX:
        if abs(x) / s >= 1 or s == PREFIX[-1][1]:
            m = round(x / s, 9)
            t = repr(m)
            return (t[:-2] if t.endswith(".0") else t) + p + unit
    raise AssertionError


def output_text(out):
    """Zener text of a design output: a value, or a 'min to max' range."""
    u = out.get("unit", "")
    if "min" in out:
        return f"{zen_number(out['min'], u)} to {zen_number(out['max'], u)}"
    return zen_number(out["value"], u)


def _close(a, b):
    return a is not None and b is not None and abs(a - b) <= REL * max(abs(a), abs(b), 1e-30)


def output_matches(out, have):
    """True when a .zen config (bench export quantity) equals a design output (value, or min and max)."""
    if have is None:
        return False
    if "min" in out:
        return _close(have.get("min"), out["min"]) and _close(have.get("max"), out["max"])
    return have.get("min") == have.get("max") and _close(have.get("value"), out["value"])


def output_file(block, spec):
    return spec.get("file", f"blocks/{block}/reference.zen")


def check_outputs(topo, ext, inst, outputs, outputs_rel, block):
    """[(config, file, message)] where a design output differs from the .zen config."""
    fails = []
    for cfg, spec in topo.get("outputs", {}).items():
        out, f = outputs.get(cfg), output_file(block, spec)
        if out is None:
            fails.append((cfg, outputs_rel, f"{outputs_rel} has no `{cfg}` (run the analysis)"))
            continue
        have = ext["configs"][inst].get(cfg)
        if not output_matches(out, have):
            fails.append((cfg, f, f"{f}: config `{cfg}` is {have['text'] if have else 'unset'}, the analysis requires "
                                  f"{output_text(out)} ({out['basis']}); apply with `python3 tools/bind.py --apply {block}`"))
    return fails


def apply_outputs(root, block, topo, outputs, ext, inst):
    """Write each design output that differs from its .zen config into the config's dict entry ("key": "...").
    Returns [(file, key, old, new)]."""
    changes = []
    for cfg, spec in topo.get("outputs", {}).items():
        out = outputs[cfg]
        if output_matches(out, ext["configs"][inst].get(cfg)):
            continue
        rel = output_file(block, spec)
        path = Path(root) / rel
        key = spec.get("key", cfg)
        text = path.read_text()
        pat = re.compile(rf'("{re.escape(key)}"\s*:\s*")([^"]*)(")')
        hits = pat.findall(text)
        if len(hits) != 1:
            raise BindError(f"{rel}: {len(hits)} entries \"{key}\": \"...\" (need exactly 1)")
        new = output_text(out)
        path.write_text(pat.sub(lambda m: m.group(1) + new + m.group(3), text))
        changes.append((rel, key, hits[0][1], new))
    return changes


# ---------------------------------------------------------------------------------------------------------- analysis
class Bound:
    """What load_inputs gives an analysis: params (params.json merged with the design values), and the binding facts
    preconditions_md and write_bound need."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def load_inputs(root, block, params_path, param_keys, optional=(), bench=None, instance=None):
    """Read params.json (analysis-only inputs; every key required, design keys refused) and the design values from the
    bench netlist export. SystemExit with the reason on any problem: a value never falls back to a default."""
    root = Path(root)
    topo = topology(root, block)
    if topo is None:
        raise SystemExit(f"blocks/{block}/topology.toml missing")
    params_path = Path(params_path)
    try:
        params_rel = str(params_path.resolve().relative_to(root))
    except ValueError:
        params_rel = str(params_path)
    raw = json.loads(params_path.read_text())
    dup = check_params(topo, raw, params_rel)
    if dup:
        raise SystemExit("\n".join(msg for _, msg in dup))
    keys = {k for k in raw if not k.endswith("_basis")}
    need = set(param_keys)
    missing, extra = (need - set(optional)) - keys, keys - need
    if missing or extra:
        raise SystemExit(f"{params_path}: missing keys {sorted(missing)}, unknown keys {sorted(extra)}")
    bench = bench or block
    try:
        ext = extract(root, bench)
        inst = instance or instance_of(ext)
        design, sources = design_values(topo, ext, inst)
    except BindError as e:
        raise SystemExit(f"design values from benches/{bench}/bind.zen: {e}")
    return Bound(root=root, block=block, bench=bench, instance=inst, topo=topo, ext=ext, design=design,
                 sources=sources, params={**raw, **design}, params_rel=params_rel,
                 topology_rel=f"blocks/{block}/topology.toml")


def preconditions_md(B, number, mdtable):
    """Report section: the preconditions, the sections resting on each, and the design values with their source."""
    md = [f"## {number}. Topology preconditions and design values", "",
          f"The circuit this analysis assumes is declared in `{B.topology_rel}` and checked against the bench netlist "
          f"(`benches/{B.bench}/bind.zen`, pcb test) by `tools/bind.py` on every verify run. When a precondition breaks, "
          "the sections listed beside it are no longer valid.", ""]
    md += mdtable(["Precondition", "Requires"], [[f"`{p['id']}`", p["what"]] for p in B.topo.get("pre", [])])
    md += [""] + mdtable(["Section", "Rests on preconditions"],
                         [[s, ", ".join(f"`{i}`" for i in ids)] for s, ids in sections_by_pre(B.topo).items()])
    md += ["", f"Design values read from the .zen (not the params file), instance `{B.instance}`:", ""]
    md += mdtable(["Analysis input", "Value", "Source"],
                  [[f"`{k}`", f"{B.design[k]!r}", B.sources[k]] for k in B.topo.get("design", {})])
    return md + [""]


def write_bound(B, fig_dir, md_path, outputs, summary, account_rows):
    """Write design-outputs.json and account.json.
    outputs: {config: {value | min and max, unit, basis}}, keys exactly topology.toml [outputs].
    summary: the report's summary rows as dicts {requirement, result, limit, margin, status} and optional key.
    account_rows: [(match, section, rests_on, open_items)]: match is a summary key, or the start of a requirement text;
    each must match exactly one summary row. Rows not named are counted as report-only."""
    from analysis._export import review_counts
    from analysis._zl import ZL_VERSION
    if set(outputs) != set(B.topo.get("outputs", {})):
        raise SystemExit(f"design outputs {sorted(outputs)} differ from topology.toml [outputs] "
                         f"{sorted(B.topo.get('outputs', {}))}")
    req = []
    for match, sec, rests, ois in account_rows:
        rows = [r for r in summary if r.get("key") == match] or [r for r in summary if r["requirement"].startswith(match)]
        if len(rows) != 1:
            raise SystemExit(f"account row '{match}' matches {len(rows)} summary rows (need 1)")
        r = rows[0]
        req.append({k: r[k] for k in ("requirement", "result", "limit", "margin", "status")} |
                   {"section": sec, "rests_on": list(rests), "open_items": list(ois)})
    n, human = review_counts()
    try:
        report = str(Path(md_path).resolve().relative_to(B.root))
    except ValueError:
        report = str(md_path)
    account = {
        "block": B.block, "instance": B.instance, "report": report, "library": ZL_VERSION, "topology": B.topology_rel,
        "inputs": {"rows": n, "human": human}, "requirements": req, "report_only": len(summary) - len(req),
        "design": [{"key": k, "value": repr(B.design[k]), "source": B.sources[k]} for k in B.topo.get("design", {})],
        "outputs": {k: dict(v, text=output_text(v)) for k, v in outputs.items()},
        "preconditions": [p["id"] for p in B.topo.get("pre", [])],
    }
    fig_dir = Path(fig_dir)
    fig_dir.mkdir(parents=True, exist_ok=True)
    (fig_dir / "design-outputs.json").write_text(json.dumps(account["outputs"], indent=2) + "\n")
    (fig_dir / "account.json").write_text(json.dumps(account, indent=2, ensure_ascii=False) + "\n")


def bench_inputs(bench_dir):
    """The analysis inputs a bench script (notes, sheet checks) needs: params.json plus the design values the analysis
    read from the .zen, as recorded in docs/design-analysis/account.json (tools/bind.py fails if they differ)."""
    bench_dir = Path(bench_dir)
    p = json.loads((bench_dir / "params.json").read_text())
    acc = bench_dir / "docs" / "design-analysis" / "account.json"
    if acc.is_file():
        p.update({d["key"]: ast.literal_eval(d["value"]) for d in json.loads(acc.read_text())["design"]})
    return p


# ---------------------------------------------------------------------------------------------------------- draft
def draft_topology(root, block, ext, inst):
    """A draft topology.toml from the current netlist: every part, every internal net as `exact`, the instance's pins on
    each port net as `contains`, every REFERENCE config as a design value. Sections are TODO (tools/bind.py fails until
    each precondition names the report sections it invalidates)."""
    zen = (Path(root) / "blocks" / block / f"{block}.zen").read_text()
    internal = set(re.findall(r"^(\w+)\s*=\s*Net\(", zen, re.M))
    roles = {}
    for p, c in sorted(ext["components"].items()):
        if p.startswith(f"{inst}."):
            roles[p.split(".")[1]] = c["component"]
    out = [f"# Draft from the {block} bench netlist (python3 tools/bind.py --init {block}). Edit it: keep the preconditions",
           "# the analysis relies on, give each a `what` and the report sections it invalidates, add mpn / separate /",
           "# same_voltage where they apply, map the design values to the params keys the analysis reads, list outputs.",
           "# Kinds and fields: docs/spec/blocks.md \"Binding\".", "", "[parts]", "exclusive = true"]
    out += [f'{r} = "{w}"' for r, w in roles.items()]
    for net, members in sorted(ext["nets"].items()):
        mine = sorted(f"{m[0].split('.')[1]}.{m[1]}" for m in members if m[0].startswith(f"{inst}."))
        if not mine:
            continue
        kind = "exact" if net in internal and len(mine) == len(members) else "contains"
        if kind == "contains" and len(mine) < 2:
            continue
        out += ["", "[[pre]]", f'id = "{net.replace("_", "-")}"', f'what = "TODO: {net}"',
                f"{kind} = [{', '.join(chr(34) + x + chr(34) for x in mine)}]", 'sections = ["TODO"]']
    out += ["", "[design]"]
    for k, q in ext["configs"][inst].items():
        out.append(f'# {k} = {{ config = "{k}" }}  # `{q["text"]}`: rename the key to the params key the analysis reads')
    out += ["", "[outputs]", "# <config> = {}  # an analysis result that sets an instance config (blocks/<Block>/reference.zen)", ""]
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------------------- account
def _cell(s):
    return str(s).replace("|", "/").replace("\n", " ")


def render_account(acc, open_ids):
    """Markdown of the generated account (between SPEC_BEGIN and SPEC_END) from design-analysis/account.json."""
    inp = acc["inputs"]
    md = [SPEC_BEGIN, "", "## Results account (generated)", "",
          f"Bench reference instance `{acc['instance']}`, from `{acc['report']}` (zener-library {acc['library']}). "
          f"Library rows read by the analysis: {inp['rows']}; checked by a person (current human stamp): "
          f"**{inp['human']} of {inp['rows']}**; the rest carry a machine `verified` stamp or none. The library does not "
          "attribute rows to individual results, so the count is per analysis, not per row below.", "",
          "| Requirement | Result (worst case) | Limit | Margin | Status | Rests on (assumption / estimate) | Open items | Section |",
          "|---|---|---|---|---|---|---|---|"]
    for r in acc["requirements"]:
        md.append("| " + " | ".join(_cell(x) for x in (
            r["requirement"], r["result"], r["limit"], r["margin"], r["status"],
            "; ".join(r["rests_on"]) or "none", ", ".join(r["open_items"]) or "none", r["section"])) + " |")
    md += ["", f"{acc['report_only']} further results are REPORT ONLY, REFERENCE or CHECK (no limit set): see the report summary.",
           "", "**Design values (read from the .zen through the bench netlist export)**", "",
           "| Analysis input | Value | Source |", "|---|---|---|"]
    md += [f"| `{d['key']}` | {_cell(d['value'])} | {_cell(d['source'])} |" for d in acc["design"]]
    md += ["", "**Design outputs (analysis result -> .zen config; tools/bind.py fails when they differ)**", "",
           "| Config | Value | Basis |", "|---|---|---|"]
    md += [f"| `{k}` | {_cell(v['text'])} | {_cell(v['basis'])} |" for k, v in acc["outputs"].items()]
    md += ["", f"Topology preconditions: {len(acc['preconditions'])} (`{acc['topology']}`), checked against the bench "
           "netlist by tools/bind.py on every verify run.", "", SPEC_END]
    return "\n".join(md)


def spec_open_ids(spec_text):
    """Open-item ids declared in a spec.md as `OI-...` inside the Open items section."""
    m = re.search(r"^## Open items\s*$(.*?)(?=^## |\Z)", spec_text, re.M | re.S)
    return set(re.findall(r"`(OI-[A-Z0-9-]+)`", m.group(1))) if m else set()


def spec_block(spec_text):
    """(start, end) offsets of the generated block in spec.md (end after SPEC_END), or None."""
    a, b = spec_text.find(SPEC_BEGIN), spec_text.find(SPEC_END)
    if a < 0 or b < a or spec_text.count(SPEC_BEGIN) != 1 or spec_text.count(SPEC_END) != 1:
        return None
    return a, b + len(SPEC_END)
