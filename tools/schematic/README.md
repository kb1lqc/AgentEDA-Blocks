# Schematic tools

- `check_sync.sh [bench.zen ...]`: every bench schematic (default `benches/*/bench.zen`) matches its `.zen`
  (strict build + `pcb apply` on a throwaway copy). Run by the pre-commit hook.
- `export_pdf.sh [--check] [benches/<Block> ...]`: reproducible PDF of each bench's sheets into
  `benches/<Block>/docs/<bench name>.pdf` (named after the Board / KiCad project; creation date pinned); `--check` fails on a stale or missing PDF (run by
  `check_sync.sh`).
- `analysis/md_pdf.py`: renders each `benches/<Block>/docs/design-analysis.md` to `design-analysis.pdf` (written by every analysis run); `python3 -m analysis.md_pdf --check <md>...` fails on a missing or stale PDF (run by `check_sync.sh`).
- `update_notes.py benches/<Block>`: rewrites the parent-sheet engineering notes from the bench's `values.json`
  using the bench's `notes_rules.py` (pattern-matched, each must match once). Run after the analysis; then gate
  with apply/netlist/build/ERC.
- `passive_text.py`: shared by every bench's `arrange_sheet.py`: Fox-1-MPPT component text stacks (value, package, rating, tolerance) for resistors and capacitors, resolved from the library catalog by MPN, plus the `--check` that `check_sync.sh` runs.
- `stamp_block.py`: copies a block's arranged reference sheet onto a board per instance (new UUIDs, instance
  prefix, missing sheet pins). See its `--help`.

Generic helpers (render, ERC summary, netlist diff, placement `place.py`, overlap/dangling check `overlap.py`) live in
zener-library `tools/schematic/`; their lessons are in the `schematic-capture` skill.
