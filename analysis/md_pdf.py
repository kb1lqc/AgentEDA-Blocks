"""Render a generated design-analysis Markdown report to PDF (stdlib + headless Chrome).

Supported Markdown subset (anything else raises with the line number, nothing is dropped):
  # / ## / ### headings, paragraphs, GFM pipe tables (alignment colons honoured), "- " bullets,
  "> " blockquotes, ![alt](path) images (embedded as base64 data URIs), **bold**, *italic*, `code`.

API:  write_pdf(md_path) -> pdf_path      writes docs/design-analysis.pdf next to the .md
CLI:  python3 -m analysis.md_pdf <md>...          write the PDFs
      python3 -m analysis.md_pdf --check <md>...  exit 1 if any PDF is missing or stale

Staleness stamp: the HTML <title> (Chrome writes it as the PDF /Title) is
"<bench folder name> design analysis md-sha256:<first 16 hex of sha256 of the .md bytes>".
--check compares that stamp with the current .md.
Chrome: $CHROME, else the default macOS install path. Missing Chrome or a missing PDF raises.
"""

import argparse
import base64
import hashlib
import html
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CHROME_DEFAULT = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PDF_NAME = "design-analysis.pdf"
STAMP_RE = re.compile(r"md-sha256:([0-9a-f]{16})\s*$")
IMAGE_TYPES = {"image/png", "image/jpeg", "image/svg+xml", "image/gif"}

CSS = """
@page { size: Letter portrait; margin: 0.6in; }
html, body { background: #fff; color: #111; }
body { font-family: -apple-system, "Helvetica Neue", Arial, sans-serif; font-size: 9.5pt; line-height: 1.35; margin: 0; }
h1 { font-size: 15pt; margin: 0 0 6pt; }
h2 { font-size: 12pt; margin: 12pt 0 4pt; padding-bottom: 2pt; border-bottom: 0.5pt solid #888; break-after: avoid; }
h3 { font-size: 10.5pt; margin: 9pt 0 3pt; break-after: avoid; }
p { margin: 3pt 0 5pt; }
ul { margin: 3pt 0 5pt; padding-left: 16pt; }
li { margin: 1pt 0; }
blockquote { margin: 5pt 0; padding: 3pt 8pt; border-left: 2pt solid #999; background: #f3f3f3; color: #333; }
blockquote p { margin: 0; }
table { width: 100%; border-collapse: collapse; margin: 4pt 0 8pt; font-size: 7.8pt; }
thead { display: table-header-group; }
th, td { border: 0.5pt solid #777; padding: 2pt 3pt; vertical-align: top; overflow-wrap: anywhere; }
th { background: #e6e6e6; text-align: left; }
tr { page-break-inside: avoid; break-inside: avoid; }
figure { margin: 6pt 0; text-align: center; page-break-inside: avoid; break-inside: avoid; }
img { max-width: 100%; height: auto; }
code { font-family: Menlo, Consolas, monospace; font-size: 0.9em; background: #f0f0f0; padding: 0 2pt; overflow-wrap: anywhere; }
"""


class MdError(ValueError):
    """The Markdown uses a construct outside the supported subset."""


def _starts_block(line):
    """True if the line starts a heading, quote, image, bullet or fence (not a table row)."""
    return line.startswith(("#", "> ", ">", "![", "- ", "```", "~~~"))


def _is_special(line):
    """True if the line starts a block other than a paragraph (table rows included)."""
    return _starts_block(line) or line.startswith("|")


def _inline(text, lineno):
    out = []
    for i, part in enumerate(re.split(r"(`[^`]*`)", text)):
        if i % 2 == 1:  # code span: verbatim, so <key> and similar are fine inside it
            out.append("<code>" + html.escape(part[1:-1], quote=False) + "</code>")
            continue
        if re.search(r"<[A-Za-z/!]", part):
            raise MdError(f"line {lineno}: raw HTML is not supported: {text.strip()!r}")
        if "](" in part:
            raise MdError(f"line {lineno}: links are not supported: {text.strip()!r}")
        s = html.escape(part, quote=False)
        s = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])", r"<em>\1</em>", s)
        if "*" in s or "`" in s:
            raise MdError(f"line {lineno}: unmatched emphasis or backtick: {text.strip()!r}")
        out.append(s)
    return "".join(out)


