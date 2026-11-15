#!/bin/zsh
# Verify every bench's KiCad schematic matches the Zener source of truth, without modifying the repo.
# Usage (repo root): tools/schematic/check_sync.sh [benches/<Block>/bench.zen ...]   exit 0 = in sync, 1 = drift
# With no arguments, checks every benches/*/bench.zen.
#
# Per bench:
# 1. `pcb build -Dwarnings`: the linked schematic must be netlist-equivalent to the .zen design
#    (catches rewiring, shorts and split nets made in KiCad).
# 2. `pcb apply` on a throwaway copy must report "schematic unchanged" (catches part values, MPNs and
#    other fields edited in KiCad, which the build does not check).
# 3. benches/<Block>/docs/<bench name>.pdf must equal a fresh reproducible export (tools/schematic/export_pdf.sh).
# Run after any human review in KiCad, and before committing (the pre-commit hook runs it).
setopt nullglob
repo=$PWD
zens=("$@")
(( ${#zens} )) || zens=(benches/*/bench.zen)
(( ${#zens} )) || { echo "no benches/*/bench.zen found (run from the repo root)"; exit 1; }
fail=0
if pgrep -q eeschema; then echo "WARN: KiCad eeschema is running; close it so the files on disk are final"; fi
tmp=$(mktemp -d)
rsync -a --exclude .git --exclude '*.lck' $repo/ $tmp/
for zen in $zens; do
  eda=${zen:h}/eda
  echo "== $zen"
  echo "[1/2] connectivity: pcb build -Dwarnings"
  if pcb build -Dwarnings $zen >$tmp/build.log 2>&1 || pcb build -Dwarnings $zen >$tmp/build.log 2>&1; then
    echo "      OK: schematic is netlist-equivalent to the .zen"
  else
    echo "      DRIFT: build failed; see diagnostics:"; grep -E 'Warning|Error' $tmp/build.log | sed 's/^/        /'; fail=1
  fi
  echo "[2/2] fields: pcb apply on a throwaway copy"
  out=$(cd $tmp && pcb apply schematic --no-open $zen 2>&1 | grep -E 'schematic (unchanged|updated|created)' | tail -1)
  if [[ $out == *unchanged* ]]; then
    echo "      OK: applying the .zen changes nothing in the schematic"
  else
    echo "      DRIFT: applying the .zen would change the schematic ($out)"
    for f in $repo/$eda/*.kicad_sch; do
      n=${f:t}; diff -q $f $tmp/$eda/$n >/dev/null || { echo "        differs: $eda/$n"; diff $f $tmp/$eda/$n | grep -E '^[<>].*"(Value|MPN|Manufacturer_Part_Number|Footprint)"' | head -10 | sed 's/^/          /'; }
    done
    fail=1
  fi
done
rm -rf $tmp
echo "== schematic PDFs (benches/*/docs/<bench name>.pdf)"
benches=(${zens:h})
tools/schematic/export_pdf.sh --check $benches | sed 's/^/      /' || fail=1
if (( fail )); then echo "RESULT: DRIFT - run 'pcb apply schematic --no-open <bench.zen>' to restore the .zen values (and tools/schematic/export_pdf.sh for a stale PDF), then review"; exit 1; fi
echo "RESULT: IN SYNC - every checked KiCad schematic matches the Zener source of truth"
