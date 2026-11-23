"""Tests for tools/bind.py and analysis/_bind.py.

Unit tests use a hand-built bench export (no pcb). The rest run against the real repo and need pcb, the zener-library
checkout and, where an analysis runs, Chrome for the report PDF ($CHROME); without them they skip, except under CI (CI set),
where a skip would hide a broken gate, so they fail:
  EveryBoundBlock  for each block with topology.toml, on its live bench export: every precondition holds, and breaking
                   each one (extra pin, moved pin, merged nets, different range, wrong MPN), each part (DNP, missing),
                   each design output fails. A new block is covered by adding its topology.toml.
  FullCircle       TEMP COPIES of the repo: per bound block, an output out of step fails and `--apply` repairs it (pcb
                   re-reads the written text), a hand edit of the spec account fails and `--write-spec` repairs it, an
                   edited bind.zen fails and `--init` repairs it, and the `--init` draft of a block holds on its netlist;
                   for INA229HighSideMonitor, issue #14 cases a-d through real .zen edits and analysis runs.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from analysis import _bind  # noqa: E402
import bind  # noqa: E402

BLOCK = "INA229HighSideMonitor"


def ext_fixture():
    """A bench export shaped like `pcb test benches/<B>/bind.zen` for a two-part block."""
    return {
        "nets": {
            "SENSE_P": [["X.SHUNT.WSK2512", "E1"], ["X.U.INA229AIDGS", "IN+"]],
            "BUS_IN": [["X.SHUNT.WSK2512", "I1"]],
            "BUS_OUT": [["X.SHUNT.WSK2512", "I2"], ["X.U.INA229AIDGS", "VBUS"], ["LOAD.R", "1"]],
        },
        "components": {
            "X.SHUNT.WSK2512": {"component": "WSK2512", "mpn": "M1", "dnp": False,
                                "pins": {"E1": "SENSE_P", "I1": "BUS_IN", "I2": "BUS_OUT"}},
            "X.U.INA229AIDGS": {"component": "INA229AIDGS", "mpn": "M2", "dnp": False,
                                "pins": {"IN+": "SENSE_P", "VBUS": "BUS_OUT"}},
            "LOAD.R": {"component": "ERJ-3EKF", "mpn": "M3", "dnp": False, "pins": {"1": "BUS_OUT"}},
        },
        "voltages": {"BUS_IN": {"text": "12-40V", "min": 12.0, "max": 40.0, "nominal": 26.0, "tolerance": 0.5},
                     "BUS_OUT": {"text": "12-40V", "min": 12.0, "max": 40.0, "nominal": 26.0, "tolerance": 0.5}},
        "configs": {"X": {"rshunt": {"text": "10m", "value": 0.01, "min": 0.01, "max": 0.01},
                          "vsense_max": {"text": "60.5mV", "value": 0.0605, "min": 0.0605, "max": 0.0605},
                          "v_vgs": {"text": "0V to 13.86V", "value": 6.93, "min": 0.0, "max": 13.86}}},
        "bench": {"ambient": {"text": "0-70C", "unit": "K", "value": 308.15, "min": 273.15, "max": 343.15},
                  "derate": {"text": "1.0", "value": 1.0}},
    }


TOPO = {
    "parts": {"exclusive": True, "U": "INA229AIDGS", "SHUNT": "WSK2512"},
    "pre": [
        {"id": "SP", "what": "Kelvin", "exact": ["SHUNT.E1", "U.IN+"], "sections": ["1", "5b"]},
        {"id": "VB", "what": "VBUS", "contains": ["SHUNT.I2", "U.VBUS"], "sections": ["7"]},
        {"id": "PATH", "what": "path", "separate": ["SHUNT.I1", "SHUNT.I2"], "sections": ["2"]},
        {"id": "RANGE", "what": "range", "same_voltage": ["SHUNT.I1", "SHUNT.I2"], "sections": ["7"]},
    ],
    "design": {"rshunt_ohm": {"config": "rshunt"}, "vbus_hi_v": {"net": "U.VBUS", "field": "max"},
               "ta_max_c": {"bench": "ambient", "field": "max"}, "derate": {"bench": "derate"}},
    "outputs": {"vsense_max": {}},
}


class Topology(unittest.TestCase):
    def ids(self, ext):
        return [f[0] for f in _bind.check_topology(TOPO, ext, "X")]

    def test_holds(self):
        self.assertEqual(self.ids(ext_fixture()), [])

    def test_exact_extra_member_fails_and_names_sections(self):
        e = ext_fixture()
        e["nets"]["SENSE_P"].append(["X.C.GCM188", "1"])
        e["components"]["X.C.GCM188"] = {"component": "GCM188", "mpn": "C", "dnp": False, "pins": {"1": "SENSE_P"}}
        f = {x[0]: x for x in _bind.check_topology(TOPO, e, "X")}
        self.assertIn("SP", f)
        self.assertEqual(f["SP"][3], ["1", "5b"])
        self.assertIn("C.1", f["SP"][2])
        self.assertIn("PARTS-ONLY", f)  # the extra part is outside [parts]

    def test_contains_allows_board_parts_but_not_a_move(self):
        e = ext_fixture()  # LOAD.R on BUS_OUT is outside the instance: allowed by contains
        self.assertNotIn("VB", self.ids(e))
        e["components"]["X.U.INA229AIDGS"]["pins"]["VBUS"] = "BUS_IN"
        self.assertIn("VB", self.ids(e))

    def test_separate_and_same_voltage(self):
        e = ext_fixture()
        e["components"]["X.SHUNT.WSK2512"]["pins"]["I1"] = "BUS_OUT"
        self.assertIn("PATH", self.ids(e))
        e = ext_fixture()
        e["voltages"]["BUS_IN"] = dict(e["voltages"]["BUS_IN"], max=60.0, text="12-60V")
        self.assertIn("RANGE", self.ids(e))

    def test_missing_role_and_dnp(self):
        e = ext_fixture()
        e["components"]["X.U.INA229AIDGS"]["dnp"] = True
        self.assertIn("PART-U", self.ids(e))
        del e["components"]["X.U.INA229AIDGS"]
        ids = self.ids(e)
        self.assertIn("PART-U", ids)
        self.assertIn("SP", ids)  # a precondition on a missing role fails ("cannot evaluate"), never passes

    def test_report_sections_and_inverse_map(self):
        md = "# T\n## Design definition\n## 1. A\n## 5b. B\n### 3.1 sub\n## 12. C\n"
        self.assertEqual(_bind.report_sections(md), {"Design definition", "1", "5b", "12"})
        self.assertEqual(list(_bind.sections_by_pre(TOPO)), ["1", "2", "5b", "7"])


class Values(unittest.TestCase):
    def test_design_values(self):
        v, src = _bind.design_values(TOPO, ext_fixture(), "X")
        self.assertEqual(v, {"rshunt_ohm": 0.01, "vbus_hi_v": 40.0, "ta_max_c": 70.0, "derate": 1.0})
        self.assertIn("rshunt", src["rshunt_ohm"])

    def test_kelvin_float_back_to_degc(self):
        e = ext_fixture()
        e["bench"]["ambient"]["min"] = 233.15  # -40 degC; 233.15 - 273.15 is -39.99999999999997 in float
        topo = dict(TOPO, design={"t": {"bench": "ambient", "field": "min"}})
        self.assertEqual(_bind.design_values(topo, e, "X")[0]["t"], -40.0)

    def test_missing_field_is_an_error_never_a_default(self):
        e = ext_fixture()
        del e["voltages"]["BUS_OUT"]
        with self.assertRaises(_bind.BindError):
            _bind.design_values(TOPO, e, "X")

    def test_params_must_not_repeat_design_values(self):
        f = _bind.check_params(TOPO, {"rshunt_ohm": 0.01, "rshunt_ohm_basis": "x", "i_fs_a": 6}, "p.json")
        self.assertEqual([k for k, _ in f], ["rshunt_ohm"])

    def test_outputs(self):
        e = ext_fixture()
        ok = {"vsense_max": {"value": 0.0605, "unit": "V", "basis": "b"}}
        self.assertEqual(_bind.check_outputs(TOPO, e, "X", ok, "o.json", "B"), [])
        bad = {"vsense_max": {"value": 0.0603, "unit": "V", "basis": "b"}}
        (cfg, file, msg), = _bind.check_outputs(TOPO, e, "X", bad, "o.json", "B")
        self.assertEqual((cfg, file), ("vsense_max", "blocks/B/reference.zen"))
        self.assertIn("60.3mV", msg)

    def test_range_outputs_compare_both_bounds(self):
        have = ext_fixture()["configs"]["X"]["v_vgs"]
        self.assertTrue(_bind.output_matches({"min": 0.0, "max": 13.86}, have))
        self.assertFalse(_bind.output_matches({"min": 0.0, "max": 13.9}, have))
        self.assertFalse(_bind.output_matches({"value": 13.86}, have))  # a range config is not a single value

    def test_zen_number(self):
        z = _bind.zen_number
        self.assertEqual([z(0.0273, "V"), z(536e3, "ohm"), z(1e-7, "V"), z(-0.45445225, "V"), z(0, "V"), z(40.0, "V")],
                         ["27.3mV", "536kohm", "100nV", "-454.45225mV", "0V", "40V"])
        self.assertEqual(_bind.output_text({"min": -1.2, "max": 40.0, "unit": "V"}), "-1.2V to 40V")

    def test_apply_outputs_rewrites_only_what_differs(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "blocks/B/reference.zen"
            p.parent.mkdir(parents=True)
            p.write_text('REFERENCE = {\n    "rshunt": "10mohm",\n    "vsense_max": "60.5mV",\n}\n')
            same = {"vsense_max": {"value": 0.0605, "unit": "V", "basis": "b"}}
            self.assertEqual(_bind.apply_outputs(t, "B", TOPO, same, ext_fixture(), "X"), [])
            new = {"vsense_max": {"value": 0.0603, "unit": "V", "basis": "b"}}
            ch = _bind.apply_outputs(t, "B", TOPO, new, ext_fixture(), "X")
            self.assertEqual(ch, [("blocks/B/reference.zen", "vsense_max", "60.5mV", "60.3mV")])
            self.assertIn('"vsense_max": "60.3mV"', p.read_text())
            self.assertIn('"rshunt": "10mohm"', p.read_text())

    def test_draft_topology_holds_on_its_own_netlist(self):
        import tomllib
        with tempfile.TemporaryDirectory() as t:
            zen = Path(t) / "blocks/B/B.zen"
            zen.parent.mkdir(parents=True)
            zen.write_text("SENSE_P = Net()\n")
            draft = tomllib.loads(_bind.draft_topology(t, "B", ext_fixture(), "X"))
        self.assertEqual(dict(draft["parts"]), {"exclusive": True, "SHUNT": "WSK2512", "U": "INA229AIDGS"})
        kinds = {p["id"]: ("exact" if "exact" in p else "contains") for p in draft["pre"]}
        self.assertEqual(kinds, {"SENSE-P": "exact", "BUS-OUT": "contains"})  # BUS_IN has one instance pin: skipped
        self.assertEqual(_bind.check_topology(draft, ext_fixture(), "X"), [])


class Account(unittest.TestCase):
    ACC = {"instance": "X", "report": "r.md", "library": "v0", "topology": "t.toml", "inputs": {"rows": 3, "human": 1},
           "requirements": [{"requirement": "R", "result": "1 V", "limit": "2 V", "margin": "1 V", "status": "PASS",
                             "section": "1", "rests_on": [], "open_items": ["OI-A"]}],
           "report_only": 0, "design": [], "outputs": {}, "preconditions": ["SP"]}

    def test_block_roundtrip_and_markers(self):
        md = _bind.render_account(self.ACC, {"OI-A"})
        spec = "# S\n\n" + md + "\n\n## Open items\n\n- `OI-A` **a**\n"
        a, b = _bind.spec_block(spec)
        self.assertEqual(spec[a:b], md)
        self.assertEqual(_bind.spec_open_ids(spec), {"OI-A"})
        self.assertIn("**1 of 3**", md)
        self.assertIsNone(_bind.spec_block(spec + "\n" + _bind.SPEC_END))  # two END markers: malformed


def _need_tools():
    """Reason to skip the integration tests, or None. Under CI a missing tool fails instead."""
    missing = [t for t in ("pcb", "rsync") if shutil.which(t) is None]
    zl = Path(os.environ.get("ZENER_LIBRARY", ROOT.parent / "zener-library"))
    if not (zl / "lib" / "__init__.py").is_file():
        missing.append(f"zener-library at {zl}")
    if not missing:
        return None
    if os.environ.get("CI"):
        raise AssertionError(f"bind integration tests need {', '.join(missing)} in CI")
    return f"needs {', '.join(missing)}"


SKIP = _need_tools()
BOUND = bind.blocks(ROOT)[0]


def _move(ext, inst, pin, new_net):
    """Move '<role>.<pin>' to a new net in an export (both the component's pin map and the net lists)."""
    path, name = _bind._member(ext, inst, pin)
    old = ext["components"][path]["pins"][name]
    ext["nets"][old] = [m for m in ext["nets"][old] if m != [path, name]]
    ext["nets"].setdefault(new_net, []).append([path, name])
    ext["components"][path]["pins"][name] = new_net
    if old in ext["voltages"]:
        ext["voltages"][new_net] = dict(ext["voltages"][old])


def breakers(pre, ext, inst):
    """(label, mutated export) pairs that each must break this precondition."""
    import copy
    out = []
    for kind in ("exact", "contains"):
        for gi, group in enumerate(_bind._groups(pre.get(kind, []))):
            e = copy.deepcopy(ext)
            _move(e, inst, group[-1], f"MOVED_{gi}")
            out.append((f"{kind} group {gi}: {group[-1]} moved", e))
            if kind == "exact":
                e = copy.deepcopy(ext)
                e["nets"][_bind.pin_net(e, inst, group[0])].append(["ZZ.EXTRA.X", "1"])
                out.append((f"exact group {gi}: extra member", e))
    if "separate" in pre:
        e = copy.deepcopy(ext)
        a, b = pre["separate"][:2]
        _move(e, inst, b, _bind.pin_net(e, inst, a))
        out.append((f"separate: {b} merged onto {a}", e))
    if "same_voltage" in pre:
        e = copy.deepcopy(ext)
        net = _bind.pin_net(e, inst, pre["same_voltage"][-1])
        e["voltages"][net] = dict(e["voltages"][net], max=e["voltages"][net]["max"] + 1.0, text="changed")
        out.append(("same_voltage: range changed", e))
    if "mpn" in pre:
        e = copy.deepcopy(ext)
        e["components"][_bind.role_path(e, inst, pre["mpn"])]["mpn"] += "X"
        out.append(("mpn: changed", e))
    return out


@unittest.skipIf(SKIP, SKIP or "")
class EveryBoundBlock(unittest.TestCase):
    """Generic: needs only a block's topology.toml and its bench export."""

    @classmethod
    def setUpClass(cls):
        cls.exts = {b: _bind.extract(ROOT, b) for b in BOUND}

    def test_some_block_is_bound(self):
        self.assertGreaterEqual(len(BOUND), 2, "INA229HighSideMonitor and TPS48000HighSideSwitch are bound")

    def test_every_precondition_holds_and_each_breaker_fails_it(self):
        for b in BOUND:
            topo, ext = _bind.topology(ROOT, b), self.exts[b]
            inst = _bind.instance_of(ext)
            self.assertEqual(_bind.check_topology(topo, ext, inst), [], b)
            for pre in topo["pre"]:
                cases = breakers(pre, ext, inst)
                self.assertTrue(cases, f"{b} {pre['id']}: no breaker for its kinds")
                for label, e in cases:
                    with self.subTest(block=b, pre=pre["id"], mutation=label):
                        self.assertIsNotNone(_bind.check_pre(pre, e, inst))

    def test_every_part_rule_fires(self):
        import copy
        for b in BOUND:
            topo, ext = _bind.topology(ROOT, b), self.exts[b]
            inst = _bind.instance_of(ext)
            for role in [r for r in topo["parts"] if r != "exclusive"]:
                for label, fn in (("dnp", lambda e, p: e["components"][p].update(dnp=True)),
                                  ("missing", lambda e, p: e["components"].pop(p))):
                    e = copy.deepcopy(ext)
                    fn(e, _bind.role_path(e, inst, role))
                    with self.subTest(block=b, role=role, mutation=label):
                        self.assertIn(f"PART-{role}", [f[0] for f in _bind.check_topology(topo, e, inst)])
            e = copy.deepcopy(ext)
            e["components"][f"{inst}.ZZ_EXTRA.ERJ-3EKF"] = {"component": "ERJ-3EKF", "mpn": "X", "dnp": False, "pins": {}}
            self.assertIn("PARTS-ONLY", [f[0] for f in _bind.check_topology(topo, e, inst)])

    def test_every_design_output_is_checked(self):
        import copy
        for b in BOUND:
            topo, ext = _bind.topology(ROOT, b), self.exts[b]
            inst = _bind.instance_of(ext)
            outs = json.loads((ROOT / f"benches/{b}/docs/design-analysis/design-outputs.json").read_text())
            self.assertEqual(_bind.check_outputs(topo, ext, inst, outs, "o", b), [], b)
            for cfg in topo["outputs"]:
                bad = copy.deepcopy(outs)
                k = "max" if "max" in bad[cfg] else "value"
                bad[cfg][k] = bad[cfg][k] * 1.01 + 1e-6
                with self.subTest(block=b, output=cfg):
                    self.assertEqual([f[0] for f in _bind.check_outputs(topo, ext, inst, bad, "o", b)], [cfg])

    def test_design_values_are_the_ones_the_report_used(self):
        for b in BOUND:
            ext = self.exts[b]
            values, _ = _bind.design_values(_bind.topology(ROOT, b), ext, _bind.instance_of(ext))
            acc = json.loads((ROOT / f"benches/{b}/docs/design-analysis/account.json").read_text())
            self.assertEqual({d["key"]: d["value"] for d in acc["design"]}, {k: repr(v) for k, v in values.items()}, b)


@unittest.skipIf(SKIP, SKIP or "")
class FullCircle(unittest.TestCase):
    """Issue #14 test cases a-d on a temp copy of the repo, each followed by the repair the tools offer."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory(prefix="bind_test_")
        self.root = Path(self._t.name) / "repo"
        r = subprocess.run(["rsync", "-a", "--exclude", ".git", "--exclude", "__pycache__", "--exclude", "*.lck",
                            f"{ROOT}/", f"{self.root}/"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                        ZENER_LIBRARY=str(Path(os.environ.get("ZENER_LIBRARY", ROOT.parent / "zener-library")).resolve()))

    def tearDown(self):
        self._t.cleanup()

    def run_py(self, *args):
        p = subprocess.run([sys.executable, *args], cwd=self.root, env=self.env, capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr

    def bind(self, block=BLOCK):
        return self.run_py("tools/bind.py", block)

    def analysis(self):
        rc, out = self.run_py(f"blocks/{BLOCK}/analysis.py")
        self.assertEqual(rc, 0, out[-2000:])

    def edit(self, rel, old, new):
        p = self.root / rel
        text = p.read_text()
        self.assertEqual(text.count(old), 1, f"{rel}: '{old}' must occur once")
        p.write_text(text.replace(old, new))

    def outputs(self):
        return json.loads((self.root / f"benches/{BLOCK}/docs/design-analysis/design-outputs.json").read_text())

    def test_unmodified_repo_passes(self):
        rc, out = self.run_py("tools/bind.py")
        self.assertEqual(rc, 0, out)
        self.assertIn(f"bind: PASS ({len(BOUND)} checked", out)

    def test_every_block_output_out_of_step_then_apply(self):
        """Each bound block: write a wrong value for every design output into the .zen (as text pcb must parse), bind
        fails naming each config, --apply writes the analysis values back, bind passes."""
        for b in BOUND:
            topo = _bind.topology(self.root, b)
            outs = json.loads((self.root / f"benches/{b}/docs/design-analysis/design-outputs.json").read_text())
            wrong = {k: dict(v, **({"max": v["max"] * 1.5 + 1e-3} if "max" in v else {"value": v["value"] * 1.5}))
                     for k, v in outs.items()}
            ext = _bind.extract(self.root, b)
            self.assertEqual(len(_bind.apply_outputs(self.root, b, topo, wrong, ext, _bind.instance_of(ext))), len(outs))
            rc, out = self.bind(b)
            self.assertEqual(rc, 1, out)
            self.assertEqual(sorted(re.findall(r"\[BIND-OUTPUT\] \S+ (\S+):", out)), sorted(topo["outputs"]), out)
            rc, out = self.run_py("tools/bind.py", "--apply", b)
            self.assertEqual(rc, 0, out)
            rc, out = self.bind(b)
            self.assertEqual(rc, 0, f"{b}: {out}")

    def test_every_block_spec_hand_edit_then_write_spec(self):
        for b in BOUND:
            spec = self.root / f"blocks/{b}/spec.md"
            text = spec.read_text()
            a, _ = _bind.spec_block(text)
            spec.write_text(text[:a] + text[a:].replace("| PASS |", "| FAIL |", 1))
            rc, out = self.bind(b)
            self.assertEqual(rc, 1, out)
            self.assertIn("generated account is stale or was edited by hand", out)
            self.assertEqual(self.run_py("tools/bind.py", "--write-spec", b)[0], 0)
            self.assertEqual(self.bind(b)[0], 0)

    def test_every_block_bind_zen_edit_then_init(self):
        for b in BOUND:
            zen = self.root / f"benches/{b}/bind.zen"
            zen.write_text(zen.read_text().replace('name="bind"', 'name="bind2"'))
            rc, out = self.bind(b)
            self.assertEqual(rc, 1, out)
            self.assertIn("[BIND-EXTRACT]", out)
            self.assertEqual(self.run_py("tools/bind.py", "--init", b)[0], 0)
            self.assertEqual(self.bind(b)[0], 0)

    def test_init_draft_for_a_block_being_bound(self):
        """The new-block path: without topology.toml, --init writes a draft that holds on the netlist as built, and bind
        fails until every precondition names the report sections it invalidates."""
        for b in BOUND:
            (self.root / f"blocks/{b}/topology.toml").unlink()
            rc, out = self.run_py("tools/bind.py", "--init", b)
            self.assertEqual(rc, 0, out)
            draft = _bind.topology(self.root, b)
            ext = _bind.extract(self.root, b)
            self.assertEqual(_bind.check_topology(draft, ext, _bind.instance_of(ext)), [], b)
            self.assertGreater(len(draft["pre"]), 3)
            rc, out = self.bind(b)
            self.assertEqual(rc, 1, out)
            self.assertIn("[BIND-SECTION]", out)

    def test_a_resistor_between_shunt_e1_and_in_plus(self):
        zen = f"blocks/{BLOCK}/{BLOCK}.zen"
        self.edit(zen, "SENSE_N = Net(voltage=BUS_OUT.voltage)\n",
                  "SENSE_N = Net(voltage=BUS_OUT.voltage)\nSENSE_P_R = Net(voltage=BUS_IN.voltage)\n")
        self.edit(zen, "    IN_P=SENSE_P,\n", "    IN_P=SENSE_P_R,\n")
        self.edit(zen, "# Decoupling at the VS pin.\n",
                  'R_TEST = ERJ3EKF(name="R_TEST", value="10ohm", P1=SENSE_P, P2=SENSE_P_R, derate=derate, **_AMB)\n\n'
                  "# Decoupling at the VS pin.\n")
        rc, out = self.bind()
        self.assertEqual(rc, 1, out)
        line = next(l for l in out.splitlines() if "[BIND-TOPO]" in l and " SENSE-P:" in l)
        self.assertIn("Report sections no longer valid: 1, 5, 5b, 6, 8, 11", line)
        self.assertIn("not on one net", line)
        self.assertRegex(out, r"\[BIND-TOPO\] .* PARTS-ONLY: .*R_TEST")

    def test_b_rshunt_changed_in_the_zen_only(self):
        self.edit(f"blocks/{BLOCK}/reference.zen", '"rshunt": "10mohm"', '"rshunt": "12mohm"')
        rc, out = self.bind()
        self.assertEqual(rc, 1, out)
        self.assertRegex(out, r"\[BIND-DESIGN\] \S+account\.json rshunt_ohm: the report was computed with "
                              r"rshunt_ohm=0\.01, the \.zen now gives 0\.012")
        # Full circle: re-run the analysis; the shunt voltage moves, so the .zen config and the spec are now stale.
        self.analysis()
        new = self.outputs()["vsense_max"]["text"]
        self.assertNotEqual(new, "60.5mV")
        rc, out = self.bind()
        self.assertEqual(rc, 1, out)
        self.assertRegex(out, rf"\[BIND-OUTPUT\] blocks/{BLOCK}/reference\.zen vsense_max: .*config `vsense_max` is "
                              rf"60\.5mV, the analysis requires {re.escape(new)}")
        self.assertIn("[BIND-SPEC]", out)
        rc, out = self.run_py("tools/bind.py", "--apply", BLOCK)
        self.assertEqual(rc, 0, out)
        self.assertIn(f"vsense_max 60.5mV -> {new}", out)
        self.assertEqual(self.run_py("tools/bind.py", "--write-spec", BLOCK)[0], 0)
        rc, out = self.bind()
        self.assertEqual(rc, 0, out)
        self.assertIn(f'"vsense_max": "{new}"', (self.root / f"blocks/{BLOCK}/reference.zen").read_text())

    def test_c_tolerance_code_changes_vsense_max_zen_not_updated(self):
        self.edit(f"blocks/{BLOCK}/reference.zen", '"rshunt_tol": "D"', '"rshunt_tol": "B"')
        self.analysis()  # the report is regenerated; only the .zen config is left behind
        new = self.outputs()["vsense_max"]
        self.assertNotEqual(new["text"], "60.5mV", "the tolerance change must move vsense_max for this test to mean anything")
        rc, out = self.bind()
        self.assertEqual(rc, 1, out)
        self.assertRegex(out, rf"\[BIND-OUTPUT\] blocks/{BLOCK}/reference\.zen vsense_max: blocks/{BLOCK}/reference\.zen: "
                              rf"config `vsense_max` is 60\.5mV, the analysis requires {re.escape(new['text'])}")

    def test_d_hand_edit_of_generated_spec_section(self):
        spec = f"blocks/{BLOCK}/spec.md"
        text = (self.root / spec).read_text()
        m = re.search(r"checked by a person \(current human stamp\): \*\*(\d+) of (\d+)\*\*", text)
        self.assertIsNotNone(m)
        self.edit(spec, m.group(0), m.group(0).replace(f"**{m.group(1)} of", f"**{m.group(2)} of"))
        rc, out = self.bind()
        self.assertEqual(rc, 1, out)
        self.assertRegex(out, r"\[BIND-SPEC\] .*spec\.md : generated account is stale or was edited by hand")
        self.assertEqual(self.run_py("tools/bind.py", "--write-spec", BLOCK)[0], 0)
        self.assertEqual(self.bind()[0], 0)

    def test_tps_mode_pin_moved_in_the_zen(self):
        """TPS48000HighSideSwitch: CS_SEL moved from GND to V3V3 in the block .zen (a different sense mode than the one
        analysed). The bench still builds; bind names MODE-PINS and the sections it invalidates."""
        b = "TPS48000HighSideSwitch"
        self.edit(f"blocks/{b}/{b}.zen", "    CS_SEL=_GND,\n", "    CS_SEL=V3V3,\n")
        r = subprocess.run(["pcb", "build", f"benches/{b}/bench.zen"], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rc, out = self.bind(b)
        self.assertEqual(rc, 1, out)
        self.assertRegex(out, r"\[BIND-TOPO\] \S+ MODE-PINS: .*Report sections no longer valid: 5, 8")

    def test_e_params_repeating_a_design_value(self):
        p = self.root / f"benches/{BLOCK}/params.json"
        d = json.loads(p.read_text())
        d["rshunt_ohm"] = 0.01
        p.write_text(json.dumps(d, indent=2) + "\n")
        rc, out = self.bind()
        self.assertEqual(rc, 1, out)
        self.assertIn("[BIND-PARAMS]", out)
        rc, out = self.run_py(f"blocks/{BLOCK}/analysis.py")
        self.assertNotEqual(rc, 0)
        self.assertIn("is a design value read from the .zen", out)


if __name__ == "__main__":
    unittest.main()
