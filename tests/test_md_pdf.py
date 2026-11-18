"""Tests for analysis/md_pdf.py: a rerun that changes no result rewrites no report file."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analysis import md_pdf  # noqa: E402

REPORT = "# B design analysis\n\n- Results date: {d}.\n\nvalue {v}\n"


class WritePdf(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.md = Path(self._t.name) / "docs" / "design-analysis.md"
        self.md.parent.mkdir(parents=True)
        self.md.write_text(REPORT.format(d="2026-11-17", v=1))
        self.pdf = self.md.parent / md_pdf.PDF_NAME
        self._chrome = os.environ.get("CHROME")
        os.environ["CHROME"] = str(Path(self._t.name) / "no-chrome")  # any real render attempt raises

    def tearDown(self):
        if self._chrome is None:
            os.environ.pop("CHROME", None)
        else:
            os.environ["CHROME"] = self._chrome
        self._t.cleanup()

    def test_up_to_date_pdf_is_not_rerendered(self):
        self.pdf.write_bytes(b"%PDF old bytes")
        with mock.patch.object(md_pdf, "_pdf_stamp", return_value=md_pdf.source_digest(self.md)):
            self.assertEqual(md_pdf.write_pdf(self.md), self.pdf.resolve())
        self.assertEqual(self.pdf.read_bytes(), b"%PDF old bytes")

    def test_image_change_makes_pdf_stale(self):
        # the .md stays byte-identical; only the embedded chart changes, so the digest (and the PDF) must change
        png = self.md.parent / "chart.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n first chart")
        self.md.write_text(REPORT.format(d="2026-11-17", v=1) + "\n![chart](chart.png)\n")
        before = md_pdf.source_digest(self.md)
        png.write_bytes(b"\x89PNG\r\n\x1a\n second chart")
        self.assertNotEqual(md_pdf.source_digest(self.md), before)
        self.pdf.write_bytes(b"%PDF old bytes")
        with mock.patch.object(md_pdf, "_pdf_stamp", return_value=before):
            with self.assertRaisesRegex(RuntimeError, "Chrome not found"):
                md_pdf.write_pdf(self.md)

    def test_css_change_makes_pdf_stale(self):
        before = md_pdf.source_digest(self.md)
        with mock.patch.object(md_pdf, "CSS", md_pdf.CSS + "p { color: red; }"):
            self.assertNotEqual(md_pdf.source_digest(self.md), before)

    def test_stale_pdf_is_rerendered(self):
        self.pdf.write_bytes(b"%PDF old bytes")
        with mock.patch.object(md_pdf, "_pdf_stamp", return_value="0" * 16):
            with self.assertRaisesRegex(RuntimeError, "Chrome not found"):
                md_pdf.write_pdf(self.md)

    def test_missing_pdf_is_rendered(self):
        with self.assertRaisesRegex(RuntimeError, "Chrome not found"):
            md_pdf.write_pdf(self.md)


class WriteReport(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.md = Path(self._t.name) / "design-analysis.md"
        self.render = mock.patch.object(md_pdf, "write_pdf", return_value="pdf").start()

    def tearDown(self):
        mock.patch.stopall()
        self._t.cleanup()

    def test_new_report_is_written(self):
        md_pdf.write_report(self.md, REPORT.format(d="2026-11-18", v=1))
        self.assertEqual(self.md.read_text(), REPORT.format(d="2026-11-18", v=1))
        self.render.assert_called_once()

    def test_only_date_changed_keeps_file(self):
        self.md.write_text(REPORT.format(d="2026-11-17", v=1))
        md_pdf.write_report(self.md, REPORT.format(d="2026-11-18", v=1))
        self.assertEqual(self.md.read_text(), REPORT.format(d="2026-11-17", v=1))

    def test_result_changed_rewrites_with_new_date(self):
        self.md.write_text(REPORT.format(d="2026-11-17", v=1))
        md_pdf.write_report(self.md, REPORT.format(d="2026-11-18", v=2))
        self.assertEqual(self.md.read_text(), REPORT.format(d="2026-11-18", v=2))

    def test_other_date_like_text_is_not_masked(self):
        old = "# B\n\n- Results date: 2026-11-17.\n- Run on 2026-11-17\n"
        new = "# B\n\n- Results date: 2026-11-18.\n- Run on 2026-11-18\n"
        self.md.write_text(old)
        md_pdf.write_report(self.md, new)
        self.assertEqual(self.md.read_text(), new)


if __name__ == "__main__":
    unittest.main()
