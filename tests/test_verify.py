"""Tests for tools/verify.py with a fake command runner (nothing real is built, applied or exported)."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import verify as v  # noqa: E402

ERC_OK = "** ERC report for x **\n[isolated_pin_label]: a\n    ; Warning\n[pin_not_driven]: b\n[pin_not_driven]: c\n"


def write(p, text=""):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


class Fake:
    """Scripted runner. replies: substring of the command -> (rc, output) or a list of them (consumed in order)."""

    def __init__(self, **replies):
        self.calls, self.replies = [], {
            "pcb --version": (0, "pcbc 0.4.102\n"), "check_env": (0, "check_env: PASS\n"),
            "pcb build": (0, ""), "check_reports": (0, "check_reports: PASS\n"),
            "tools/bind.py": (0, "bind: PASS (1 checked, 0 violations, 0 stale allowlist entries, 0 allowlisted findings)\n"),
            "check_sync": (0, "RESULT: IN SYNC\n"),
        }
        self.replies.update(replies)
        self.effects = {}

    def __call__(self, cmd, cwd, env=None):
        s = " ".join(map(str, cmd))
        self.calls.append(s)
        key = next((k for k in self.replies if k in s), None)
        if key is None:
            return 127, f"unscripted: {s}"
        r = self.replies[key]
        if isinstance(r, list):
            r = r.pop(0) if len(r) > 1 else r[0]
        for k, fn in self.effects.items():
            if k in s:
                fn(cmd, cwd)
        return r


def mkrepo(tmp, benches=("B1",)):
    root = Path(tmp) / "repo"
    for b in benches:
        write(root / f"benches/{b}/bench.zen")
    return root


class TestChain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = mkrepo(self.tmp.name, ("B1", "B2"))
        self.logs = Path(self.tmp.name) / "logs"

    def tearDown(self):
        self.tmp.cleanup()

    def go(self, fake, names=()):
        return v.verify(self.root, names, self.logs, fake)

    def test_pass(self):
        rc, lines = self.go(Fake())
        self.assertEqual(rc, 0)
        self.assertEqual(lines[0], "PASS: benches 2/2 built, bound 1, sync IN SYNC")

    def test_steps_run_in_order(self):
        f = Fake()
        self.go(f)
        keys = ("pcb --version", "check_env", "pcb build", "check_reports", "tools/bind.py", "check_sync")
        order = [next(k for k in keys if k in c) for c in f.calls]
        self.assertEqual(order, ["pcb --version", "check_env", "pcb build", "pcb build", "check_reports", "tools/bind.py",
                                 "check_sync"])

    def test_named_bench_only(self):
        f = Fake()
        rc, lines = self.go(f, ["B2"])
        self.assertEqual(rc, 0)
        self.assertEqual([c for c in f.calls if "pcb build" in c], ["pcb build -Dwarnings benches/B2/bench.zen"])
        self.assertTrue(any("check_sync.sh benches/B2/bench.zen" in c for c in f.calls))

    def test_zero_benches_fails(self):
        rc, lines = v.verify(Path(self.tmp.name) / "empty", (), self.logs, Fake())
        self.assertEqual(rc, 1)
        self.assertIn("no benches", lines[1])

    def test_named_bench_missing_fails(self):
        rc, lines = self.go(Fake(), ["Nope"])
        self.assertEqual(rc, 1)
        self.assertIn("benches/Nope/bench.zen:1 bench.zen not found", lines[1])

    def test_pcb_cannot_start_fails(self):
        self.assertEqual(self.go(Fake(**{"pcb --version": (127, "cannot start")}))[0], 1)

    def test_check_env_failure_stops_chain(self):
        f = Fake(check_env=(1, "tools/check_env.py:3 ENV-LCK stale lock\nFAIL x\n"))
        rc, lines = self.go(f)
        self.assertEqual(rc, 1)
        self.assertIn("check_env: FAIL x", lines[1:])
        self.assertFalse(any("pcb build" in c for c in f.calls))

    def test_build_with_diagnostic_is_not_retried(self):
        f = Fake(**{"pcb build": (1, "benches/B1/bench.zen:4:1 Error: bad\n")})
        rc, lines = self.go(f, ["B1"])
        self.assertEqual(rc, 1)
        self.assertEqual(len([c for c in f.calls if "pcb build" in c]), 1)
        self.assertEqual(lines[1], "benches/B1/bench.zen:4 Error: bad")

    def test_build_without_diagnostic_retried_once_and_logged(self):
        f = Fake(**{"pcb build": [(1, ""), (0, "")]})
        rc, lines = self.go(f, ["B1"])
        self.assertEqual(rc, 0)
        self.assertEqual(len([c for c in f.calls if "pcb build" in c]), 2)
        self.assertIn("retried 1", lines[0])
        self.assertTrue(list(self.logs.glob("*build-B1-first.log")))

    def test_build_fails_twice_fails(self):
        rc, lines = self.go(Fake(**{"pcb build": (1, "")}), ["B1"])
        self.assertEqual(rc, 1)
        self.assertIn("no diagnostic", lines[1])

    def test_check_reports_failure(self):
        rc, lines = self.go(Fake(check_reports=(1, "REP-STALE design-analysis.md differs\n")))
        self.assertEqual(rc, 1)
        self.assertIn("REP-STALE", lines[1])

    def test_bind_failure_stops_chain(self):
        f = Fake(**{"tools/bind.py": (1, "FAIL [BIND-TOPO] blocks/B/topology.toml SENSE-P: Kelvin: not on one net\n")})
        rc, lines = self.go(f)
        self.assertEqual(rc, 1)
        self.assertIn("BIND-TOPO", lines[1])
        self.assertFalse(any("check_sync" in c for c in f.calls))

    def test_unbound_block_is_a_note(self):
        f = Fake(**{"tools/bind.py": (0, "UNBOUND T: no blocks/T/topology.toml\nbind: PASS (1 checked, 0 violations)\n")})
        rc, lines = self.go(f)
        self.assertEqual(rc, 0)
        self.assertEqual(lines[0], "PASS: benches 2/2 built, bound 1, sync IN SYNC; UNBOUND T")

    def test_check_sync_exit0_without_in_sync_fails(self):
        self.assertEqual(self.go(Fake(check_sync=(0, "nothing\n")))[0], 1)

    def test_check_sync_drift_fails(self):
        rc, lines = self.go(Fake(check_sync=(1, "      DRIFT: build failed\nRESULT: DRIFT\n")))
        self.assertEqual(rc, 1)
        self.assertIn("DRIFT", lines[1])

    def test_at_most_five_failures(self):
        rc, lines = self.go(Fake(check_env=(1, "".join(f"ERROR e{i}\n" for i in range(9)))))
        self.assertEqual(len([l for l in lines if "ERROR e" in l]), 5)

    def test_deterministic(self):
        self.assertEqual(self.go(Fake()), self.go(Fake()))

    def test_run_cmd_unstartable_is_127(self):
        self.assertEqual(v.run_cmd(["/no/such/bin"], self.tmp.name)[0], 127)


class TestLogDir(unittest.TestCase):
    def test_default_log_dir_is_fresh_per_run(self):
        # two runs at once must not share a log directory (circuit-blocks #8)
        import shutil
        a, b = v.Chain(".", None, None), v.Chain(".", None, None)
        self.addCleanup(shutil.rmtree, a.log_dir, True)
        self.addCleanup(shutil.rmtree, b.log_dir, True)
        self.assertNotEqual(a.log_dir, b.log_dir)
        self.assertTrue(a.log_dir.is_dir() and a.log_dir.name.startswith("cb-logs-"))


class TestErc(unittest.TestCase):
    def test_classify(self):
        counts, bad = v.classify_erc(ERC_OK)
        self.assertEqual(counts, {"isolated_pin_label": 1, "pin_not_driven": 2})
        self.assertEqual(bad, [])

    def test_fatal_classes(self):
        for cls in v.FATAL_ERC:
            counts, bad = v.classify_erc(f"** ERC report **\n[{cls}]: Wire not connected\n")
            self.assertEqual(bad, [f"{cls}: Wire not connected"])

    def test_not_a_report(self):
        self.assertIsNone(v.classify_erc("")[0])
        self.assertIsNone(v.classify_erc("garbage")[0])


class TestSchematic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.root = mkrepo(t)
        self.zl = t / "zl"
        write(self.zl / "tools/schematic/netcmp.py")
        write(self.zl / "tools/schematic/overlap.py")
        self.kicad = write(t / "kicad-cli")
        self.logs = t / "logs"

    def tearDown(self):
        self.tmp.cleanup()

    def fake(self, apply=((0, "schematic unchanged"), (0, "schematic unchanged")), erc=ERC_OK, netcmp=(0, "12 nets baseline\nIDENTICAL\n"),
             sheets=("B1_bench.kicad_sch", "X.kicad_sch")):
        f = Fake(**{"pcb apply": list(apply), "netcmp": netcmp, "overlap": (0, ""), "rsync": (0, ""), "sch erc": (0, ""), "export netlist": (0, "")})
        def rsync(cmd, cwd):
            for s in sheets:
                write(Path(cmd[-1]) / "benches/B1/eda" / s)
        def netlist(cmd, cwd):
            write(cmd[cmd.index("--output") + 1], "(export)")
        def erc_(cmd, cwd):
            write(cmd[cmd.index("-o") + 1], erc)
        f.effects = {"rsync": rsync, "export netlist": netlist, "sch erc": erc_}
        return f

    def go(self, f, **kw):
        return v.verify_schematic(self.root, "B1", self.zl, self.logs, f, kicad=str(self.kicad), **kw)

    def test_pass(self):
        rc, lines = self.go(self.fake())
        self.assertEqual(rc, 0, lines)
        self.assertEqual(lines[0], "PASS: apply unchanged/unchanged, nets 12, erc 0 fatal, 3 other, sheets 2; "
                                   "erc other: isolated_pin_label 1, pin_not_driven 2")

    def test_second_apply_updated_fails(self):
        rc, lines = self.go(self.fake(apply=((0, "schematic updated"), (0, "schematic updated"))))
        self.assertEqual(rc, 1)
        self.assertIn("not 'unchanged'", lines[1])

    def test_first_updated_second_unchanged_passes_with_note(self):
        rc, lines = self.go(self.fake(apply=((0, "schematic updated"), (0, "schematic unchanged"))))
        self.assertEqual(rc, 0)
        self.assertIn("first apply updated", lines[0])

    def test_apply_without_status_line_fails(self):
        rc, lines = self.go(self.fake(apply=((0, "ok"), (0, "ok"))))
        self.assertEqual(rc, 1)
        self.assertIn("no 'schematic unchanged", lines[1])

    def test_apply_exit_nonzero_fails(self):
        self.assertEqual(self.go(self.fake(apply=((1, "Error: x"), (1, "Error: x"))))[0], 1)

    def test_netlist_mismatch_fails(self):
        rc, lines = self.go(self.fake(netcmp=(1, "12 nets baseline\nDIFF VIN a b\nMISMATCH\n")))
        self.assertEqual(rc, 1)
        self.assertIn("DIFF VIN", lines[1])

    def test_zero_nets_fails_closed(self):
        rc, lines = self.go(self.fake(netcmp=(0, "0 nets baseline; 0 new\nIDENTICAL\n")))
        self.assertEqual(rc, 1)
        self.assertIn("zero nets", lines[1])

    def test_fatal_erc_fails_other_counted(self):
        rc, lines = self.go(self.fake(erc=ERC_OK + "[wire_dangling]: Wire end\n"))
        self.assertEqual(rc, 1)
        self.assertIn("erc: FATAL wire_dangling: Wire end", lines[1:])

    def test_missing_erc_report_fails(self):
        self.assertEqual(self.go(self.fake(erc="no report here"))[0], 1)

    def test_overlap_findings_fail(self):
        f = self.fake()
        f.replies["overlap"] = (1, "a.kicad_sch | overlap | text-wire | 1 2 | label\n")
        rc, lines = self.go(f)
        self.assertEqual(rc, 1)

    def test_no_root_sheet_fails(self):
        rc, lines = self.go(self.fake(sheets=("X.kicad_sch",)))
        self.assertEqual(rc, 1)
        self.assertIn("exactly one *_bench.kicad_sch", lines[1])

    def test_missing_zl_tools_fail(self):
        (self.zl / "tools/schematic/netcmp.py").unlink()
        rc, lines = self.go(self.fake())
        self.assertEqual(rc, 1)
        self.assertIn("netcmp.py not found", lines[1])

    def test_missing_kicad_fails(self):
        rc, lines = v.verify_schematic(self.root, "B1", self.zl, self.logs, self.fake(), kicad="/no/kicad-cli")
        self.assertEqual(rc, 1)
        self.assertIn("kicad-cli: not found", lines[1])

    def test_missing_bench_fails(self):
        rc, lines = v.verify_schematic(self.root, "Nope", self.zl, self.logs, self.fake(), kicad=str(self.kicad))
        self.assertEqual(rc, 1)

    def test_repo_never_written_and_temp_removed(self):
        before = sorted(p for p in self.root.rglob("*"))
        self.go(self.fake())
        self.assertEqual(sorted(self.root.rglob("*")), before)
        self.assertFalse(list(Path(tempfile.gettempdir()).glob("cb-verify-*")))

    def test_deterministic(self):
        self.assertEqual(self.go(self.fake()), self.go(self.fake()))


if __name__ == "__main__":
    unittest.main()
