# ina229 board: agent rules

Board repo for an INA229-based power monitor, captured in Zener (`pcb` 0.4.x). Components come from
`github.com/kb1lqc/zener-library` (the author's library; part data, equations and wrappers are fixed there, not
here). Correctness beats speed. Never guess a number. I review every diff; do not commit unless I ask.

## Sources of truth
- **Part data and datasheet equations** live in the library's `component.toml` / `calc.py`. Never retype a datasheet
  number or equation in this repo. A missing or wrong part number is a library change, made in the library repo
  (local working copy: `../diodecomputer`), released with a new tag, then picked up here.
- **Design intent** (rails, ranges, gains, current limits, ambient) lives in `spec.md` first, then as named configs
  on the board or module. Every non-datasheet value is a DESIGN CHOICE with a comment saying why.
- **Analysis** (worst case, nominal) is Python in `analysis/`, loading parts through the library's `lib`
  (`from analysis import _zl` then `from lib import load, Param, wca, table`). Compute component values there from
  `calc.py`; snap in Zener with the stdlib E-series helpers.

## Design approach (how I design; apply by default)
- **Worst-case analysis bounds every requirement.** Derive each value from WCA over all min/max corners (Python,
  `analysis/`), not from typicals. State the requirement, the bound, and the margin.
- **Fewest components.** Add a part only when a requirement or WCA needs it. Use internal pull-ups/defaults where
  the datasheet allows.
- **Minimize BOM line items on the first pass.** Reuse resistor and capacitor values already in the design. Before
  adding a new value, check whether an existing one meets the WCA bound, and use it if it does. Report the BOM line
  count and any value that is unique to one location, with the reason.
- **0603 or larger** for passives unless the spec says otherwise (easy to rework).
- **Decoupling:** 100 nF at every IC power pin, placed at the pin, plus bulk capacitance where the rail or the
  datasheet needs it.
- **SPI series termination at each driving end:** MISO at the peripheral's pin; SCLK, MOSI and CS at the
  controller's pins. R_series = 50 ohm - R_out(driver), snapped to a standard value (prefer one already in the BOM).
  The trace after the resistor is a 50 ohm controlled-impedance line (e.g. R_out about 17 ohm -> 33 ohm). Take
  R_out from the driver's datasheet (VOH/VOL vs IOH/IOL, e.g. (VS - VOH)/IOH), worst case. This is not a DS equation;
  label it as such. If the datasheet gives no data, record it as an open item; never guess.
- **Schematics are well laid out, with attention to detail:** signal flow left to right, power at the top, ground at
  the bottom, decoupling drawn next to the pin it serves, related parts grouped, readable net names, no crossing
  clutter. Review the generated schematic, not just the build result.

## Library version pinning
- Zener deps: `pcb.toml` `[dependencies]`, one entry per component package, tool-managed. Never hand-edit versions;
  use `pcb sync`, `pcb list -m -u`, `pcb add -u`, or `pcb add <url>@<version>`.
- Python: `analysis/_zl.py` `ZL_VERSION`, checkout at `../zener-library` (or `$ZENER_LIBRARY`). Keep it equal to
  the Zener dep versions; bump both together. Never point it at the in-progress `../diodecomputer` tree for results
  that go in a report.
- Never put the library (submodule or copy) inside this repo: `pcb` fails on any nested `[workspace]` pcb.toml,
  hidden or git-ignored included.

## Zener rules
- Use the `zener-language` skill (`.claude/skills/zener-language/`) for syntax. Confirm any construct in the stdlib
  source (`pcb list -m -json @stdlib`) or `pcb <cmd> --help` before using it; there is no `pcb doc`.
- Import parts by full URL: `Module("github.com/kb1lqc/zener-library/components/<Mfr>/<Value>/<Value>.zen")`, then
  `pcb sync`. Relative paths that leave a package fail.
- Give every checked net a voltage with tolerance (`Power("V5", voltage="5V 5%")`). An undeclared voltage reports
  UNVERIFIED and fails `-Dwarnings`.
- Pass the board's `ambient` and `derate` down to every wrapper (and through every module). Never hard-code them.
  A `waive_<key>` needs a rationale with a cite and is listed in the report.
- Hierarchical blocks go in `modules/<Name>.zen`. Keep topology stable: configs change values and `dnp`, never add,
  remove or reconnect instances.
- Prefer stdlib generics for passives/connectors/test points unless a library series part is needed for analysis
  (e.g. precision resistors, the shunt).

## After every change
From the repo root, judging by exit code:
`pcb fmt` -> `pcb sync` (if imports changed) -> `pcb build -Dwarnings ina229.zen` -> any affected
`python3 -m analysis.<name>`. Delete `__pycache__/` afterwards. Report every UNVERIFIED, waiver and open item.
