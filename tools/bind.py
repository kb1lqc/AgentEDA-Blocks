#!/usr/bin/env python3
"""Bind each block's analysis to its design (docs/spec/blocks.md "Binding"). Exit 0 = pass, 1 = any violation.

Usage (repo root):
  python3 tools/bind.py [BLOCK ...]          check every block with blocks/<Block>/topology.toml (or the named ones)
  python3 tools/bind.py --write-spec [BLOCK ...]   rewrite the generated account in blocks/<Block>/spec.md
  python3 tools/bind.py --apply BLOCK        write the analysis design outputs into the .zen (prints each change)
  python3 tools/bind.py --init BLOCK         (re)generate benches/<Block>/bind.zen; write a draft topology.toml when
                                             the block has none (a new block, or one being bound)

Per bound block (bench benches/<Block>/, instance = the one the bench export configures), against the netlist that
`pcb test benches/<Block>/bind.zen` prints (tools/bind/extract.zen):
  BIND-EXTRACT  the export fails, or benches/<Block>/bind.zen is missing or not what --init generates
  BIND-TOPO     a part rule or topology precondition is broken; names the precondition and the report sections that
                are no longer valid
  BIND-SECTION  a precondition names no section, or one the committed design-analysis.md does not have
  BIND-PARAMS   params.json repeats a design value (one source: the .zen)
  BIND-DESIGN   a design value in the .zen differs from the one the committed report was computed with (re-run the
                analysis)
  BIND-OUTPUT   a design output (design-outputs.json) differs from the .zen config; names file, config, required value
  BIND-SPEC     the generated account in spec.md is missing or not what account.json renders to, or names an open
                item spec.md does not declare
Blocks without topology.toml are listed as UNBOUND (not a failure). Generated inputs (account.json,
design-outputs.json) are checked for staleness by tools/check_reports.py; run it first (tools/verify.py does).
"""

import argparse
import json
import sys
from pathlib import Path

ROOT_DEFAULT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DEFAULT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from analysis import _bind  # noqa: E402
from _findings import Finding, load_allowlist, report  # noqa: E402

RULES = {"BIND-EXTRACT", "BIND-TOPO", "BIND-SECTION", "BIND-PARAMS", "BIND-DESIGN", "BIND-OUTPUT", "BIND-SPEC"}


def blocks(root, names=()):
    """(bound, unbound) block names: a block is bound when blocks/<Block>/topology.toml exists."""
    all_ = sorted(p.parent.name for p in (root / "blocks").glob("*/analysis.py"))
    pick = list(names) or all_
    return ([b for b in pick if (root / "blocks" / b / "topology.toml").is_file()],
            [b for b in pick if not (root / "blocks" / b / "topology.toml").is_file()])


def paths(root, block):
    docs = root / "benches" / block / "docs"
    return {"md": docs / "design-analysis.md", "outputs": docs / "design-analysis" / "design-outputs.json",
            "account": docs / "design-analysis" / "account.json", "params": root / "benches" / block / "params.json",
            "spec": root / "blocks" / block / "spec.md"}


def rel(root, p):
    return str(Path(p).relative_to(root))


def _json(path):
    return json.loads(path.read_text()) if path.is_file() else None


def check_block(root, block, ext=None):
    """Findings for one bound block. ext: a bench export to use instead of running pcb (tests)."""
    f, P = [], paths(root, block)
    topo_rel = f"blocks/{block}/topology.toml"
    topo = _bind.topology(root, block)
    zen = _bind.bind_zen(root, block)
    try:
        if not zen.is_file() or zen.read_text() != _bind.render_bind_zen(root, block):
            f.append(Finding("BIND-EXTRACT", rel(root, zen), "", "missing or not what the block, its reference.zen and the "
                             f"bench declare; regenerate: python3 tools/bind.py --init {block}"))
        ext = ext if ext is not None else _bind.extract(root, block)
        inst = _bind.instance_of(ext)
    except _bind.BindError as e:
        return f + [Finding("BIND-EXTRACT", rel(root, zen), "", str(e))]

    for pid, what, detail, secs in _bind.check_topology(topo, ext, inst):
        f.append(Finding("BIND-TOPO", topo_rel, pid, f"{what}: {detail}. Report sections no longer valid: "
                                                      f"{', '.join(secs) or 'none listed'}"))

    md = P["md"].read_text() if P["md"].is_file() else ""
    have = _bind.report_sections(md)
    for pre in topo.get("pre", []):
        if not pre.get("sections"):
            f.append(Finding("BIND-SECTION", topo_rel, pre["id"], "names no report section (list what it invalidates)"))
        for s in pre.get("sections", []):
            if s not in have:
                f.append(Finding("BIND-SECTION", topo_rel, pre["id"], f"section '{s}' is not a heading of {rel(root, P['md'])}"))

    params = _json(P["params"]) or {}
    for key, msg in _bind.check_params(topo, params, rel(root, P["params"])):
        f.append(Finding("BIND-PARAMS", rel(root, P["params"]), key, msg))

    account = _json(P["account"])
    if account is None:
        f.append(Finding("BIND-DESIGN", rel(root, P["account"]), "", "missing: run the analysis"))
    else:
        try:
            values, sources = _bind.design_values(topo, ext, inst)
        except (_bind.BindError, ValueError, KeyError) as e:
            values, sources = {}, {}
            f.append(Finding("BIND-DESIGN", topo_rel, "", f"design values cannot be read from the netlist: {e}"))
        used = {d["key"]: d for d in account.get("design", [])}
        for key in sorted(set(values) | set(used)):
            now = repr(values[key]) if key in values else None
            was = used.get(key, {}).get("value")
            if now != was and values:
                f.append(Finding("BIND-DESIGN", rel(root, P["account"]), key,
                                 f"the report was computed with {key}={was}, the .zen now gives {now} "
                                 f"({sources.get(key, 'not read')}); run python3 blocks/{block}/analysis.py and review"))

    outputs = _json(P["outputs"])
    if outputs is None:
        f.append(Finding("BIND-OUTPUT", rel(root, P["outputs"]), "", "missing: run the analysis"))
    else:
        for cfg, file, msg in _bind.check_outputs(topo, ext, inst, outputs, rel(root, P["outputs"]), block):
            f.append(Finding("BIND-OUTPUT", file, cfg, msg))

    spec = P["spec"].read_text() if P["spec"].is_file() else ""
    span = _bind.spec_block(spec)
    if account is not None:
        ois = _bind.spec_open_ids(spec)
        for r in account.get("requirements", []):
            for oi in r["open_items"]:
                if oi not in ois:
                    f.append(Finding("BIND-SPEC", rel(root, P["spec"]), oi,
                                     f"account row '{r['requirement']}' names open item {oi}, not declared under Open items"))
        if span is None:
            f.append(Finding("BIND-SPEC", rel(root, P["spec"]), "", "no generated account block (one BEGIN and one END "
                             f"marker); run python3 tools/bind.py --write-spec {block}"))
        elif spec[span[0]:span[1]] != _bind.render_account(account, ois):
            f.append(Finding("BIND-SPEC", rel(root, P["spec"]), "", "generated account is stale or was edited by hand; "
                             f"run python3 tools/bind.py --write-spec {block} and review"))
    return f


