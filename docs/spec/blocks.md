# Block packages: layout, ports, versioning, analysis, reuse

Contract for every block in this repository. Judgment rules are in the `reusable-blocks` skill.

## Layout
```
pcb.toml                          [workspace] repository = "github.com/<owner>/<blocks repo>"
blocks/<Block>/                   one package per block (`pcb new package blocks/<Block>`)
  pcb.toml                        dependencies on library parts (tool-managed)
  <Block>.zen                     the module: ports, configs, Project(name="<Block>", path=..., layout=False)
  spec.md                         purpose, operating conditions, ports, configs with valid ranges, part table with
                                  basis, worst-case results, interfaces, limits and why, open items
  analysis.py                     parameterised worst-case analysis
benches/<Block>/bench.zen         bench board: declared nets with voltages, one instance with reference configs,
                                  Board(path="eda"); imports the block by full URL (resolved locally)
benches/<Block>/params.json       the reference instance's analysis inputs, each with a basis
benches/<Block>/eda/              bench root sheet + arranged reference sheet <instance>.kicad_sch
benches/<Block>/docs/             generated: design-analysis.md and .pdf, charts, values.json, <bench name>.pdf
benches/<Block>/notes_rules.py    note rules for the parent sheet (read by tools/schematic/update_notes.py)
analysis/                         shared helpers (_zl.py loads the library, report writer); no block numbers
tools/                            git/pre-commit; schematic/ (tools/schematic/README.md)
```
- A block package must not reach outside its folder with relative paths; shared Zener code goes in its own package, imported by URL.
- Benches are where gates run: `pcb build -Dwarnings`, `tools/schematic/check_sync.sh`, ERC, renders.
- Bench or board `pcb.toml` has no `[board]` section in the workspace root.
- Analysis imports the library with `from analysis import _zl` then `from lib import load, Param, wca, table`.

## Zener rules
- Import parts by full URL: `Module("github.com/kb1lqc/zener-library/components/<Mfr>/<Value>/<Value>.zen")`.
- `Power()` names carry the voltage and no leading digit (`V3V3`); `tools/check_env.py` enforces it.
- Give every checked net a voltage with tolerance (`Power("V5", voltage="5V 5%")`); an undeclared voltage reports
  UNVERIFIED and fails `-Dwarnings`.
- Pass `ambient` and `derate` down to every wrapper and through every block; never hard-code them. A `waive_<key>`
  needs a rationale with a cite and is listed in the report.
- Prefer stdlib generics for passives, connectors and test points unless analysis needs a library series part
  (precision resistors, the shunt).
- Current-sense blocks take bus-voltage sense from the shunt low side (load-side terminal), wired inside the block, not a port.
- Confirm any Zener construct in the stdlib source (`pcb list -m -json @stdlib`) or `pcb <cmd> --help` before use.

## Standard ports
| Role | Name | Net type into the block |
|---|---|---|
| Power path in / out (through a shunt, switch, fuse) | `BUS_IN` / `BUS_OUT` | plain `Net` with declared voltage (drawn as labels; a `Power` into a sub-sheet breaks the sheet pin) |
| Rail feeding parts | `V3V3`, `V5`, `V12` | plain `Net` port; `Power(...)` on the board; local inside the sheet |
| Ground | `GND` | plain `Net` port, cast to `Ground` inside |
| Enable / control in | `EN` (active high) or `<NAME>_N` | `Net` with logic voltage |
| Status out (open drain) | `FAULT_N`, `ALERT_N`, `PGOOD` (high = good), `PGOOD_N` (low = good) | `Net` with logic voltage |
| Digital interface | stdlib interface (`Spi`, `I2c`) named by role (`SPI`) | interface |
| Analog output | role name stating what it represents (`IMON`, `VMON`) | `Net` with voltage range |

Power paths through a block are nets, not rail symbols. Rails that only feed parts are power symbols on the board and hierarchical labels inside the block.

## Sheet-symbol geometry (parent sheet)
- Power-path ports sit at the same height on opposite edges (`BUS_IN` left, `BUS_OUT` right), at the same offset from the top on every block, so chained blocks join with straight wires.
- Interface pins group below the path on the right edge; ground on the bottom edge; rails on the top edge.
- A logic rail that only feeds pull-ups or bias (`V3V3` on a switch block) enters at the lower left: a short wire left, the power symbol pointing up at its left end, no text over the sheet symbol. A rail that powers an IC in the block stays on the top edge.

