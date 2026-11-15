# Schematic tools

- `update_notes.py`: rewrites the engineering notes on both sheets from `docs/design-analysis/values.json`
  (pattern-matched, each must match once). Run after the analysis; then gate with apply/netlist/build/ERC.

## INA sheet arrangement scripts (reference)

One-shot scripts the drafting agents used to arrange `eda/INA.kicad_sch` (2026-11-15), kept as worked examples
for the placement generator described in the zener-library `schematic-capture` skill. They are not meant to be
re-run as-is: each reads a specific intermediate schematic and writes the next.

- `arrange_ina_pass1.py`: places the shunt, INA229 (rotated 180 degrees), sense stubs, VS/GND/C1 and the SPI
  fan-out from the freshly generated sheet.
- `arrange_ina_pass2.py`: moves R1 next to the ALERT pin and straightens the SPI/ALERT_N runs.

Generic helpers (render, ERC summary, netlist diff) live in zener-library `tools/schematic/`.
