#!/usr/bin/env python3
"""Fast environment and naming checks for circuit-blocks (no `pcb build`). Exit 0 = pass, 1 = any violation.

Usage (repo root): python3 tools/check_env.py [--root DIR] [--zl DIR]

Rules (ids are used in tools/known_findings.toml):
  ENV-PCB-VERSION      root pcb.toml has [workspace] and pcb-version "<major>.<minor>" only (a patch version breaks builds)
  ENV-NESTED-WORKSPACE no pcb.toml other than the root has [workspace]; the whole tree is walked (hidden and git-ignored
                       folders, .pcb/ included; only .git is skipped, it holds no manifests of this repo)
  ENV-ZL-CHECKOUT      the zener-library checkout ($ZENER_LIBRARY or ../zener-library, as analysis/_zl.py) exists, has no
                       tracked changes and no untracked files outside .claude/, and HEAD is tagged ZL_VERSION
  ENV-DEP-TAG          every github.com/kb1lqc/zener-library/components/<Mfr>/<Value> dependency in every pcb.toml (direct
                       and [dependencies.indirect]) has tag components/<Mfr>/<Value>/v<version> in the checkout, and that
                       commit is an ancestor of the ZL_VERSION tag. Key "<url>@0.2" = "0.2.0": the "@0.2" is the resolver's
                       major.minor selector and is stripped; the value is the exact version, tag = "v" + value. The
                       selector must be a prefix of the value.
  ENV-LCK              no *.lck file under benches/
  ENV-APPLY-NO-OPEN    every `pcb apply` command in tools/ and benches/ scripts carries --no-open (shell scripts: command
                       lines, comments and echo/print text skipped; Python: lines holding the argv word "apply")
  NAME-RAIL            Power("<name>") in benches/*/bench.zen and blocks/*/*.zen: name has no leading digit and contains a
                       digit (the voltage, V3V3)
  TITLE-BLOCK          every benches/*/eda/*.kicad_sch has title, date, rev, company and comments 1-3 (repo URL + hash,
                       description, author: the repo convention) all non-empty
  PASSIVE-SIZE         every passive in the dependencies (component.toml [part] category capacitor*/resistor*/
                       current_sense_resistor, tag as above) has a package whose imperial size code is 0603 or larger
Not checked here: active-low `_N` suffix (needs intent), 100 nF per power pin (needs adjacency), geometry (tools/verify.py).
"""

import argparse
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _findings import Finding, load_allowlist, report  # noqa: E402

RULES = {"ENV-PCB-VERSION", "ENV-NESTED-WORKSPACE", "ENV-ZL-CHECKOUT", "ENV-DEP-TAG", "ENV-LCK", "ENV-APPLY-NO-OPEN",
         "NAME-RAIL", "TITLE-BLOCK", "PASSIVE-SIZE"}
ZL_URL = "github.com/kb1lqc/zener-library"
ZL_PREFIX = ZL_URL + "/components/"


def git(zl, *args):
    # Drop GIT_* from the environment: inside a git hook (pre-commit) git exports GIT_DIR / GIT_INDEX_FILE for THIS
    # repository, and `git -C <zl>` would then read this repository's index and tags instead of the library's.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", "-C", str(zl), *args], capture_output=True, text=True, env=env)


def rel(root, p):
    return str(Path(p).relative_to(root))


def read_zl_version(root):
    p = root / "analysis" / "_zl.py"
    m = re.search(r'^ZL_VERSION\s*=\s*"([^"]+)"', p.read_text(), re.M) if p.is_file() else None
    return m.group(1) if m else None


def default_zl(root):
    return Path(os.environ.get("ZENER_LIBRARY", root.parent / "zener-library")).resolve()


def manifests(root):
    """Every pcb.toml under root, .git skipped (.pcb/ and other hidden folders included)."""
    out = []
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x != ".git"]
        if "pcb.toml" in files:
            out.append(Path(d) / "pcb.toml")
    return sorted(out)


def check_manifests(root):
    f, n = [], 0
    rootm = root / "pcb.toml"
    for m in manifests(root):
        n += 1
        data = tomllib.loads(m.read_text())
        r = rel(root, m)
        if m == rootm:
            ws = data.get("workspace")
            v = ws.get("pcb-version") if isinstance(ws, dict) else None
            if ws is None:
                f.append(Finding("ENV-PCB-VERSION", r, "[workspace]", "root pcb.toml has no [workspace]"))
            elif not isinstance(v, str) or not re.fullmatch(r"\d+\.\d+", v):
                f.append(Finding("ENV-PCB-VERSION", r, "pcb-version", f"is {v!r}; must be major.minor only, e.g. \"0.4\""))
        elif "workspace" in data:
            f.append(Finding("ENV-NESTED-WORKSPACE", r, "[workspace]", "nested [workspace] makes pcb fail; remove it"))
    if not rootm.is_file():
        f.append(Finding("ENV-PCB-VERSION", "pcb.toml", "", "root pcb.toml missing"))
    return f, n