def write_spec(root, block):
    P = paths(root, block)
    account, spec = _json(P["account"]), P["spec"].read_text()
    if account is None:
        raise SystemExit(f"{rel(root, P['account'])} missing: run python3 blocks/{block}/analysis.py first")
    block_md = _bind.render_account(account, _bind.spec_open_ids(spec))
    span = _bind.spec_block(spec)
    if span is None:
        if _bind.SPEC_BEGIN in spec or _bind.SPEC_END in spec:
            raise SystemExit(f"{rel(root, P['spec'])}: malformed generated-account markers; fix by hand")
        anchor = spec.find("\n## Open Questions")
        if anchor < 0:
            raise SystemExit(f"{rel(root, P['spec'])}: no markers and no '## Open Questions' heading to insert before")
        new = spec[:anchor] + "\n" + block_md + "\n" + spec[anchor:]
    else:
        new = spec[:span[0]] + block_md + spec[span[1]:]
    if new != spec:
        P["spec"].write_text(new)
        print(f"wrote {rel(root, P['spec'])}")
    else:
        print(f"{rel(root, P['spec'])}: unchanged")


def apply(root, block):
    P = paths(root, block)
    outputs = _json(P["outputs"])
    if outputs is None:
        raise SystemExit(f"{rel(root, P['outputs'])} missing: run python3 blocks/{block}/analysis.py first")
    ext = _bind.extract(root, block)
    changes = _bind.apply_outputs(root, block, _bind.topology(root, block), outputs, ext, _bind.instance_of(ext))
    for file, key, old, new in changes:
        print(f"{file}: {key} {old} -> {new} ({outputs[key]['basis']})")
    if changes:
        print("Review the diff, then run pcb build on the bench, tools/verify.py, and the schematic gates.")
    else:
        print("no change: every design output already matches the .zen")


def init(root, block):
    zen = _bind.bind_zen(root, block)
    text = _bind.render_bind_zen(root, block)
    if not zen.is_file() or zen.read_text() != text:
        zen.write_text(text)
        print(f"wrote {rel(root, zen)}")
    topo = root / "blocks" / block / "topology.toml"
    if topo.is_file():
        print(f"{rel(root, topo)} exists: left as is")
        return
    ext = _bind.extract(root, block)
    topo.write_text(_bind.draft_topology(root, block, ext, _bind.instance_of(ext)))
    print(f"wrote draft {rel(root, topo)}: edit it (sections, design keys, outputs), then wire the analysis "
          "(analysis/_bind.py header) and run python3 tools/bind.py " + block)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("blocks", nargs="*")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--write-spec", action="store_true")
    g.add_argument("--apply", action="store_true")
    g.add_argument("--init", action="store_true")
    ap.add_argument("--root", default=ROOT_DEFAULT)
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    if a.init:
        if len(a.blocks) != 1:
            raise SystemExit("--init takes exactly one block")
        init(root, a.blocks[0])
        return 0
    bound, unbound = blocks(root, a.blocks)
    if a.apply:
        if len(a.blocks) != 1 or not bound:
            raise SystemExit("--apply takes exactly one bound block")
        apply(root, bound[0])
        return 0
    if a.write_spec:
        for b in bound:
            write_spec(root, b)
        return 0
    findings = []
    for b in bound:
        findings += check_block(root, b)
    for b in unbound:
        print(f"UNBOUND {b}: no blocks/{b}/topology.toml (its design values and topology are not checked)")
    return report("bind", findings, load_allowlist(root / "tools" / "known_findings.toml"), RULES, len(bound))


if __name__ == "__main__":
    sys.exit(main())
