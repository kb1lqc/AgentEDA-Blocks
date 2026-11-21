"""Tests for analysis/_export.py: library-inputs.json rows from lib.used_inputs() labels."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analysis import _export  # noqa: E402


def write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


class Export(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.zl = Path(self._t.name)
        write(self.zl / "components/Acme/X/component.toml", "")
        write(self.zl / "components/Murata/GCM188/component.toml", "")
        write(self.zl / "components/ADI/FAM/component.toml", "")
        write(self.zl / "components/ADI/FAM/variants.toml", '[variant.FAM-1]\nmpn = "a"\n[variant.FAM-2]\nmpn = "b"\n')
        write(self.zl / "components/A/DUP/component.toml", "")
        write(self.zl / "components/B/DUP/component.toml", "")

    def tearDown(self):
        self._t.cleanup()

    def test_rows_sorted_with_dirs(self):
        rows = _export.library_inputs(["X:vos", "GCM188[GCM188R71H104KA57]:tc_x7r", "FAM-1:vth", "DUP:a", "Gone:b",
                                       "X:iq"], zl=self.zl)
        self.assertEqual(rows, [
            {"value": "DUP", "key": "a", "input": "DUP:a"},  # two dirs of that name: no dir
            {"value": "FAM", "key": "vth", "input": "FAM-1:vth", "dir": "components/ADI/FAM"},  # variant -> family
            {"value": "GCM188", "key": "tc_x7r", "input": "GCM188[GCM188R71H104KA57]:tc_x7r",
             "dir": "components/Murata/GCM188"},
            {"value": "Gone", "key": "b", "input": "Gone:b"},
            {"value": "X", "key": "iq", "input": "X:iq", "dir": "components/Acme/X"},
            {"value": "X", "key": "vos", "input": "X:vos", "dir": "components/Acme/X"}])

    def test_write_is_deterministic(self):
        from unittest import mock
        with mock.patch("lib.used_inputs", return_value=["X:vos"], create=True), \
                mock.patch.object(_export, "ZL", self.zl), \
                mock.patch.object(_export.library_inputs, "__defaults__", (self.zl,)):
            p = _export.write_library_inputs(self.zl)
            first = p.read_bytes()
            _export.write_library_inputs(self.zl)
        self.assertEqual(p.read_bytes(), first)
        self.assertEqual(json.loads(first), [{"value": "X", "key": "vos", "input": "X:vos", "dir": "components/Acme/X"}])

    def test_both_analyses_write_it(self):
        root = Path(__file__).resolve().parent.parent
        for a in sorted(root.glob("blocks/*/analysis.py")):
            self.assertIn("write_library_inputs(FIG_DIR)", a.read_text(), a)


if __name__ == "__main__":
    unittest.main()