def check_zl(root, zl, version):
    f = []
    if version is None:
        return [Finding("ENV-ZL-CHECKOUT", "analysis/_zl.py", "ZL_VERSION", "ZL_VERSION not found")]
    if not (Path(zl) / "lib" / "__init__.py").is_file() or git(zl, "rev-parse", "--git-dir").returncode:
        return [Finding("ENV-ZL-CHECKOUT", str(zl), "", f"no zener-library git checkout at {zl} (set ZENER_LIBRARY or clone beside the repo)")]
    tags = git(zl, "tag", "--points-at", "HEAD").stdout.split()
    if version not in tags:
        f.append(Finding("ENV-ZL-CHECKOUT", str(zl), "HEAD", f"HEAD is not tagged {version} (tags at HEAD: {tags or 'none'})"))
    for line in git(zl, "status", "--porcelain").stdout.splitlines():
        path = line[3:]
        if line.startswith("??") and path.startswith(".claude/"):
            continue  # untracked agent docs; no effect on lib/ or components/
        f.append(Finding("ENV-ZL-CHECKOUT", str(zl), path, f"checkout is not clean ({line.strip()})"))
    return f


def parse_deps(root):
    """[(manifest, key, url, selector, version)] for every zener-library dependency in every non-.pcb pcb.toml."""
    out = []
    for m in manifests(root):
        if ".pcb" in m.relative_to(root).parts:
            continue
        data = tomllib.loads(m.read_text())
        deps = dict(data.get("dependencies", {}))
        indirect = deps.pop("indirect", {})
        for key, val in [*deps.items(), *indirect.items()]:
            if key.startswith(ZL_URL):
                url, _, sel = key.partition("@")
                ver = val.get("version") if isinstance(val, dict) else val
                out.append((rel(root, m), key, url, sel, ver))
    return out


def dep_tag(url, ver):
    return f"components/{url[len(ZL_PREFIX):]}/v{ver}"


def check_deps(root, zl, version):
    f, deps = [], parse_deps(root)
    if not deps:
        return [], 0, deps
    if not (Path(zl) / ".git").exists():
        return [Finding("ENV-DEP-TAG", "", "", "no zener-library checkout to resolve tags in")], len(deps), deps
    top = git(zl, "rev-parse", "--verify", "-q", f"refs/tags/{version}^{{commit}}").stdout.strip()
    for manifest, key, url, sel, ver in deps:
        if not url.startswith(ZL_PREFIX) or not isinstance(ver, str):
            f.append(Finding("ENV-DEP-TAG", manifest, key, "not a components/<Mfr>/<Value> dependency with a version string"))
            continue
        if sel and not (ver == sel or ver.startswith(sel + ".")):
            f.append(Finding("ENV-DEP-TAG", manifest, key, f"selector @{sel} is not a prefix of version {ver}"))
        tag = dep_tag(url, ver)
        c = git(zl, "rev-parse", "--verify", "-q", f"refs/tags/{tag}^{{commit}}").stdout.strip()
        if not c:
            f.append(Finding("ENV-DEP-TAG", manifest, key, f"tag {tag} does not exist in the checkout"))
        elif not top:
            f.append(Finding("ENV-DEP-TAG", manifest, key, f"ZL_VERSION tag {version} does not exist"))
        elif git(zl, "merge-base", "--is-ancestor", c, top).returncode != 0:
            f.append(Finding("ENV-DEP-TAG", manifest, key, f"{tag} is not an ancestor of {version} (released after it)"))
    return f, len(deps), deps


def check_lck(root):
    f = [Finding("ENV-LCK", rel(root, p), "", "stale KiCad lock file; close KiCad and delete it")
         for p in sorted((root / "benches").rglob("*.lck"))]
    return f


APPLY_SH = re.compile(r"(^|[\s$(;&|])pcb\s+apply\b")


def check_apply(root):
    f, n = [], 0
    files = [p for d in ("tools", "benches") if (root / d).is_dir() for p in sorted((root / d).rglob("*"))
             if p.is_file() and (p.suffix in (".sh", ".py") or p.parent.name == "git")]
    for p in files:
        try:
            lines = p.read_text().splitlines()
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(lines, 1):
            s = line.strip()
            if p.suffix == ".py":
                hit = re.search(r"""["']apply["']""", s) and re.search(r"""["']pcb["']""", "\n".join(lines[max(0, i - 4):i]))
            else:
                hit = APPLY_SH.search(s) and not s.startswith("#") and not re.match(r"(echo|print)\b", s) \
                    and not re.search(r"\becho\b", s.split("pcb")[0])
            if hit:
                n += 1
                # a command may continue on the next lines (python list / shell backslash): look at a short window
                window = " ".join(lines[i - 1:i + 3]) if p.suffix == ".py" else s
                if "--no-open" not in window:
                    f.append(Finding("ENV-APPLY-NO-OPEN", rel(root, p), f"line {i}", "`pcb apply` without --no-open"))
    return f, n


