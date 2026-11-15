#!/bin/zsh
# Export each bench's KiCad schematic (all sheets) to benches/<Block>/docs/<bench name>.pdf, reproducibly.
# The bench name is the Board name, which is also the KiCad project name in eda/ (e.g. INA229HighSideMonitor_bench).
# Usage (repo root): tools/schematic/export_pdf.sh [--check] [benches/<Block> ...]   (default: every benches/*/)
#   --check: export to a temp dir and compare with the committed PDF; exit 1 if any is missing or stale.
# kicad-cli runs on a copy of eda/ (it can leave .lck files in the project folder). The PDF's only varying byte
# content, /CreationDate, is pinned to a fixed value of the same length (xref offsets stay valid), so the same
# schematic and KiCad version give a byte-identical file. A KiCad upgrade can change the output: re-export then.
setopt nullglob
kc=${KICAD_CLI:-/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli}
check=0
[[ $1 == --check ]] && { check=1; shift; }
benches=("$@")
(( ${#benches} )) || benches=(benches/*/)
tmp=$(mktemp -d)
fail=0
for b in $benches; do
  b=${b%/}
  root=($b/eda/*.kicad_pro)
  (( ${#root} == 1 )) || { echo "$b: expected one .kicad_pro in $b/eda"; fail=1; continue; }
  name=${root[1]:t:r}
  rm -rf $tmp/eda && cp -R $b/eda $tmp/eda && rm -f $tmp/eda/*.lck
  out=$tmp/${b:t}.pdf
  if ! $kc sch export pdf -o $out $tmp/eda/$name.kicad_sch >/dev/null 2>&1; then
    echo "$b: kicad-cli export failed"; fail=1; continue
  fi
  LC_ALL=C sed -i '' -E 's#/CreationDate \(D:[0-9:]{19}\)#/CreationDate (D:2000:01:01:00:00:00)#' $out
  dest=$b/docs/$name.pdf
  if (( check )); then
    if cmp -s $out $dest; then echo "$dest: up to date"; else echo "$dest: STALE or missing (run tools/schematic/export_pdf.sh $b)"; fail=1; fi
  else
    mkdir -p $b/docs && cp $out $dest && echo "wrote $dest"
  fi
done
rm -rf $tmp
exit $fail
