"""Canonical item order for a generated KiCad sheet, so a generator run on its own output is byte-identical.

KiCad saves a sheet's top-level items grouped by type, each group sorted by uuid. place.Sheet appends new items in
emission order instead, so a generated file differs from the KiCad-saved one (same items, different order) and every
re-run churns the file. canonicalize() reorders the generated items into KiCad's order; items of kinds not listed
here (header, title block, lib_symbols, sheet_instances, ...) keep their place.
"""
ORDER = ("rectangle", "text", "junction", "no_connect", "wire", "label", "global_label", "hierarchical_label",
         "symbol", "sheet")


def _uuid(node):
    g = node.get("uuid")
    return g.args()[0] if g else ""


def _fix_indent(node):
    """place.Sheet snippets are written at depth 0 and only the outer line is re-indented on append, so a freshly
    added item has its children one tab too shallow (KiCad re-indents on its own save). Shift such a subtree."""
    if not any(isinstance(c, type(node)) and c.ws == "\n\t" for c in node.items):
        return
    for n in node.walk():
        if n is not node and "\n" in n.ws:
            n.ws += "\t"
        if "\n" in n.close_ws or n is node:
            n.close_ws += "\t" if "\n" in n.close_ws else ""
        for a in n.atoms():
            if "\n" in a.ws:
                a.ws += "\t"


def canonicalize(root):
    """Reorder the ORDER kinds in place: by type, then uuid, starting where the first of them sat."""
    rank = {h: i for i, h in enumerate(ORDER)}
    movable = [c for c in root.nodes() if c.head in rank]
    for m in movable:
        _fix_indent(m)
    if not movable:
        return
    first = root.items.index(movable[0])
    rest = [c for c in root.items if not any(c is m for m in movable)]
    # keep non-movable items that preceded the first movable item before it, the others after the block
    head = [c for c in root.items[:first] if not any(c is m for m in movable)]
    tail = rest[len(head):]
    ordered = sorted(movable, key=lambda n: (rank[n.head], _uuid(n)))
    root.items = head + ordered + tail


def save(sheet):
    """Canonicalize and save a place.Sheet."""
    canonicalize(sheet.root)
    sheet.save()