## Versioning
- Release with tag `blocks/<Block>/vX.Y.Z` (`pcb publish`, or a manual tag if it needs a TTY).
- A board pins each block with `pcb add github.com/<owner>/<repo>/blocks/<Block>@<version>`; never hand-edit versions. In a block folder, use `pcb add <url>@<version>`; a plain `pcb sync` picks old versions.
- Patch: notes, docs, analysis fixes that change no result direction. Minor: new configs with backward-compatible defaults, new analysis outputs. Major: any port, topology or default change.

## Analysis per instance
- `analysis.py` exposes `analyze(params) -> values` (instance configs and operating conditions in, a dict out) plus a writer that renders one instance's datasheet section and charts. Datasheet equations come only from the library `calc.py`; the block adds the composition (self-heating, stack-ups, calibration) labelled "not a DS equation".
- CLI: `python3 blocks/<Block>/analysis.py --params <json> --out <dir> --instance <NAME>`; defaults are the bench `params.json` and `docs/`. Instance inputs live in params, never as constants in the script.
- A board calls it once per instance from a checkout pinned at the block's tag, writing one values file and one datasheet section per instance, plus board-level results spanning blocks (total dissipation, rail budget, a switch's Rds(on) seen by a monitor's bus voltage).
- Values files are keyed by instance name; the notes script fills each instance's notes on the parent sheet.
- Design-value change loop: edit analysis/`.zen` configs (never the sheet) -> analysis -> `update_notes` script (run twice; the second run changes nothing) -> apply twice, netlist diff, build, ERC, PDF export, sync check. Grep `.zen` comments, spec and README for the old numbers and replace copies with cites. If a note's wording changes, edit it once in the `.kicad_sch` keeping the line count, then extend the rule.

## Schematic reuse
`pcb` generates one unarranged child sheet per instance in the board's own `eda/`; a module's `Project(path=)` is not a template. Each board stamps the block's reference sheet:
1. `pcb apply schematic --no-open` once, so the root has sheet symbols and refdes/UUIDs.
2. `tools/schematic/stamp_block.py --ref <ref sheet> --ref-instance <ref name> --board <board.zen> --instance <NAME>...` (see `--help`). It refuses to overwrite hand edits without `--force`.
3. `pcb apply schematic --no-open` twice (second unchanged), netlist diff against the fresh generation, ERC.

Known limits: a net shared between instances may change from a global-style name (`VBUS_MID`) to the root-local `/VBUS_MID` (same nodes); existing sheet pins are not moved and transplanted pins get no root wiring; untested for blocks with nested sub-sheets.
- Each bench commits a PDF of all its sheets (`docs/<bench name>.pdf`, named after the Board) so a reader can review without KiCad.
- Reference-sheet notes hold for every instance (limits, reasons, roles). Instance numbers (full scale, error, scaling, dissipation) go in the DESIGN VALUES box beside the sheet symbol, filled from the instance's values by the notes script.
- The top sheet is a block diagram: blocks left to right along the power path, chained through `BUS_IN`/`BUS_OUT`, interfaces right, rails up, ground down.

## New block checklist
1. Spec first: purpose, ports from the table, configs and valid ranges, requirements to bound.
2. Library parts exist (or are added to the library and released first).
3. Module plus bench; `pcb build -Dwarnings` exit 0 on the bench.
4. `analysis.py` with `analyze(params)`; bench datasheet generated; every requirement judged with margin.
5. Reference sheet per `docs/briefs/schematic-capture.md` in the library.
6. Release the tag; record standard-port additions here.

## Block spec and report contents
The spec and the worst-case report are the single source for every schematic number. They state, per block: input and output voltage ranges, rail voltages and tolerances, design maximum current, worst-case full-scale and low-end error, scaling (A/V, A/count, V/count, LSB), headroom to ADC or rating limits, dissipation vs rating, and the reason for each hard limit. Missing values are produced by the analysis before annotation. Report format: `analysis-report.md`.
