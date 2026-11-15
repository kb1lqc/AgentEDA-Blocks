#!/usr/bin/env python3
"""Stamp a block's arranged reference KiCad sheet onto a board, once per instance.

pcb generates one fresh, unarranged child sheet `<instance>.kicad_sch` per block instance in the board's eda dir.
This tool replaces it with a copy of the arranged reference sheet:

  * the instance prefix in internal net labels is rewritten (`INA.SENSE_P` -> `<NAME>.SENSE_P`; label names only),
  * every `(uuid ...)` gets a deterministic new value (uuid5 of instance + old uuid), so re-runs are stable and
    two instances never share a UUID,
  * part symbols take the refdes and uuid pcb generated for this instance in the existing sheet (refdes are
    board-wide: INA gets R1..R3, INB R4..R6),
  * symbol `(instances (project ...) (path ...))` entries are pointed at the board's project name, root-sheet UUID
    and the instance's sheet-symbol UUID (read from the board's generated root sheet),
  * the root sheet symbol gets any sheet pin that matches a hierarchical label in the child but is missing
    (e.g. V3V3 on the top edge, GND on the bottom edge), copied in form from the reference board's root sheet
    symbol. Pins that already exist are left where they are (their root wires stay connected).

Prerequisite: generate the board once (`pcb apply schematic --no-open <board>.zen`) so the root sheet and the
sheet symbols exist. After stamping, run `pcb apply schematic --no-open` twice (the second must be unchanged).

Usage:
  python3 tools/schematic/stamp_block.py --ref <ref>.kicad_sch --ref-instance INA \\
      --board <board>.zen --instance NAME [--instance NAME2 ...] [--ref-root <root>.kicad_sch] [--force]

A child sheet that already has hand edits (boxes, notes or wires that differ from the reference's) is not
overwritten without --force. A fresh pcb sheet (no boxes or notes) or an earlier stamp of the same reference is overwritten (idempotent).
"""
import argparse
import re
import sys
import uuid
from pathlib import Path

NS = uuid.UUID("6f1d0c52-3b0e-4f0e-9a43-5d1f6a2c7b10")
GRID = 1.27


# --------------------------------------------------------------------------- S-expression tokenizer / tree
class Atom:
    def __init__(self, start, end, value, quoted):
        self.start, self.end, self.value, self.quoted = start, end, value, quoted


class Node:
    """A parenthesised list; start/end are character offsets in the source text."""

    def __init__(self, start):
        self.start, self.end, self.children = start, start, []

    @property
    def head(self):
        c = self.children
        return c[0].value if c and isinstance(c[0], Atom) and not c[0].quoted else None

    def sub(self, head):
        return [c for c in self.children if isinstance(c, Node) and c.head == head]

    def first(self, head):
        s = self.sub(head)
        return s[0] if s else None

    def atoms(self):
        return [c for c in self.children if isinstance(c, Atom)]

    def walk(self):
        yield self
        for c in self.children:
            if isinstance(c, Node):
                yield from c.walk()


_TOK = re.compile(r'\s+|(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+)')


def parse(text):
    """Parse one top-level S-expression. Raises ValueError on malformed input."""
    stack, root, pos = [], None, 0
    while pos < len(text):
        m = _TOK.match(text, pos)
        if not m:
            raise ValueError(f"tokenizer stuck at offset {pos}")
        if m.group(1):
            n = Node(m.start())
            if stack:
                stack[-1].children.append(n)
            elif root is None:
                root = n
            else:
                raise ValueError("more than one top-level expression")
            stack.append(n)
        elif m.group(2):
            if not stack:
                raise ValueError("unbalanced ')'")
            stack[-1].end = m.end()
            stack.pop()
        elif m.group(3) is not None:
            if not stack:
                raise ValueError("atom outside list")
            stack[-1].children.append(Atom(m.start(), m.end(), m.group(3), True))
        elif m.group(4) is not None:
            if not stack:
                raise ValueError("atom outside list")
            stack[-1].children.append(Atom(m.start(), m.end(), m.group(4), False))
        pos = m.end()
    if stack or root is None:
        raise ValueError("unbalanced '('")
    return root