def zen_files(root):
    return sorted(list((root / "benches").glob("*/bench.zen")) + list((root / "blocks").glob("*/*.zen")))


def check_rails(root):
    f, n = [], 0
    files = zen_files(root)
    for p in files:
        for i, line in enumerate(p.read_text().splitlines(), 1):
            code = line.split("#")[0]
            for m in re.finditer(r"""\bPower\(\s*(?:name\s*=\s*)?["']([^"']*)["']""", code):
                n += 1
                name = m.group(1)
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or not re.search(r"\d", name):
                    f.append(Finding("NAME-RAIL", rel(root, p), name, f"line {i}: rail name must carry its voltage (V3V3) and not start with a digit"))
    if not files:
        f.append(Finding("EMPTY", "benches/*/bench.zen blocks/*/*.zen", "", "no .zen files found"))
    return f, len(files)


def sexp_block(text, start):
    """Text of the balanced (...) starting at index `start` (quoted strings respected)."""
    depth, q, i = 0, False, start
    while i < len(text):
        c = text[i]
        if q:
            if c == "\\":
                i += 1
            elif c == '"':
                q = False
        elif c == '"':
            q = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    return text[start:]


def check_title_blocks(root):
    f, sheets = [], sorted(root.glob("benches/*/eda/*.kicad_sch"))
    for p in sheets:
        text, r = p.read_text(), rel(root, p)
        i = text.find("(title_block")
        if i < 0:
            f.append(Finding("TITLE-BLOCK", r, "title_block", "no title block"))
            continue
        tb = sexp_block(text, i)
        vals = {m.group(1): m.group(2) for m in re.finditer(r'\((title|date|rev|company)\s+"([^"]*)"\)', tb)}
        vals.update({f"comment {m.group(1)}": m.group(2) for m in re.finditer(r'\(comment\s+(\d+)\s+"([^"]*)"\)', tb)})
        for k in ("title", "date", "rev", "company", "comment 1", "comment 2", "comment 3"):
            if not vals.get(k, "").strip():
                f.append(Finding("TITLE-BLOCK", r, k, "missing or empty"))
        for k, v in vals.items():
            if not v.strip() and k not in ("title", "date", "rev", "company", "comment 1", "comment 2", "comment 3"):
                f.append(Finding("TITLE-BLOCK", r, k, "empty"))
    return f, len(sheets)


PASSIVE = re.compile(r"^(capacitor|resistor|current_sense_resistor|inductor)")


def check_passives(root, zl, deps):
    f, n = [], 0
    for manifest, key, url, sel, ver in deps:
        if not url.startswith(ZL_PREFIX) or not isinstance(ver, str):
            continue
        tag = dep_tag(url, ver)
        r = git(zl, "show", f"{tag}:components/{url[len(ZL_PREFIX):]}/component.toml")
        if r.returncode:
            continue  # missing tag/file is reported by ENV-DEP-TAG
        part = tomllib.loads(r.stdout).get("part", {})
        if not PASSIVE.match(part.get("category", "")):
            continue
        n += 1
        m = re.search(r"(?<!\w)(\d{4})(?!\w)", part.get("package", ""))
        if not m:
            f.append(Finding("PASSIVE-SIZE", manifest, key, f"package {part.get('package')!r} has no 4-digit size code"))
        elif int(m.group(1)[:2]) < 6:
            f.append(Finding("PASSIVE-SIZE", manifest, key, f"package {part['package']!r} is smaller than 0603 (ee-design: 0603 or larger)"))
    return f, n


def run(root, zl, version=None):
    root = Path(root)
    version = version or read_zl_version(root)
    findings, checked = [], 0
    for fs, n in (check_manifests(root),):
        findings += fs
        checked += n
    findings += check_zl(root, zl, version)
    fs, n, deps = check_deps(root, zl, version)
    findings += fs
    checked += n
    if not deps:
        findings.append(Finding("EMPTY", "pcb.toml", "", "no zener-library dependencies found"))
    findings += check_lck(root)
    fs, n = check_apply(root)
    findings += fs
    checked += n
    fs, n = check_rails(root)
    findings += fs
    checked += n
    fs, n = check_title_blocks(root)
    findings += fs
    checked += n
    if n == 0:
        findings.append(Finding("EMPTY", "benches/*/eda/*.kicad_sch", "", "no schematics found"))
    fs, n = check_passives(root, zl, deps)
    findings += fs
    checked += n
    return findings, checked


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--zl")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    zl = Path(a.zl).resolve() if a.zl else default_zl(root)
    findings, checked = run(root, zl)
    entries = load_allowlist(root / "tools" / "known_findings.toml")
    return report("check_env", findings, entries, RULES, checked)


if __name__ == "__main__":
    sys.exit(main())
