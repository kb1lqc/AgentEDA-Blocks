# INA229HighSideMonitor block specification

## Purpose

A reusable **high-side** INA229 current, bus-voltage, power, energy and charge monitor block,
`INA229HighSideMonitor` (`blocks/INA229HighSideMonitor/INA229HighSideMonitor.zen`), with the current-sense shunt on the block,
read over SPI. The bench board (`benches/INA229HighSideMonitor/bench.zen`) carries the reference instance, on a 12-40 V
high-side bus; its values are the defaults in `benches/INA229HighSideMonitor/params.json`. Other boards drop the block onto a high-side
power path and change only its configs. Low-side sensing is out of scope (a separate block if ever needed).

Phase: schematic only. No PCB layout work until it is explicitly started.

## Requirements

Worst-case analysis: `blocks/INA229HighSideMonitor/analysis.py` (per instance: `--params`, `--out`) -> for the bench,
`benches/INA229HighSideMonitor/docs/design-analysis.md` and charts and `values.json` in `benches/INA229HighSideMonitor/docs/design-analysis/` (all
min/max corners; every datasheet number from zener-library v0.4.0, `analysis/_zl.py`, with its cite).

**Operating conditions (bench reference instance; a board sets its own per instance)**
- Bus: 12 V to 40 V DC, high-side sensing. Shunt in the positive rail.
- Current: 6 A full scale, bidirectional (designer choice 2026-11-15).
- Ambient: 0 to 70 degC (board `ambient` config). Derating factor `derate` = 1.0 (none).
- Logic supply: 3.3 V +/-3 %, one rail shared by the host and the INA229. A 3 % rail keeps logic levels within the
  VIO abs max of VS + 0.3 V when checked worst case (+/-5 % would exceed it by 30 mV).
- Accuracy: +/-1 % of reading at full scale, BOL, after one-point gain calibration at 6 A / 25 degC with a +/-0.1 %
  reference (designer requirement 2026-11-15), judged on the self-heated worst case.
  Result: -0.374 % to +0.375 % of reading (max 0.375 %). **PASS**, margin 0.625 % of reading (ESTIMATE: shunt
  self-heating, `benches/INA229HighSideMonitor/docs/design-analysis.md` section 5b).
- Production: one-point gain calibration required (at 6 A, 25 degC ambient, reference +/-0.1 % of reading). Without it the
  BOL error is 6.44 % of reading at 6 A (FAIL vs +/-1 %).

**Block definition: `INA229HighSideMonitor`**
- High-side only. The INA229 VBUS (bus voltage) input is wired inside the block to `BUS_OUT`, the low (load) side of
  the shunt, at the current terminal rather than a Kelvin tap (DS Fig 10-1 note 1, load power). The block has no
  VBUS port.
- Kelvin sense: IN+ and IN- connect to the shunt's sense terminals (E1, E2), so copper outside the shunt is not
  measured.

| Port | Type | Role |
|---|---|---|
| `BUS_IN` | Net | High-side power path in (source side of the shunt) |
| `BUS_OUT` | Net | High-side power path out (load side); positive current flows `BUS_IN` -> `BUS_OUT`; also the VBUS sense point |
| `V3V3` | Net (local in block; board V3V3 Power net 3.3 V +/-3 %); block accepts 2.7-5.5 V | Logic supply; the name carries the intended 3.3 V rail, the range is the INA229 rec-op |
| `GND` | Net (local in block, board GND) | Ground |
| `SPI` | stdlib `Spi` (CS, MISO, MOSI, CLK) | SPI peripheral; CS active low |
| `ALERT_N` | Net | Alert output, active low, open drain |

| Config | Set by board | Meaning |
|---|---|---|
| `rshunt` | yes (no default) | Shunt resistance, from the WCA |
| `rshunt_tol` | yes | WSK2512 tolerance code (B, D, F) |
| `vsense_max` | yes | Worst-case shunt voltage from the WCA; declares the shunt's voltage across for the rating check |
| `alert_pullup` | optional (default fitted) | Fit the on-block 10 kohm ALERT_N pull-up |
| `ambient`, `derate` | yes | Passed to every library part's checks |

