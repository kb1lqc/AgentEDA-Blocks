# Schematic tools

Each script documents itself (`--help` or header). All run on copies where noted; stdlib only.
- `check_sync.sh [bench.zen ...]`: every bench schematic matches its `.zen`, plus stale PDF and stamp checks. Run by the pre-commit hook.
- `export_pdf.sh [--check] [benches/<Block> ...]`: reproducible bench PDF into `benches/<Block>/docs/<bench name>.pdf`.
- `analysis/md_pdf.py`: renders `design-analysis.md` to PDF (every analysis run); `python3 -m analysis.md_pdf --check <md>...` fails when stale.
- `stamp_hash.py [--check] [sheets...]`: sets each sheet's title-block hash to the commit that last changed its content. After committing sheet changes: `stamp_hash.py`, `export_pdf.sh`, commit "Stamp sheet hashes".
- `update_notes.py benches/<Block>`: rewrites parent-sheet notes from `values.json` via the bench's `notes_rules.py` (each pattern must match once). Run after the analysis, then gate with apply, netlist, build, ERC.
- `passive_text.py`: component text stacks for `arrange_sheet.py`, resolved from the library catalog by MPN; has the `--check` that `check_sync.sh` runs.
- `canon.py`: canonicalises saved sheets (idempotent generators).
- `stamp_block.py`: copies a block's reference sheet onto a board per instance.

- `../check_env.py`: fast env and naming checks (pcb-version, nested `[workspace]`, ZL checkout and dep tags, `*.lck`, `--no-open`, rail names, title blocks, passive size); runs on every commit. `../check_reports.py`: regenerates each bench report in a temp copy and compares `design-analysis.md` and `values.json` byte for byte. Accepted findings: `../known_findings.toml`.

Generic helpers (render, ERC summary, netlist diff, `place.py`, `overlap.py`) live in zener-library `tools/schematic/`; facts and workflow: `../zener-library/docs/spec/pcb-toolchain.md`, `docs/briefs/schematic-capture.md`.
