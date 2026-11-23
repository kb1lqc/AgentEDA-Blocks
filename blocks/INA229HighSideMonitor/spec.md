# INA229HighSideMonitor block specification

## Purpose

A reusable **high-side** INA229 current, bus-voltage, power, energy and charge monitor block,
`INA229HighSideMonitor` (`blocks/INA229HighSideMonitor/INA229HighSideMonitor.zen`), with the current-sense shunt on the block,
read over SPI. The bench board (`benches/INA229HighSideMonitor/bench.zen`) carries the reference instance, on a 12-40 V
high-side bus; its design values are its configs (`blocks/INA229HighSideMonitor/reference.zen`, the bench nets and configs),
and `benches/INA229HighSideMonitor/params.json` holds only analysis inputs (binding: `docs/spec/blocks.md`). Other boards
drop the block onto a high-side power path and change only its configs. Low-side sensing is out of scope (a separate block if ever needed).

Phase: schematic only. No PCB layout work until it is explicitly started.

## Requirements

Worst-case analysis: `blocks/INA229HighSideMonitor/analysis.py` (per instance: `--params`, `--out`) -> for the bench,
`benches/INA229HighSideMonitor/docs/design-analysis.md` and charts and `values.json` in `benches/INA229HighSideMonitor/docs/design-analysis/` (all
min/max corners; every datasheet number from the zener-library release pinned in `analysis/_zl.py`, with its cite).
Results, margins and their status: the generated account below.

**Operating conditions (bench reference instance; a board sets its own per instance)**
- Bus: 12 V to 40 V DC, high-side sensing. Shunt in the positive rail.
- Current: 6 A full scale, bidirectional (designer choice 2026-11-15).
- Ambient: 0 to 70 degC (board `ambient` config). Derating factor `derate` = 1.0 (none).
- Logic supply: 3.3 V +/-3 %, one rail shared by the host and the INA229. A 3 % rail keeps logic levels within the
  VIO abs max of VS + 0.3 V when checked worst case (+/-5 % would exceed it by 30 mV).
- Accuracy: +/-1 % of reading at full scale, BOL, after one-point gain calibration at 6 A / 25 degC with a +/-0.1 %
  reference (designer requirement 2026-11-15), judged on the self-heated worst case.
- Production: one-point gain calibration required (at 6 A, 25 degC ambient, reference +/-0.1 % of reading): the
  uncalibrated BOL error fails the accuracy requirement (account below, report section 5b).

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
| Shunt | Vishay WSK2512R0100DEA | 10 mohm, code D, 4-terminal Kelvin (configs `rshunt`, `rshunt_tol`) | designer choice 2026-11-15; meets the accuracy requirement only after one-point calibration (account below, report section 5b) |
| VS bypass | Murata GCM188R71H104KA57 | 100 nF X7R 50 V 0603, at pin VS | DS section 9 |
| MISO termination | Panasonic ERJ-3EKF | 33 ohm 0603, at the INA229 MISO pin | DESIGN CHOICE placeholder (see open items) |
| ALERT_N pull-up | Panasonic ERJ-3EKF | 10 kohm 0603 to V3V3, DNP-able (`alert_pullup`) | DS Fig 8-2 |

5 parts, 5 BOM lines, all 0603 or larger except the 2512 shunt. No input filter or input protection parts
(decision; see open items).

**Firmware constants**: CURRENT_LSB and SHUNT_CAL for the instance are in report section 4 (`values.json` keys
`current_lsb_ua`, `shunt_cal`, `a_per_v`).

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
- zener-library packages INA229AIDGS, WSK2512, GCM188, ERJ-3EKF (versions in `pcb.toml`, tool-managed); the Python
  analysis uses the release `analysis/_zl.py` pins (the account names it). All symbols are KiCad 10 format, required by
  `pcb apply`.

<!-- BEGIN GENERATED ACCOUNT: python3 tools/bind.py --write-spec; do not edit by hand -->

## Results account (generated)

Bench reference instance `INA`, from `benches/INA229HighSideMonitor/docs/design-analysis.md` (zener-library v0.7.0). Library rows read by the analysis: 53; checked by a person (current human stamp): **16 of 53**; the rest carry a machine `verified` stamp or none. The library does not attribute rows to individual results, so the count is per analysis, not per row below.

