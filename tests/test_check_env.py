"""Tests for tools/check_env.py and tools/_findings.py. Each rule has a PASS fixture and a FAIL fixture."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import check_env as ce  # noqa: E402
import _findings as fd  # noqa: E402

SCH = '''(kicad_sch
	(title_block
		(title "T")
		(date "2026-11-17")
		(rev "A")
		(company "Co")
		(comment 1 "github.com/x/y @ abc1234: benches/B")
		(comment 2 "desc")
		(comment 3 "Author: A")
	)
	(lib_symbols)
)
'''
TOML_PART = '[part]\ncategory = "{cat}"\npackage = "{pkg}"\n'


def sh(cwd, *cmd):
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                            GIT_COMMITTER_EMAIL="t@t"))


def write(p, text):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


class Env(unittest.TestCase):
    """A passing repo + zener-library fixture, mutated per test."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        t = Path(self._t.name)
        self.root, self.zl = t / "repo", t / "zl"
        zl = self.zl
        write(zl / "lib/__init__.py", "")
        write(zl / "components/Mfr/R0603/component.toml", TOML_PART.format(cat="resistor_thick_film", pkg="0603 (1608 metric)"))
        write(zl / "components/Mfr/IC/component.toml", TOML_PART.format(cat="mosfet", pkg="SO-8"))
        sh(zl, "git", "init", "-q")
        sh(zl, "git", "add", "-A")
        sh(zl, "git", "commit", "-qm", "a")
        sh(zl, "git", "tag", "components/Mfr/R0603/v0.1.0")
        sh(zl, "git", "tag", "components/Mfr/IC/v0.1.0")
        write(zl / "lib/x.py", "x")
        sh(zl, "git", "add", "-A")
        sh(zl, "git", "commit", "-qm", "b")
        sh(zl, "git", "tag", "v0.2.0")
        r = self.root
        write(r / "analysis/_zl.py", 'ZL_VERSION = "v0.2.0"\n')
        write(r / "pcb.toml", '[workspace]\npcb-version = "0.4"\n[dependencies]\n'
              '"github.com/kb1lqc/zener-library/components/Mfr/R0603" = "0.1.0"\n'
              '[dependencies.indirect]\n"github.com/kb1lqc/zener-library/components/Mfr/IC@0.1" = "0.1.0"\n')
        write(r / "benches/B/bench.zen", 'V3V3 = Power("V3V3", voltage="3.3V")\n')
        write(r / "benches/B/eda/B.kicad_sch", SCH)
        write(r / "tools/s.sh", 'pcb apply schematic --no-open x.zen\n# pcb apply in a comment\necho "pcb apply text"\n')

    def tearDown(self):
        self._t.cleanup()

    def run_env(self):
        return ce.run(self.root, self.zl)[0]

    def rules(self):
        return {f.rule for f in self.run_env()}

    def test_pass(self):
        self.assertEqual(self.run_env(), [])

    def test_pass_inside_git_hook_env(self):
        # a pre-commit hook exports GIT_DIR / GIT_INDEX_FILE for the committing repo; they must not leak into zl calls
        other = Path(self._t.name) / "other"
        other.mkdir()
        sh(other, "git", "init", "-q")
        keys = ("GIT_DIR", "GIT_INDEX_FILE")
        old = {k: os.environ.get(k) for k in keys}
        os.environ["GIT_DIR"] = str(other / ".git")
        os.environ["GIT_INDEX_FILE"] = str(other / ".git" / "index")
        try:
            self.assertEqual(self.run_env(), [])
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_pcb_version_patch_fails(self):
        write(self.root / "pcb.toml", (self.root / "pcb.toml").read_text().replace('"0.4"', '"0.4.95"'))
        self.assertIn("ENV-PCB-VERSION", self.rules())

    def test_nested_workspace_fails_even_hidden(self):
        write(self.root / "benches/B/.hidden/pcb.toml", "[workspace]\n")
        fs = [f for f in self.run_env() if f.rule == "ENV-NESTED-WORKSPACE"]
        self.assertEqual(len(fs), 1)

    def test_nested_empty_manifest_passes(self):
        write(self.root / "blocks/B/pcb.toml", '[dependencies]\n')
        self.assertEqual(self.run_env(), [])

    def test_zl_missing_fails(self):
        self.assertIn("ENV-ZL-CHECKOUT", {f.rule for f in ce.run(self.root, self.zl / "nope")[0]})

    def test_zl_dirty_fails_but_claude_untracked_ok(self):
        write(self.zl / ".claude/skills/a.md", "x")
        self.assertEqual(self.run_env(), [])
        write(self.zl / "lib/new.py", "x")
        self.assertIn("ENV-ZL-CHECKOUT", self.rules())

    def test_zl_wrong_tag_fails(self):
        write(self.root / "analysis/_zl.py", 'ZL_VERSION = "v0.1.0"\n')
        self.assertIn("ENV-ZL-CHECKOUT", self.rules())

    def test_dep_tag_missing_fails(self):
        write(self.root / "pcb.toml", (self.root / "pcb.toml").read_text().replace('R0603" = "0.1.0"', 'R0603" = "0.9.0"'))
        self.assertIn("ENV-DEP-TAG", self.rules())

    def test_dep_selector_mismatch_fails(self):
        write(self.root / "pcb.toml", (self.root / "pcb.toml").read_text().replace("IC@0.1", "IC@0.2"))
        self.assertIn("ENV-DEP-TAG", self.rules())

    def test_dep_released_after_zl_version_fails(self):
        write(self.zl / "lib/y.py", "y")
        sh(self.zl, "git", "add", "-A")
        sh(self.zl, "git", "commit", "-qm", "c")
        sh(self.zl, "git", "tag", "components/Mfr/IC/v0.2.0")
        sh(self.zl, "git", "tag", "-f", "v0.2.0", "HEAD~1")  # keep ZL_VERSION on the older commit
        sh(self.zl, "git", "tag", "v0.3.0")
        write(self.root / "pcb.toml", (self.root / "pcb.toml").read_text().replace("IC@0.1\" = \"0.1.0", "IC@0.2\" = \"0.2.0"))
        sh(self.zl, "git", "checkout", "-q", "v0.2.0")
        fs = [f for f in self.run_env() if f.rule == "ENV-DEP-TAG"]
        self.assertEqual(len(fs), 1)
        self.assertIn("not an ancestor", fs[0].message)

    def test_no_deps_fails_closed(self):
        write(self.root / "pcb.toml", '[workspace]\npcb-version = "0.4"\n')
        self.assertIn("EMPTY", self.rules())

    def test_lck_fails(self):
        write(self.root / "benches/B/eda/.~lock.x.lck", "")
        self.assertIn("ENV-LCK", self.rules())

    def test_apply_without_no_open_fails(self):
        write(self.root / "tools/s.sh", "pcb apply schematic x.zen\n")
        self.assertIn("ENV-APPLY-NO-OPEN", self.rules())

    def test_apply_python_argv(self):
        write(self.root / "tools/p.py", 'subprocess.run(["pcb", "apply", "schematic", "x.zen"])\n')
        self.assertIn("ENV-APPLY-NO-OPEN", self.rules())
        write(self.root / "tools/p.py", 'subprocess.run(["pcb", "apply", "schematic", "--no-open", "x.zen"])\n')
        self.assertEqual(self.run_env(), [])

    def test_rail_names(self):
        for bad in ("3V3", "VBUS", "V_RAIL"):
            write(self.root / "benches/B/bench.zen", f'R = Power("{bad}", voltage="3V")\n')
            self.assertIn("NAME-RAIL", self.rules(), bad)
        write(self.root / "benches/B/bench.zen", 'R = Power("V12", voltage="12V")  # Power("3V3")\n')
        self.assertEqual(self.run_env(), [])

    def test_title_block_empty_field_fails(self):
        write(self.root / "benches/B/eda/B.kicad_sch", SCH.replace('(company "Co")', '(company "")'))
        fs = [f for f in self.run_env() if f.rule == "TITLE-BLOCK"]
        self.assertEqual([f.location for f in fs], ["company"])

    def test_title_block_missing_comment_fails(self):
        write(self.root / "benches/B/eda/B.kicad_sch", SCH.replace('(comment 3 "Author: A")', ""))
        self.assertIn("TITLE-BLOCK", self.rules())

    def test_no_schematics_fails_closed(self):
        (self.root / "benches/B/eda/B.kicad_sch").unlink()
        self.assertIn("EMPTY", self.rules())

    def test_passive_0402_fails(self):
        write(self.zl / "components/Mfr/R0402/component.toml", TOML_PART.format(cat="resistor_thick_film", pkg="0402 (1005 metric)"))
        sh(self.zl, "git", "add", "-A")
        sh(self.zl, "git", "commit", "-qm", "r")
        sh(self.zl, "git", "tag", "components/Mfr/R0402/v0.1.0")
        sh(self.zl, "git", "tag", "-f", "v0.2.0")
        write(self.root / "pcb.toml", (self.root / "pcb.toml").read_text().replace(
            '[dependencies.indirect]', '"github.com/kb1lqc/zener-library/components/Mfr/R0402" = "0.1.0"\n[dependencies.indirect]'))
        fs = [f for f in self.run_env() if f.rule == "PASSIVE-SIZE"]
        self.assertEqual(len(fs), 1)

    def test_passive_metric_first_form_ok(self):
        write(self.zl / "components/Mfr/C/component.toml", TOML_PART.format(cat="capacitor_mlcc", pkg="1608M (0603)"))
        sh(self.zl, "git", "add", "-A")
        sh(self.zl, "git", "commit", "-qm", "c")
        sh(self.zl, "git", "tag", "components/Mfr/C/v0.1.0")
        sh(self.zl, "git", "tag", "-f", "v0.2.0")
        write(self.root / "pcb.toml", (self.root / "pcb.toml").read_text().replace(
            '[dependencies.indirect]', '"github.com/kb1lqc/zener-library/components/Mfr/C" = "0.1.0"\n[dependencies.indirect]'))
        self.assertEqual(self.run_env(), [])


class Allowlist(unittest.TestCase):
    F = fd.Finding("R1", "a.zen", "k", "m")

    def test_exact_match_allows(self):
        v, a, s = fd.apply_allowlist([self.F], [{"rule": "R1", "file": "a.zen", "location": "k"}], {"R1"})
        self.assertEqual((v, len(a), s), ([], 1, []))

    def test_non_match_is_violation_and_entry_stale(self):
        v, a, s = fd.apply_allowlist([self.F], [{"rule": "R1", "file": "a.zen", "location": "other"}], {"R1"})
        self.assertEqual((len(v), len(a), len(s)), (1, 0, 1))

    def test_other_tools_entries_ignored(self):
        v, a, s = fd.apply_allowlist([], [{"rule": "R9", "file": "x", "location": "y"}], {"R1"})
        self.assertEqual(s, [])

    def test_zero_checked_fails(self):
        self.assertEqual(fd.report("t", [], [], {"R1"}, 0), 1)
        self.assertEqual(fd.report("t", [], [], {"R1"}, 3), 0)


if __name__ == "__main__":
    unittest.main()
