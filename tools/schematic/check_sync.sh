#!/bin/zsh
# Verify the KiCad schematic matches the Zener source of truth, without modifying the repo.
# Usage (repo root): tools/schematic/check_sync.sh [board.zen]      exit 0 = in sync, 1 = drift
#
# 1. `pcb build -Dwarnings`: the linked schematic must be netlist-equivalent to the .zen design
#    (catches rewiring, shorts and split nets made in KiCad).
# 2. `pcb apply` on a throwaway copy must report "schematic unchanged" (catches part values, MPNs and
#    other fields edited in KiCad, which the build does not check).
# Run after any human review in KiCad, and before committing.
setopt nullglob
zen=${1:-ina229.zen}
repo=$PWD
fail=0
if pgrep -q eeschema; then echo "WARN: KiCad eeschema is running; close it so the files on disk are final"; fi

echo "[1/2] connectivity: pcb build -Dwarnings $zen"
if pcb build -Dwarnings $zen >/tmp/check_sync_build.log 2>&1; then
  echo "      OK: schematic is netlist-equivalent to the .zen"
else
  echo "      DRIFT: build failed; see diagnostics:"; grep -E 'Warning|Error' /tmp/check_sync_build.log | sed 's/^/        /'; fail=1
fi

echo "[2/2] fields: pcb apply on a throwaway copy"
tmp=$(mktemp -d)
rsync -a --exclude .git --exclude '*.lck' $repo/ $tmp/
out=$(cd $tmp && pcb apply schematic --no-open $zen 2>&1 | grep -E 'schematic (unchanged|updated|created)' | tail -1)
if [[ $out == *unchanged* ]]; then
  echo "      OK: applying the .zen changes nothing in the schematic"
else
  echo "      DRIFT: applying the .zen would change the schematic ($out)"
  for f in $repo/eda/*.kicad_sch; do
    n=${f:t}; diff -q $f $tmp/eda/$n >/dev/null || { echo "        differs: eda/$n"; diff $f $tmp/eda/$n | grep -E '^[<>].*"(Value|MPN|Manufacturer_Part_Number|Footprint)"' | head -10 | sed 's/^/          /'; }
  done
  fail=1
fi
rm -rf $tmp
if (( fail )); then echo "RESULT: DRIFT - run 'pcb apply schematic --no-open $zen' to restore the .zen values, then review"; exit 1; fi
echo "RESULT: IN SYNC - the KiCad schematic matches the Zener source of truth"