| Requirement | Result (worst case) | Limit | Margin | Status | Rests on (assumption / estimate) | Open items | Section |
|---|---|---|---|---|---|---|---|
| Sense voltage headroom (self-heated worst Vsense at 6 A) | 60.47 mV | 163.8 mV (ADCRANGE 0) | 103.4 mV (63.09 %) | PASS | ESTIMATE theta_est (shunt self-heating, 3.2); ASSUMPTION t_ref_c (shunt TCR reference) | OI-EOL | 1 |
| Shunt dissipation at 6 A, 70 degC (ambient rating) | 0.3624 W | 1 W (p70 x derate) | 0.6376 W (63.76 %) | PASS | ASSUMPTION t_ref_c (shunt TCR reference) | OI-PATH | 2 |
| Shunt element temperature at 6 A, 70 degC (theta_est 100 degC/W, self-consistent) | 106.3 degC | 170 degC (ASSUMED element limit) | 63.72 degC | ESTIMATE, PASS only if element limit = 170 degC (assumption) | ESTIMATE theta_est (shunt self-heating, 3.2); ASSUMPTION t_ref_c (shunt TCR reference); ASSUMPTION t_elem_limit_c (element limit) | OI-PATH | 3 |
| BOL calibrated error at 6 A (REQUIREMENT, judged) | -0.374 to +0.375 % (max 0.375 %) | +/-1 % of reading | +0.625 % of reading | PASS, BOL, self-heated, one-point cal at 6 A / 25 degC | ESTIMATE theta_est (shunt self-heating, 3.2); ASSUMPTION t_ref_c (shunt TCR reference); ASSUMPTION cal_ta_c (calibration ambient); design input cal_ref_pct (calibration reference) | OI-EOL, OI-CAL, OI-DATA | 5b |
| BOL uncalibrated error at 6 A (self-heated, solder-heat limit) | -6.424 to +6.444 % (max 6.444 %) | +/-1 % (not the judged case) | -5.444 % of reading | FAIL (reported; the judged case is calibrated) | ESTIMATE theta_est (shunt self-heating, 3.2); ASSUMPTION t_ref_c (shunt TCR reference); solder-heat qualification limit used as a bound | OI-EOL, OI-DATA | 5b |
| INA229 junction temperature at TA 70 degC (typical-based) | 71.04 degC (all power) | 150 degC (tj_abs) | 78.96 degC | ESTIMATE, PASS | ESTIMATE rtheta_ja (typical only) | none | 9 |
| Differential, hard load short (IN+ = 40 V, IN- = 0 V) | 40 to 40 V | `vin_diff_abs`: -40 to 40 V | 0 V | PASS, ZERO MARGIN (OPEN ITEM) | none | OI-SHORT | 11 |
| VIO high level, MOSI/MISO/SCLK/ALERT_N (3.3 V +/-3 %) | 3.399 to 3.399 V | `vio_abs`: -0.3 to 3.501 V | 0.102 V | PASS | none | none | 11 |
| MISO output source resistance (design placeholder) | 33 ohm | <= 400 ohm (`voh_25c`) | 367 ohm | PASS (placeholder, OPEN ITEM) | DESIGN CHOICE R_MISO placeholder | OI-MISO | 12 |

10 further results are REPORT ONLY, REFERENCE or CHECK (no limit set): see the report summary.

**Design values (read from the .zen through the bench netlist export)**

| Analysis input | Value | Source |
|---|---|---|
| `rshunt_ohm` | 0.01 | config `rshunt` = `10m` |
| `rshunt_tol_code` | 'D' | config `rshunt_tol` = `D` |
| `r_miso_ohm` | 33.0 | R_MISO MPN `ERJ3EKF33R0V` value (library) |
| `r_alert_pullup_ohm` | 10000.0 | R_ALERT MPN `ERJ3EKF1002V` value (library) |
| `vbus_lo_v` | 12.0 | net `VBUS_LOAD` (`12–40V (26V nom.)`) min |
| `vbus_hi_v` | 40.0 | net `VBUS_LOAD` (`12–40V (26V nom.)`) max |
| `vs_nom_v` | 3.3 | net `V3V3` (`3.3V 3%`) nominal |
| `vs_tol_frac` | 0.03 | net `V3V3` (`3.3V 3%`) tolerance |
| `ta_min_c` | 0.0 | bench config `ambient` = `0–70°C (35°C nom.)` min |
| `ta_max_c` | 70.0 | bench config `ambient` = `0–70°C (35°C nom.)` max |
| `derate` | 1.0 | bench config `derate` = `1.0` |

