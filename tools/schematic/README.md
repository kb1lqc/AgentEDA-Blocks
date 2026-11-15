# Schematic tools

- `check_sync.sh [bench.zen ...]`: every bench schematic (default `benches/*/bench.zen`) matches its `.zen`
  (strict build + `pcb apply` on a throwaway copy). Run by the pre-commit hook.
- `export_pdf.sh [--check] [benches/<Block> ...]`: reproducible PDF of each bench's sheets into
  `benches/<Block>/docs/<bench name>.pdf` (named after the Board / KiCad project; creation date pinned); `--check` fails on a stale or missing PDF (run by
  `check_sync.sh`).
- `update_notes.py benches/<Block>`: rewrites the parent-sheet engineering notes from the bench's `values.json`
  using the bench's `notes_rules.py` (pattern-matched, each must match once). Run after the analysis; then gate
  with apply/netlist/build/ERC.
- `stamp_block.py`: copies a block's arranged reference sheet onto a board per instance (new UUIDs, instance
  prefix, missing sheet pins). See its `--help`.

## INA sheet arrangement scripts (reference)

One-shot scripts the drafting agents used to arrange the INA sheet (2026-11-15, then `eda/INA.kicad_sch`, now `benches/INA229HighSideMonitor/eda/INA.kicad_sch`), kept as worked examples
for the placement generator described in the zener-library `schematic-capture` skill. They are not meant to be
re-run as-is: each reads a specific intermediate schematic and writes the next.

- `arrange_ina_pass1.py`: places the shunt, INA229 (rotated 180 degrees), sense stubs, VS/GND/C1 and the SPI
  fan-out from the freshly generated sheet.
- `arrange_ina_pass2.py`: moves R1 next to the ALERT pin and straightens the SPI/ALERT_N runs.

Generic helpers (render, ERC summary, netlist diff) live in zener-library `tools/schematic/`.