**Block design (first instance)**
| Function | Part | Value / choice | Basis |
|---|---|---|---|
| Monitor | TI INA229AIDGS | ADCRANGE = 0 (+/-163.84 mV) | DS p.5 |
| Shunt | Vishay WSK2512R0100DEA | 10 mohm, +/-0.5 % (code D), 1 W, 4-terminal Kelvin | designer choice 2026-11-15; meets +/-1 % at 6 A only after one-point calibration (calibrated 0.375 %, uncalibrated 6.44 %; `benches/INA229HighSideMonitor/docs/design-analysis.md` section 5b) |
| VS bypass | Murata GCM188R71H104KA57 | 100 nF X7R 50 V 0603, at pin VS | DS section 9 |
| MISO termination | Panasonic ERJ-3EKF | 33 ohm 0603, at the INA229 MISO pin | DESIGN CHOICE placeholder (see open items) |
| ALERT_N pull-up | Panasonic ERJ-3EKF | 10 kohm 0603 to V3V3, DNP-able (`alert_pullup`) | DS Fig 8-2 |

5 parts, 5 BOM lines, all 0603 or larger except the 2512 shunt. No input filter or input protection parts
(decision; see open items).

**Worst-case results (TA 0-70 degC, 6 A full scale; `benches/INA229HighSideMonitor/docs/design-analysis.md`, numbers in `benches/INA229HighSideMonitor/docs/design-analysis/values.json`)**
- Shunt voltage at 6 A, self-heated worst case (ESTIMATE, section 1): 60.47 mV vs 163.84 mV ADC range (ADCRANGE 0):
  103.4 mV headroom (63.1 %). Board `vsense_max` = 60.5 mV (rounded up to 0.1 mV).
- Shunt power at 6 A, 70 degC: 0.362 W (0.3624 W) vs 1.0 W rating (63.8 % margin, section 2).
- Shunt element temperature at 6 A, 70 degC: 106.3 degC (ESTIMATE, section 3; theta_est 100 degC/W; rise 36.3 degC),
  63.7 degC below the ASSUMED 170 degC element limit.
- Current error at 6 A, BOL calibrated, self-heated worst case (judged, ESTIMATE): max 0.375 % of reading, PASS vs
  +/-1 %, margin 0.625 %. BOL uncalibrated at 6 A: max 6.44 % (FAIL, reported; the solder-heat test limit alone is
  +/-5.5 % of R). At 100 mA: calibrated max 0.590 %, uncalibrated max 6.46 % (report only, no limit set). Reference
  (pre-solder, self-heated): 0.930 %; ambient only: 0.802 %. Stack-up and plots: `benches/INA229HighSideMonitor/docs/design-analysis.md` section 5b,
  `benches/INA229HighSideMonitor/docs/design-analysis/error_stackup.png`, `benches/INA229HighSideMonitor/docs/design-analysis/current_error.png`.
- Power error at 6 A (V x I, ambient only, report only; section 8): -0.967 % to +0.973 % at 12 V, -0.946 % to +0.952 %
  at 40 V.
- Bus voltage error (incl. 1 LSB): +/-20.4 mV at 12 V, +/-28.8 mV at 18 V, +/-42.8 mV at 28 V, +/-54.0 mV at 36 V,
  +/-59.6 mV at 40 V (0.15 to 0.17 % of voltage).
- Firmware constants: CURRENT_LSB = 1.14441e-5 A/LSB (11.4441 uA; 6 A / 2^19), SHUNT_CAL = 1500 (ADCRANGE 0, R_NOM
  10 mohm; raw 1500.000000, exact integer, no rounding). Scaling: 100 A/V (A/V = 1/R with R = 10 mohm).
**Interfaces (board level)**
- SPI: 50 ohm controlled-impedance lines, each with a source series termination at its driver (R_series + driver
  R_out about 50 ohm). On the block, MISO only; SCLK, MOSI and CS are terminated at the host.
- ALERT_N: open drain, active low; on-block 10 kohm pull-up, or DNP when the host or a shared wired-OR pull-up is
  used.