def q(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def apply_edits(text, edits):
    """edits: (start, end, replacement) tuples, non-overlapping."""
    for s, e, r in sorted(edits, key=lambda x: x[0], reverse=True):
        text = text[:s] + r + text[e:]
    return text


def new_uuid(instance, old):
    """Deterministic replacement for `old`. The version nibble is kept: pcb treats version-5 uuids as its own
    generated items (and removes/re-adds them), while the hand-placed items (hierarchical labels that replaced
    the power symbols) carry version-4 uuids and must stay recognisably hand-made."""
    u = uuid.uuid5(NS, f"{instance}:{old}")
    try:
        ver = uuid.UUID(old).version
    except ValueError:
        ver = 5
    return str(uuid.UUID(int=(u.int & ~(0xF << 76)) | ((ver or 5) << 76)))


# --------------------------------------------------------------------------- helpers
def board_info(zen):
    src = Path(zen).read_text()
    m = re.search(r"\bBoard\s*\((.*?)\n\)", src, re.S) or re.search(r"\bBoard\s*\((.*?)\)", src, re.S)
    if not m:
        sys.exit(f"no Board(...) call found in {zen}")
    name = re.search(r'\bname\s*=\s*"([^"]+)"', m.group(1))
    path = re.search(r'\bpath\s*=\s*"([^"]+)"', m.group(1))
    if not name or not path:
        sys.exit(f"Board(...) in {zen} needs literal name= and path=")
    return name.group(1), (Path(zen).resolve().parent / path.group(1)).resolve()


def prop(node, key):
    for p in node.sub("property"):
        a = p.atoms()
        if len(a) >= 3 and a[1].value == key:
            return a[2].value
    return None


def find_sheet(tree, sheetfile=None, sheetname=None):
    for s in tree.sub("sheet"):
        if sheetname is not None and prop(s, "Sheetname") == sheetname:
            return s
        if sheetfile is not None and prop(s, "Sheetfile") == sheetfile:
            return s
    return None


def nums(node):
    return [float(x.value) for x in node.atoms()[1:]]


def fmt(v):
    return f"{round(v, 4):g}"


def signature(tree):
    """Placement fingerprint (uuid- and instance-independent): wires, rectangles, texts."""
    sig = []
    for n in tree.children:
        if not isinstance(n, Node):
            continue
        if n.head == "wire":
            sig.append(("wire", tuple(tuple(nums(p)) for p in n.first("pts").sub("xy"))))
        elif n.head == "rectangle":
            sig.append(("rect", tuple(tuple(nums(n.first(k))) for k in ("start", "end"))))
        elif n.head == "text":
            sig.append(("text", n.atoms()[1].value, tuple(nums(n.first("at")))))
    return sorted(sig, key=repr)


def counts(tree):
    out = {}
    for n in tree.children:
        if isinstance(n, Node) and n.head in ("wire", "rectangle", "text", "label", "hierarchical_label", "symbol"):
            out[n.head] = out.get(n.head, 0) + 1
    return out


# --------------------------------------------------------------------------- stamping one child
def refnum(ref):
    m = re.search(r"(\d+)$", ref)
    return int(m.group(1)) if m else 0


def symbol_map(ref_tree, target_tree, name, ref_instance):
    """Pair each reference part with the part pcb generated for this instance in the existing sheet.

    Returns {ref symbol uuid: (target symbol uuid, target refdes, {pin number: target pin uuid})}.
    Reference designators are board-wide (INA owns R1..R3, INB gets R4..R6) and pcb recognises its parts by
    refdes, uuid and the `Path` property (`INA.R_ALERT.ERJ-3EKF`); a part that does not match is re-placed
    off-sheet. Parts are paired by Path with the instance prefix swapped; without a Path property, by
    (lib_id, Value) and refdes order. The netlist comparison is the final check. With no existing sheet the
    reference refdes stay and uuids are derived.
    """
    def key(n):
        pth = prop(n, "Path")
        if pth is not None:
            return ("path", pth[len(ref_instance):] if pth.startswith(ref_instance + ".") else pth)
        return ("lib", n.first("lib_id").atoms()[1].value, prop(n, "Value"))

    def parts(tree):
        out = {}
        for n in tree.sub("symbol"):
            ref = prop(n, "Reference")
            if ref is None or ref.startswith("#"):
                continue
            k = key(n)
            out.setdefault(k, []).append(n)
        for v in out.values():
            v.sort(key=lambda n: refnum(prop(n, "Reference")))
        return out

    if target_tree is None:
        return {}
    rp = parts(ref_tree)
    tp = {}
    for n in target_tree.sub("symbol"):
        ref = prop(n, "Reference")
        if ref is None or ref.startswith("#"):
            continue
        pth = prop(n, "Path")
        if pth is not None and pth.startswith(name + "."):
            k = ("path", pth[len(name):])
        elif pth is not None:
            k = ("path", pth)
        else:
            k = ("lib", n.first("lib_id").atoms()[1].value, prop(n, "Value"))
        tp.setdefault(k, []).append(n)
    for v in tp.values():
        v.sort(key=lambda n: refnum(prop(n, "Reference")))
    m = {}
    for k, rs in rp.items():
        ts = tp.get(k, [])
        if len(ts) != len(rs):
            sys.exit(f"{name}: cannot pair part {k}: reference has {len(rs)}, existing sheet has {len(ts)}")
        for r, t in zip(rs, ts):
            tpins = {pn.atoms()[1].value: pn.first("uuid").atoms()[1].value for pn in t.sub("pin")}
            m[r.first("uuid").atoms()[1].value] = (t.first("uuid").atoms()[1].value, prop(t, "Reference"), tpins)
    return m


def stamp_child(ref_text, ref_tree, ref_instance, name, project, root_uuid, sheet_uuid, symmap, file_uuid=None):
    edits = []
    done = set()  # nodes already given an edit
    top = ref_tree.first("uuid")
    if file_uuid:  # pcb derives its generated item uuids from the sheet file's own uuid: keep the one it made
        ta = top.atoms()[1]
        edits.append((ta.start, ta.end, q(file_uuid)))
        done.add(id(top))
    for sym in ref_tree.sub("symbol"):
        su = sym.first("uuid").atoms()[1]
        for pr in sym.sub("property"):
            a = pr.atoms()
            if a[1].value == "Path" and a[2].value.startswith(ref_instance + "."):
                edits.append((a[2].start, a[2].end, q(name + a[2].value[len(ref_instance):])))
        if su.value not in symmap:
            continue
        nu, nref, tpins = symmap[su.value]
        edits.append((su.start, su.end, q(nu)))
        done.add(id(sym.first("uuid")))
        for pn in sym.sub("pin"):
            pu = pn.first("uuid")
            if pu is not None and pn.atoms()[1].value in tpins:
                edits.append((pu.atoms()[1].start, pu.atoms()[1].end, q(tpins[pn.atoms()[1].value])))
                done.add(id(pu))
        for pr in sym.sub("property"):
            a = pr.atoms()
            if a[1].value == "Reference":
                edits.append((a[2].start, a[2].end, q(nref)))
        for ins in sym.sub("instances"):
            for pr in ins.sub("project"):
                for pth in pr.sub("path"):
                    rf = pth.first("reference").atoms()[1]
                    edits.append((rf.start, rf.end, q(nref)))
    for n in ref_tree.walk():
        a = n.atoms()
        if n.head == "uuid" and len(a) == 2 and id(n) not in done:
            edits.append((a[1].start, a[1].end, q(new_uuid(name, a[1].value))))
        elif n.head == "label" and len(a) >= 2 and a[1].value.startswith(ref_instance + "."):
            edits.append((a[1].start, a[1].end, q(name + a[1].value[len(ref_instance):])))
        elif n.head == "instances":
            for pr in n.sub("project"):
                pa = pr.atoms()
                edits.append((pa[1].start, pa[1].end, q(project)))
                for pth in pr.sub("path"):
                    pta = pth.atoms()
                    edits.append((pta[1].start, pta[1].end, q(f"/{root_uuid}/{sheet_uuid}")))
    return apply_edits(ref_text, edits)


# --------------------------------------------------------------------------- sheet pin transplant
def transplant_pins(root_text, root_tree, name, child_tree, ref_sheet, ref_root_text):
    """Add sheet pins for child hierarchical labels missing on NAME's sheet symbol. Returns (text, [names])."""
    sheet = find_sheet(root_tree, sheetname=name)
    have = {p.atoms()[1].value for p in sheet.sub("pin")}
    want = []
    for n in child_tree.sub("hierarchical_label"):
        v = n.atoms()[1].value
        if v not in want:
            want.append(v)
    missing = [w for w in want if w not in have]
    if not missing:
        return root_text, []
    if ref_sheet is None:
        sys.exit(f"sheet pins {missing} missing on {name} and no reference root sheet symbol to copy from "
                 f"(pass --ref-root)")
    sx, sy = nums(sheet.first("at"))
    sw, sh = nums(sheet.first("size"))
    rx, ry = nums(ref_sheet.first("at"))
    rw, rh = nums(ref_sheet.first("size"))
    ref_pins = {p.atoms()[1].value: p for p in ref_sheet.sub("pin")}
    taken = [tuple(nums(p.first("at"))[:2]) for p in sheet.sub("pin")]
    blocks, added = [], []
    for w in missing:
        rp = ref_pins.get(w)
        if rp is None:
            sys.exit(f"sheet pin {w!r} needed on {name} but the reference root sheet symbol has no such pin")
        px, py, ang = nums(rp.first("at"))
        # same edge as in the reference, same fractional position along it, on the grid
        if ang in (90, 270):
            x = sx + round((px - rx) / rw * sw / GRID) * GRID
            y = sy if ang == 90 else sy + sh
            step = (2 * GRID, 0)
        else:
            y = sy + round((py - ry) / rh * sh / GRID) * GRID
            x = sx if ang == 180 else sx + sw
            step = (0, 2 * GRID)
        while any(abs(t[0] - x) < 1.0 and abs(t[1] - y) < 1.0 for t in taken):
            x, y = x + step[0], y + step[1]
        taken.append((x, y))
        at, ua = rp.first("at"), rp.first("uuid").atoms()[1]
        blocks.append(apply_edits(ref_root_text[rp.start:rp.end], [
            (at.start - rp.start, at.end - rp.start, f"(at {fmt(x)} {fmt(y)} {int(ang)})"),
            (ua.start - rp.start, ua.end - rp.start, q(new_uuid(name, "sheetpin:" + w))),
        ]))
        added.append(f"{w}@({fmt(x)},{fmt(y)},{int(ang)})")
    pins = sheet.sub("pin")
    off = pins[-1].end if pins else sheet.first("instances").start
    return root_text[:off] + "".join("\n\t\t" + b for b in blocks) + root_text[off:], added


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", required=True, help="arranged reference child sheet (.kicad_sch)")
    ap.add_argument("--ref-instance", required=True, help="instance name the reference was generated for (INA)")
    ap.add_argument("--board", required=True, help="board .zen (Board(name=, path=) locates the eda dir)")
    ap.add_argument("--instance", action="append", required=True, help="instance name on the board (repeatable)")
    ap.add_argument("--ref-root", help="root sheet holding the reference's sheet symbol (default: a sibling of "
                                       "--ref whose sheet symbol has Sheetfile == the reference file name)")
    ap.add_argument("--force", action="store_true", help="overwrite child sheets that have hand edits")
    args = ap.parse_args()

    ref = Path(args.ref).resolve()
    ref_text = ref.read_text()
    ref_tree = parse(ref_text)
    ref_sig = signature(ref_tree)

    ref_sheet, ref_root_text = None, ""
    for c in ([Path(args.ref_root).resolve()] if args.ref_root else sorted(ref.parent.glob("*.kicad_sch"))):
        if c == ref:
            continue
        t = c.read_text()
        s = find_sheet(parse(t), sheetfile=ref.name)
        if s is not None:
            ref_root_text = t
            ref_sheet = find_sheet(parse(t), sheetfile=ref.name)
            break
    if ref_sheet is None:
        print(f"note: no reference root sheet symbol found for {ref.name}; sheet-pin transplant unavailable")

    project, eda = board_info(args.board)
    root_path = eda / f"{project}.kicad_sch"
    if not root_path.exists():
        sys.exit(f"{root_path} not found: run `pcb apply schematic --no-open {args.board}` once first")
    root_text = root_path.read_text()
    root_tree = parse(root_text)
    root_uuid = root_tree.first("uuid").atoms()[1].value
    print(f"board {project!r}: eda {eda}, root uuid {root_uuid}")
    print(f"reference {ref}: {counts(ref_tree)}")

    # check every instance first so a refusal leaves everything untouched
    plan = []
    for name in args.instance:
        sheet = find_sheet(root_tree, sheetname=name)
        if sheet is None:
            sys.exit(f"no sheet symbol named {name!r} in {root_path}; is the instance declared in {args.board}?")
        target = eda / (prop(sheet, "Sheetfile") or f"{name}.kicad_sch")
        if target.exists() and not args.force:
            cur = signature(parse(target.read_text()))
            # a fresh pcb sheet has no group boxes or notes; an arranged one must match the reference placement
            if any(s[0] in ("rect", "text") for s in cur) and cur != ref_sig:
                sys.exit(f"refusing to overwrite {target}: it has boxes/notes/wires that differ from the reference "
                         f"arrangement (hand edits); use --force")
        plan.append((name, sheet, target))

    for name, sheet, target in plan:
        sheet_uuid = sheet.first("uuid").atoms()[1].value
        cur_tree = parse(target.read_text()) if target.exists() else None
        symmap = symbol_map(ref_tree, cur_tree, name, args.ref_instance)
        file_uuid = cur_tree.first("uuid").atoms()[1].value if cur_tree is not None else None
        out = stamp_child(ref_text, ref_tree, args.ref_instance, name, project, root_uuid, sheet_uuid, symmap,
                          file_uuid)
        child_tree = parse(out)
        existed = target.exists()
        target.write_text(out)
        print(f"{name}: {'replaced' if existed else 'wrote'} {target}; {counts(child_tree)}; "
              f"labels {args.ref_instance}.* -> {name}.*; uuids uuid5; instance path /{root_uuid[:8]}/{sheet_uuid[:8]}")
        root_text, added = transplant_pins(root_text, root_tree, name, child_tree, ref_sheet, ref_root_text)
        if added:
            root_path.write_text(root_text)
            root_tree = parse(root_text)
            print(f"{name}: added root sheet pins {added}")
        else:
            print(f"{name}: root sheet symbol already has a pin for every hierarchical label")
    print("next: pcb apply schematic --no-open <board>.zen, twice (second must say unchanged)")


if __name__ == "__main__":
    main()
