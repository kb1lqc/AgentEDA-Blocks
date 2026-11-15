# Design and schematic style notes

Running notes on how this designer works, captured while reviewing schematics. Draft source material for future
skill documents. Each note says where it came from: **stated** (said directly) or **observed** (inferred from a
review; confirm before promoting it to a rule).

## Workflow
- Schematic first. No layout work until it is explicitly started. (stated, 2026-11-15)
- Worst-case analysis bounds every requirement; typical values are never bounds. (stated)
- Prefer stocked parts. When the WCA-optimal value is not stocked, rank the stocked alternatives by worst-case
  error and headroom, and present the trade-off. (observed: chose the stocked 25 mohm +/-0.5 % shunt over the
  unstocked 32 mohm +/-0.1 % one, accepting the error doubling to 0.80 %)
- Build reusable blocks as modules, with the sense element on the block (shunt inside). (stated in planning)

## Parts and BOM
- Fewest components. First pass minimizes BOM line items and reuses R/C values. (stated)
- 0603 or larger unless specified (reworkable). (stated)
- 100 nF decoupling at every IC power pin. (stated)
- SPI: series termination at each driving end, sized so R_series + R_out of the driver is about 50 ohm, with
  50 ohm controlled-impedance lines after the resistor. MISO at the peripheral; SCLK, MOSI and CS at the
  controller. (stated)
- No input filter or protection parts on the INA229 unless needed; limit the operating range instead (bus capped
  at 40 V so the +/-40 V differential abs max holds). (observed, planning decisions)
- Logic rail tolerance chosen so I/O abs-max checks pass worst case (3.3 V +/-3 %, host and IC on one rail).
  (observed)

## Schematic presentation
- Schematics well laid out with attention to detail. (stated)
- Proposed defaults, to confirm in review: signal flow left to right, power at the top, ground at the bottom,
  decoupling drawn next to the pin it serves, related parts grouped, readable net names. (proposed, not yet
  confirmed)

- Board-level nets leaving a sheet use LOCAL labels, not global labels. KiCad ERC then flags a local label as
  dangling when its sheet has no component pin and the net has several pins inside a sub-sheet; those errors are
  accepted until the board's connectors/host exist. (stated, 2026-11-15)
- When a pinout puts a rail pin below the ground pin, rotating the IC 180 degrees (instead of mirroring) can put
  the rail pin above ground so both run straight up/down with no detour. (observed: accepted in review)
- No text overlaps any wire: this includes port (hierarchical label) text, global net label text and sheet pin
  text, as well as references and values. (stated, 2026-11-15)
- Every net label sits on a wire, with the wire long enough that the label fits along it. No labels floating at a
  bare pin end. (stated, 2026-11-15)
- Components flow left to right along the main signal or power path. Current-sense example: the high-side power
  enters from the left, passes through the shunt, and exits on the right; the sense IC sits below the shunt, with
  the series resistors and the differential capacitor of the input network running down from the shunt taps to
  the IC, which is below all of them. (stated, 2026-11-15)
- Reusable module names state their topology or limits when they are not general (`INA229HighSideMonitor`, not
  `INA229Monitor`, for a block that only supports high-side sensing). (stated, 2026-11-15)
- Port names on blocks: name the node by role (`BUS_IN` / `BUS_OUT` for a high-side power path, not `I_IN`);
  supply ports keep their intended voltage in the name (`V3V3`, "3V3 style"; Zener names cannot start with a
  digit); active-low signals end in `_N` (`ALERT_N`). (stated / accepted, 2026-11-15)
- Bus-voltage sense on a current-sense block comes from the low side of the shunt (the load-side current
  terminal), not a Kelvin tap, and is wired inside the block rather than exposed as a port. (stated, 2026-11-15)
- Signals such as SPI: no per-signal prefix preferred, but carried through the hierarchy as a harness (bus group).
  (stated, 2026-11-15)
- Power-path nets that pass into a reusable sub-sheet (a high-side bus through a shunt) are plain nets with a
  declared voltage, drawn as labelled wires; they become hierarchical labels named after the module's ports, so
  the reusable sheet carries no board net names. Rails that feed parts (V3V3) and ground stay power symbols.
  (stated, 2026-11-15)
- When a pinout forces the rail and ground wires to cross (INA229: VS sits below GND), route the rail wire around
  so the rail symbol still points up and ground still points down. Do not rotate the symbols, and do not cross.
  (stated, 2026-11-15)
- When two connections would cross (shunt SENSE_P/SENSE_N taps vs IN+/IN- order), draw both nets as labelled wire
  stubs at each end, symmetrically, with no wire between. (stated, 2026-11-15)
- Toolchain: sub-sheets work in pcb 0.4.102 with `Project(name=..., path=..., layout=False)` in the module and a
  fresh schematic generation (an earlier probe wrongly concluded flat-only; the boxed-flat fallback is no longer
  needed). (verified, 2026-11-15)
- Module-internal nets keep a short instance prefix (`INA.SENSE_P`) instead of being exposed as module ports; the
  prefix also prevents collisions between instances. Board-level nets stay plain. (stated, 2026-11-15)
- Each reusable module is a hierarchical sub-sheet: the parent shows a sheet symbol with the module's ports, and
  the circuit lives on its own page, reused unchanged on every board. (stated, 2026-11-15)
- Net names are plain, with no instance prefix (`SENSE_P`, not `MONITOR.SENSE_P`); add a prefix only when two
  instances would collide. (stated, 2026-11-15)
- Rails are drawn as power symbols pointing up at the top of each part, GND symbols pointing down at the bottom;
  no rail wires running across the sheet. (stated, 2026-11-15)
- Title block carries title, revision, date, company and author, repo URL plus git short hash, and a one-line
  description of what the sheet contains. (stated, 2026-11-15)

## Open questions to ask during review
- Active-low naming (suffix `_N`, `#`, or overbar) and case.
- Reference designator style and the value text shown on parts (MPN vs value, e.g. "100nF 50V X7R").
- Global vs local vs hierarchical labels for signals leaving a sheet.
- Sheet size, grid, text sizes.
