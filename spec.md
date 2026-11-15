# ina229 Design Specification

## Purpose

A reusable **high-side** INA229 current, bus-voltage, power, energy and charge monitor block,
`INA229HighSideMonitor` (`modules/INA229HighSideMonitor.zen`), with the current-sense shunt on the block, read over
SPI. This board carries the first instance, on a 12-40 V high-side bus. Other boards drop the block onto a high-side
power path and change only its configs. Low-side sensing is out of scope (a separate block if ever needed).

Phase: schematic only. No PCB layout work until it is explicitly started.

## Requirements

Worst-case analysis: `analysis/ina229_block.py` -> `docs/design-analysis.md` and charts in `docs/design-analysis/` (all
min/max corners; every datasheet number from zener-library v0.3.0, `analysis/_zl.py`, with its cite).

**Operating conditions (first instance)**
- Bus: 12 V to 40 V DC, high-side sensing. Shunt in the positive rail.
- Current: 8 A full scale, bidirectional (designer decision 2026-11-15; was 5 A).
- Ambient: 0 to 70 degC (board `ambient` config). Derating factor `derate` = 1.0 (none).
- Logic supply: 3.3 V +/-3 %, one rail shared by the host and the INA229. A 3 % rail keeps logic levels within the
  VIO abs max of VS + 0.3 V when checked worst case (+/-5 % would exceed it by 30 mV).
- Accuracy: +/-1 % of reading at 8 A full scale, judged on the self-heated worst case (designer requirement 2026-11-15).
  Result: 0.626 % max (-0.6235 % to +0.6256 %). **PASS**, margin 0.374 % of reading (ESTIMATE: shunt self-heating,
  `docs/design-analysis.md` section 5).

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
| Shunt | Vishay WSK2512R0100BEA | 10 mohm, +/-0.1 % (code B), 1 W, 4-terminal Kelvin | Designer choice 2026-11-15, rated for 8 A full scale. Basis: best self-heated 8 A error of the candidates that pass +/-1 % (0.626 %, `docs/design-analysis.md` section 1.4). Availability **FAIR**: one distributor listing (LCSC, zero stock; snapshot 2026-11-15, `analysis/data/shunt_stock_2026-11-15_rerank.json`), OPEN ITEM. No GOOD fallback at 8 A: WSK2512R0100DEA (10 mohm, +/-0.5 % code D, same footprint, GOOD: 3 distributors) gives 1.029 % self-heated at 8 A (FAIL) |
| VS bypass | Murata GCM188R71H104KA57 | 100 nF X7R 50 V 0603, at pin VS | DS section 9 |
| MISO termination | Panasonic ERJ-3EKF | 33 ohm 0603, at the INA229 MISO pin | DESIGN CHOICE placeholder (see open items) |
| ALERT_N pull-up | Panasonic ERJ-3EKF | 10 kohm 0603 to V3V3, DNP-able (`alert_pullup`) | DS Fig 8-2 |

5 parts, 5 BOM lines, all 0603 or larger except the 2512 shunt. No input filter or input protection parts
(decision; see open items).

**Worst-case results (TA 0-70 degC, 8 A full scale; `docs/design-analysis.md`)**
- Shunt voltage at 8 A, self-heated worst case (ESTIMATE, section 1): 80.39 mV vs 163.84 mV ADC range (ADCRANGE 0):
  83.45 mV margin (50.9 %). Board `vsense_max` = 80.4 mV.
- Shunt power at 8 A, 70 degC: 0.642 W (0.6416 W) vs 1.0 W rating (35.8 % margin, section 2). Continuous current at
  70 degC: 9.99 A on the rating basis (ambient resistance), 9.97 A self-heated (ESTIMATE, section 2). Shunt element
  temperature at 8 A, 70 degC: 134.3 degC (ESTIMATE, section 3; theta_est 100 degC/W; rise 64.3 degC), 35.7 degC below
  the ASSUMED 170 degC element limit.