def _split_row(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s)]


_SEP_CELL = re.compile(r"^:?-+:?$")


def _table(lines, i, out):
    lineno = i + 1
    if i + 1 >= len(lines):
        raise MdError(f"line {lineno}: table without a separator row")
    head = _split_row(lines[i])
    sep = _split_row(lines[i + 1])
    if len(sep) != len(head) or not all(_SEP_CELL.match(c) for c in sep):
        raise MdError(f"line {lineno}: table header is not followed by a matching separator row")
    align = []
    for c in sep:
        left, right = c.startswith(":"), c.endswith(":")
        align.append("center" if left and right else "right" if right else "left" if left else "")
    rows = []
    i += 2
    while i < len(lines) and lines[i].strip() and "|" in lines[i] and not _starts_block(lines[i]):
        cells = _split_row(lines[i])
        if len(cells) != len(head):
            raise MdError(f"line {i + 1}: table row has {len(cells)} cells, header has {len(head)}")
        rows.append((i + 1, cells))
        i += 1

    def cell(tag, text, a, ln):
        style = f' style="text-align:{a}"' if a else ""
        return f"<{tag}{style}>{_inline(text, ln)}</{tag}>"

    parts = ["<table><thead><tr>"]
    parts += [cell("th", c, a, lineno) for c, a in zip(head, align)]
    parts.append("</tr></thead><tbody>")
    for ln, cells in rows:
        parts.append("<tr>" + "".join(cell("td", c, a, ln) for c, a in zip(cells, align)) + "</tr>")
    parts.append("</tbody></table>")
    out.append("".join(parts))
    return i


def md_to_body(md_text, base_dir):
    """Convert the supported Markdown subset to an HTML fragment. base_dir resolves image paths."""
    lines = md_text.splitlines()
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        lineno = i + 1
        if not line.strip():
            i += 1
            continue
        if line[0] in " \t":
            raise MdError(f"line {lineno}: indented line (nested list or code block) is not supported: {line.strip()!r}")
        if line.startswith(("```", "~~~")):
            raise MdError(f"line {lineno}: code fences are not supported")
        if re.fullmatch(r"([-*_])( *\1){2,} *", line.strip()):
            raise MdError(f"line {lineno}: horizontal rules are not supported")
        if re.match(r"\d+[.)] ", line) or re.match(r"[*+] ", line):
            raise MdError(f"line {lineno}: only '- ' bullets and no ordered lists are supported: {line.strip()!r}")
        m = re.match(r"(#{1,6}) (.+)$", line)
        if m:
            level = len(m.group(1))
            if level > 3:
                raise MdError(f"line {lineno}: only #, ## and ### headings are supported")
            out.append(f"<h{level}>{_inline(m.group(2).strip(), lineno)}</h{level}>")
            i += 1
        elif line.startswith("|"):
            i = _table(lines, i, out)
        elif line.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].startswith(">"):
                buf.append(lines[i][2:] if lines[i].startswith("> ") else lines[i][1:])
                i += 1
            out.append(f"<blockquote><p>{_inline(' '.join(b.strip() for b in buf if b.strip()), lineno)}</p></blockquote>")
        elif line.startswith("!["):
            m = re.fullmatch(r"!\[([^\]]*)\]\(([^)\s]+)\)", line.strip())
            if not m:
                raise MdError(f"line {lineno}: only ![alt](path) images are supported: {line.strip()!r}")
            src = (base_dir / m.group(2)).resolve()
            mime = mimetypes.guess_type(src.name)[0]
            if not src.is_file() or mime not in IMAGE_TYPES:
                raise MdError(f"line {lineno}: image missing or not a supported image: {m.group(2)}")
            data = base64.b64encode(src.read_bytes()).decode("ascii")
            out.append(f'<figure><img src="data:{mime};base64,{data}" alt="{html.escape(m.group(1), quote=True)}"></figure>')
            i += 1
        elif line.startswith("- "):
            items = []
            while i < len(lines) and lines[i].startswith("- "):
                item_line = i + 1
                item = [lines[i][2:].strip()]
                i += 1
                # lazy continuation: plain text right after an item belongs to it (GFM)
                while i < len(lines) and lines[i].strip() and not _is_special(lines[i]):
                    if lines[i][0] in " \t":
                        raise MdError(f"line {i + 1}: nested list or indented continuation is not supported")
                    item.append(lines[i].strip())
                    i += 1
                items.append((item_line, " ".join(item)))
            out.append("<ul>" + "".join(f"<li>{_inline(t, ln)}</li>" for ln, t in items) + "</ul>")
        else:
            if _is_special(line):
                raise MdError(f"line {lineno}: unsupported block syntax: {line.strip()!r}")
            buf = []
            while i < len(lines) and lines[i].strip() and not _is_special(lines[i]):
                if lines[i][0] in " \t":
                    raise MdError(f"line {i + 1}: indented line is not supported: {lines[i].strip()!r}")
                buf.append(lines[i].strip())
                i += 1
            out.append(f"<p>{_inline(' '.join(buf), lineno)}</p>")
    return "\n".join(out)