**Design outputs (analysis result -> .zen config; tools/bind.py fails when they differ)**

| Config | Value | Basis |
|---|---|---|
| `vsense_max` | 60.5mV | self-heated worst Vsense at I_MAX rounded up to 0.1 mV (section 1) |

Topology preconditions: 9 (`blocks/INA229HighSideMonitor/topology.toml`), checked against the bench netlist by tools/bind.py on every verify run.

<!-- END GENERATED ACCOUNT -->

## Open Questions

- Connectors and host interface (SPI header, power-path terminals, current rating of the copper and connectors).
- Board size, mounting and enclosure.
- Layer stackup and trace geometry for the 50 ohm SPI lines (4 layers assumed by the board default).

## Open items

- `OI-EOL` **End-of-life (EOL) drift not analysed (out of scope for now):** load life (+/-1 % + 0.0005 ohm), thermal shock,
  high-temperature exposure and bias humidity (test limits +/-(0.5-1 % + 0.0005 ohm), DS p.3) are not in the BOL result.
  The INA229 has no long-term drift specification.
- `OI-CAL` **Calibration assumptions:** the calibration ambient (25 degC) and the reference accuracy (+/-0.1 % of reading) are
  assumptions / design inputs. The production calibration procedure (who, where, reference standard) is not defined.
- `OI-SHORT` **Hard-short differential overstress:** a load-side short on a 40 V bus can put 40 V across IN+/IN-, equal to the
  +/-40 V abs max (zero margin); inductive kickback when the short clears can exceed it (DS 8.1.4). No input
  protection is fitted, by decision. If revisited: bidirectional TVS across the inputs plus 2 x 10 ohm series
  resistors (DS 8.1.4). This is why the bus is limited to 40 V for now.
- `OI-MISO` **MISO driver output impedance unknown:** the DS bounds it only as R_out <= 400 ohm (VOH >= VS - 0.4 V at 1 mA).
  33 ohm is a placeholder until the TI IBIS model or a TDR measurement gives R_out.
- `OI-PATH` **Current path rating:** BUS_IN/BUS_OUT copper, shunt pads and connectors must be rated for 6 A continuous plus margin
  (layout not started).
- `OI-DATA` **Typical-only or partial data:** VBUS PSRR is typical-only (shown, not bounded); shunt gain error is specified at
  VCM = 24 V only; the WSK2512 TCR reference temperature (25 degC) is assumed; INA229 gain-drift rows carry the
  25 degC table header (DS ambiguity, from the library).
- `OI-LIBCHECK` **Library check modelling:** the zener-library wrappers check voltage across a part from the two nets' ranges
  taken independently. Fixed for series resistors in zener-library (`v_across`); the block sets it for the
  shunt (`vsense_max`, a design output of the analysis, checked by tools/bind.py) and R_MISO (see `OI-VACROSS`). The INA229 `vin_diff_abs` check has the same limitation, which is why a
  bus above 40 V needs another library change.
- `OI-VACROSS` **R_MISO `v_across` is a hand calculation:** the edge-peak bound in the block .zen (R_MISO `v_across`) is not
  produced or checked by the analysis (unlike `vsense_max`). Move it into the analysis as a design output when the
  MISO R_out is known (`OI-MISO`).
- `OI-TOOLCHAIN` **Schematic toolchain limits (pcb 0.4.102):**
  - Power-path nets: declared as `Power`, `VBUS_IN`/`VBUS_LOAD` appeared inside the sub-sheet as global power
    symbols with board net names, and ERC reported the sheet pins unmatched. Resolved: they are plain nets with a
    12-40 V range, drawn as labels, and the sub-sheet uses `BUS_IN`/`BUS_OUT` hierarchical labels.
  - SPI as a harness: a KiCad bus-group sheet pin is rewritten by `pcb apply` into four separate `SPI.*` pins
    (with dangling breakouts), so SPI stays as separate sheet pins for now.
  - Internal labels inside the sub-sheet keep the instance prefix (`INA.SENSE_P`).