- Board nets: `VBUS_IN` -> `BUS_IN`, `VBUS_LOAD` -> `BUS_OUT`, `V3V3`, `GND`, `SPI_CS`, `SPI_MOSI`, `SPI_CLK`,
  `SPI_MISO`, `ALERT_N`.

**Schematic deliverable**
- The block is a reusable hierarchical sub-sheet (`Project()` in the module; child sheet `eda/INA.kicad_sch` for
  instance `INA`), drawn to the house style: `docs/style-notes.md` and the zener-library `schematic-style` skill.
- Title block filled (title, revision, date, company, author, repo + commit, sheet description).
- Checks: `pcb apply schematic` twice (second unchanged), `pcb build -Dwarnings` exit 0, KiCad ERC reviewed.

**Library dependency**
- zener-library packages: INA229AIDGS 0.2.0, WSK2512 0.2.0, GCM188 0.2.0, ERJ-3EKF 0.3.0 (`pcb.toml`, tool-managed);
  Python analysis pinned to v0.4.0. All symbols are KiCad 10 format, required by `pcb apply`.

## Open Questions

- Connectors and host interface (SPI header, power-path terminals, current rating of the copper and connectors).
- Board size, mounting and enclosure.
- Layer stackup and trace geometry for the 50 ohm SPI lines (4 layers assumed by the board default).

## Open items

- **End-of-life (EOL) drift not analysed (out of scope for now):** load life (+/-1 % + 0.0005 ohm), thermal shock,
  high-temperature exposure and bias humidity (test limits +/-(0.5-1 % + 0.0005 ohm), DS p.3) are not in the BOL result.
  The INA229 has no long-term drift specification.
- **Calibration assumptions:** the calibration ambient (25 degC) and the reference accuracy (+/-0.1 % of reading) are
  assumptions / design inputs. The production calibration procedure (who, where, reference standard) is not defined.
- **Hard-short differential overstress:** a load-side short on a 40 V bus can put 40 V across IN+/IN-, equal to the
  +/-40 V abs max (zero margin); inductive kickback when the short clears can exceed it (DS 8.1.4). No input
  protection is fitted, by decision. If revisited: bidirectional TVS across the inputs plus 2 x 10 ohm series
  resistors (DS 8.1.4). This is why the bus is limited to 40 V for now.
- **MISO driver output impedance unknown:** the DS bounds it only as R_out <= 400 ohm (VOH >= VS - 0.4 V at 1 mA).
  33 ohm is a placeholder until the TI IBIS model or a TDR measurement gives R_out.
- **Current path rating:** BUS_IN/BUS_OUT copper, shunt pads and connectors must be rated for 6 A continuous plus margin
  (layout not started).
- **Typical-only or partial data:** VBUS PSRR is typical-only (shown, not bounded); shunt gain error is specified at
  VCM = 24 V only; the WSK2512 TCR reference temperature (25 degC) is assumed; INA229 gain-drift rows carry the
  25 degC table header (DS ambiguity, from the library).
- **Library check modelling:** the zener-library wrappers check voltage across a part from the two nets' ranges
  taken independently. Fixed for series resistors in zener-library v0.2.0 (`v_across`); the block sets it for the
  shunt (`vsense_max`) and R_MISO (1.35 V). The INA229 `vin_diff_abs` check has the same limitation, which is why a
  bus above 40 V needs another library change.
- **Schematic toolchain limits (pcb 0.4.102):**
  - Power-path nets: declared as `Power`, `VBUS_IN`/`VBUS_LOAD` appeared inside the sub-sheet as global power
    symbols with board net names, and ERC reported the sheet pins unmatched. Resolved: they are plain nets with a
    12-40 V range, drawn as labels, and the sub-sheet uses `BUS_IN`/`BUS_OUT` hierarchical labels.
  - SPI as a harness: a KiCad bus-group sheet pin is rewritten by `pcb apply` into four separate `SPI.*` pins
    (with dangling breakouts), so SPI stays as separate sheet pins for now.
  - Internal labels inside the sub-sheet keep the instance prefix (`INA.SENSE_P`).