def _html_doc(title, body):
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">\n"
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(title, quote=False)}</title>\n<style>{CSS}</style>\n</head>\n"
        f"<body>\n{body}\n</body></html>\n"
    )


def md_digest(md_path):
    return hashlib.sha256(Path(md_path).read_bytes()).hexdigest()[:16]


def _pdf_stamp(pdf_path):
    """The md-sha256 stamp in the PDF /Title (via pdfinfo), or None."""
    try:
        res = subprocess.run(["pdfinfo", str(pdf_path)], capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        raise RuntimeError("pdfinfo not found (poppler); needed to read the PDF /Title stamp")
    if res.returncode != 0:
        raise RuntimeError(f"pdfinfo failed on {pdf_path}: {res.stderr.strip()}")
    for line in res.stdout.splitlines():
        if line.startswith("Title:"):
            m = STAMP_RE.search(line.split(":", 1)[1].strip())
            return m.group(1) if m else None
    return None


def write_pdf(md_path):
    """Render md_path to docs/design-analysis.pdf next to it and return that path. Raises on any failure."""
    md = Path(md_path).resolve()
    digest = md_digest(md)
    bench = md.parent.parent.name
    title = f"{bench} design analysis md-sha256:{digest}"
    body = md_to_body(md.read_text(encoding="utf-8"), md.parent)
    chrome = os.environ.get("CHROME") or CHROME_DEFAULT
    if not Path(chrome).is_file():
        raise RuntimeError(f"Chrome not found at {chrome} (set CHROME to its path)")
    pdf = md.parent / PDF_NAME
    with tempfile.TemporaryDirectory(prefix="md_pdf_") as tmp:
        html_path = Path(tmp) / "design-analysis.html"
        html_path.write_text(_html_doc(title, body), encoding="utf-8")
        out = Path(tmp) / PDF_NAME
        res = subprocess.run(
            [chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
             f"--print-to-pdf={out}", html_path.as_uri()],
            capture_output=True, text=True, timeout=300)
        if res.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
            raise RuntimeError(f"Chrome did not produce {pdf} (exit {res.returncode}): {res.stderr.strip()[-800:]}")
        if _pdf_stamp(out) != digest:
            raise RuntimeError(f"PDF /Title stamp is not md-sha256:{digest}; the staleness check would be wrong")
        shutil.move(str(out), str(pdf))
    return pdf


def check(md_paths):
    """Print one line per md/PDF pair; return the number of missing or stale PDFs."""
    bad = 0
    for m in md_paths:
        md = Path(m)
        pdf = md.parent / PDF_NAME
        block = md.resolve().parent.parent.name
        if pdf.is_file() and _pdf_stamp(pdf) == md_digest(md):
            print(f"{pdf}: up to date")
        else:
            print(f"{pdf}: STALE or missing (run python3 blocks/{block}/analysis.py)")
            bad += 1
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description="Render design-analysis.md to design-analysis.pdf, or check the PDFs are current.")
    ap.add_argument("--check", action="store_true", help="do not write; exit 1 if any PDF is missing or stale")
    ap.add_argument("md", nargs="+", help="design-analysis.md file(s)")
    args = ap.parse_args(argv)
    if args.check:
        return 1 if check(args.md) else 0
    for m in args.md:
        print(write_pdf(m))
    return 0


if __name__ == "__main__":
    sys.exit(main())
