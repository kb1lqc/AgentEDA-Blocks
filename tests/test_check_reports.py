"""Tests for tools/check_reports.py with fixture repos (a fake analysis.py writes the bench report)."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import check_reports as cr  # noqa: E402

ANALYSIS = '''import json, sys
from pathlib import Path
root = Path(__file__).resolve().parent.parent.parent
d = root / "benches" / "B" / "docs"
(d / "design-analysis").mkdir(parents=True, exist_ok=True)
(d / "design-analysis.md").write_text("# report\\nvalue 1\\n")
(d / "design-analysis" / "values.json").write_text(json.dumps({"a": 1}))
(d / "design-analysis" / "library-inputs.json").write_text("[]\\n")
'''


def write(p, text):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


class Reports(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = Path(self._t.name) / "repo"
        r = self.root
        write(r / "benches/B/bench.zen", "")
        write(r / "blocks/B/analysis.py", ANALYSIS)
        write(r / "benches/B/docs/design-analysis.md", "# report\nvalue 1\n")
        write(r / "benches/B/docs/design-analysis/values.json", '{"a": 1}')
        write(r / "benches/B/docs/design-analysis/library-inputs.json", "[]\n")

    def tearDown(self):
        self._t.cleanup()

    def rules(self):
        fs, n = cr.run(self.root, self.root)
        return {f.rule for f in fs}, n

    def test_fresh_passes(self):
        self.assertEqual(self.rules(), (set(), 1))

    def test_stale_md_fails(self):
        write(self.root / "benches/B/docs/design-analysis.md", "# report\nvalue 2\n")
        self.assertEqual(self.rules()[0], {"REP-STALE"})

    def test_results_date_only_difference_passes(self):
        write(self.root / "blocks/B/analysis.py", ANALYSIS.replace("# report\\n", "# report\\n- Results date: 2026-11-18.\\n"))
        write(self.root / "benches/B/docs/design-analysis.md", "# report\n- Results date: 2026-11-17.\nvalue 1\n")
        self.assertEqual(self.rules(), (set(), 1))

    def test_results_date_plus_value_difference_fails(self):
        write(self.root / "blocks/B/analysis.py", ANALYSIS.replace("# report\\n", "# report\\n- Results date: 2026-11-18.\\n"))
        write(self.root / "benches/B/docs/design-analysis.md", "# report\n- Results date: 2026-11-17.\nvalue 2\n")
        self.assertEqual(self.rules()[0], {"REP-STALE"})

    def test_stale_values_fails(self):
        write(self.root / "benches/B/docs/design-analysis/values.json", '{"a": 2}')
        self.assertEqual(self.rules()[0], {"REP-STALE"})

    def test_stale_or_missing_library_inputs_fails(self):
        p = self.root / "benches/B/docs/design-analysis/library-inputs.json"
        write(p, '[{"value": "X", "key": "k", "input": "X:k"}]\n')
        self.assertEqual(self.rules()[0], {"REP-STALE"})
        p.unlink()
        self.assertEqual(self.rules()[0], {"REP-STALE"})

    def test_check_never_writes_into_repo(self):
        write(self.root / "benches/B/docs/design-analysis.md", "old\n")
        self.rules()
        self.assertEqual((self.root / "benches/B/docs/design-analysis.md").read_text(), "old\n")

    def test_zero_benches_fails(self):
        (self.root / "benches/B/bench.zen").unlink()
        self.assertEqual(self.rules(), ({"REP-NO-BENCHES"}, 0))

    def test_missing_values_fails(self):
        (self.root / "benches/B/docs/design-analysis/values.json").unlink()
        self.assertIn("REP-VALUES", self.rules()[0])

    def test_empty_values_fails(self):
        write(self.root / "benches/B/docs/design-analysis/values.json", "{}")
        self.assertIn("REP-VALUES", self.rules()[0])

    def test_invalid_values_fails(self):
        write(self.root / "benches/B/docs/design-analysis/values.json", "")
        self.assertIn("REP-VALUES", self.rules()[0])

    def test_missing_analysis_script_fails(self):
        (self.root / "blocks/B/analysis.py").unlink()
        self.assertIn("REP-GENERATE", self.rules()[0])

    def test_failing_script_fails(self):
        write(self.root / "blocks/B/analysis.py", "raise SystemExit(3)\n")
        self.assertIn("REP-GENERATE", self.rules()[0])

    def test_script_writing_nothing_cannot_pass(self):
        write(self.root / "blocks/B/analysis.py", "pass\n")
        self.assertIn("REP-STALE", self.rules()[0])


if __name__ == "__main__":
    unittest.main()