- Current error at 8 A, SELF-HEATED worst case (ESTIMATE): 0.626 % of reading (-0.6235 % to +0.6256 %), PASS vs
  +/-1 %, margin 0.374 % (ambient only: 0.400 %, reference). At 100 mA, SELF-HEATED worst case: 0.558 % of reading,
  report only, no limit set (ambient only: 0.558 %). Plot: `docs/design-analysis/current_error.png`.
- Power error at 8 A (V x I, ambient only, report only; section 8): -0.5683 % to +0.5707 % at 12 V, -0.5474 % to
  +0.5496 % at 40 V.
- Bus voltage error (unchanged): +/-20.2 mV at 12 V, +/-28.6 mV at 18 V, +/-42.6 mV at 28 V, +/-53.8 mV at 36 V,
  +/-59.4 mV at 40 V (0.15 to 0.17 %).
- Firmware constants: CURRENT_LSB = 1.52588e-5 A/LSB (15.2588 uA; 8 A / 2^19), SHUNT_CAL = 2000 (ADCRANGE = 0,
  R_NOM 10 mohm; raw 2000.0, exact integer, no rounding). Scaling: 100 A/V (A/V = 1/R with R = 10 mohm, unchanged);
  shunt-voltage LSB 312.5 nV (ADCRANGE 0).

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
- zener-library packages: INA229AIDGS 0.1.0, WSK2512 0.2.0, GCM188 0.2.0, ERJ-3EKF 0.3.0 (`pcb.toml`, tool-managed);
  Python analysis pinned to v0.3.0. All symbols are KiCad 10 format, required by `pcb apply`.

## Open Questions

- Connectors and host interface (SPI header, power-path terminals, current rating of the copper and connectors).
- Board size, mounting and enclosure.
- Layer stackup and trace geometry for the 50 ohm SPI lines (4 layers assumed by the board default).

## Open items

- **Hard-short differential overstress:** a load-side short on a 40 V bus can put 40 V across IN+/IN-, equal to the
  +/-40 V abs max (zero margin); inductive kickback when the short clears can exceed it (DS 8.1.4). No input
  protection is fitted, by decision. If revisited: bidirectional TVS across the inputs plus 2 x 10 ohm series
  resistors (DS 8.1.4). This is why the bus is limited to 40 V for now.
- **MISO driver output impedance unknown:** the DS bounds it only as R_out <= 400 ohm (VOH >= VS - 0.4 V at 1 mA).
  33 ohm is a placeholder until the TI IBIS model or a TDR measurement gives R_out.
- **Shunt availability:** availability of WSK2512R0100BEA is FAIR (single distributor listing, LCSC, zero stock; snapshot
  2026-11-15): confirm long-term availability and lead time with Vishay or the distributors before release. No GOOD
  fallback exists at 8 A: WSK2512R0100DEA (10 mohm +/-0.5 %, same footprint, GOOD) gives 1.029 % self-heated (FAIL). The
  shunt element 170 degC limit is an assumption (the element temperature estimate depends on it). Lifecycle and PCN not
  verified.
- **Current path rating:** BUS_IN/BUS_OUT copper, shunt pads and connectors must be rated for 8 A continuous plus margin
  (layout not started).
- **Maximum current study (`docs/design-analysis.md`, MF.0 to MF.9):** for WSK2512R0100BEA the highest passing integer is
  9 A (0.686 %, 0.814 W, element 151.4 degC: 18.6 degC to the assumed limit and 0.186 W to the rating, thin margins);
  10 A fails. Across the WSK2512 and WSK2512-LOW library the GOOD-rated maximum is 7 A (WSK2512R0100DEA, 0.976 %), the
  FAIR-rated maximum is 10 A (WSK25125L000DEA, 5 mohm code D, one distributor, 0.980 %), and no design passes at 11 A or
  above (scan to 14 A). 8 A is the designer's choice on the FAIR-rated 10 mohm code B part.
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
