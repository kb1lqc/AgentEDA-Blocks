# ina229 Design Specification

## Purpose

A reusable **high-side** INA229 current, bus-voltage, power, energy and charge monitor block,
`INA229HighSideMonitor` (`modules/INA229HighSideMonitor.zen`), with the current-sense shunt on the block, read over
SPI. This board carries the first instance, on a 12-40 V high-side bus. Other boards drop the block onto a high-side
power path and change only its configs. Low-side sensing is out of scope (a separate block if ever needed).

Phase: schematic only. No PCB layout work until it is explicitly started.

## Requirements

Worst-case analysis: `analysis/ina229_block.py` -> `analysis/out/ina229_block.md` (all min/max corners; every
datasheet number from zener-library v0.3.0, `analysis/_zl.py`, with its cite).

**Operating conditions (first instance)**
- Bus: 12 V to 40 V DC, high-side sensing. Shunt in the positive rail.
- Current: 5 A full scale, bidirectional.
- Ambient: 0 to 70 degC (board `ambient` config). Derating factor `derate` = 1.0 (none).
- Logic supply: 3.3 V +/-3 %, one rail shared by the host and the INA229. A 3 % rail keeps logic levels within the
  VIO abs max of VS + 0.3 V when checked worst case (+/-5 % would exceed it by 30 mV).

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
| `V3V3` | Power, 2.7-5.5 V accepted | Logic supply; the name carries the intended 3.3 V rail, the range is the INA229 rec-op |
| `GND` | Ground | Ground |
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
| Shunt | Vishay WSK2512R0250DEA | 25 mohm, +/-0.5 % (code D), 1 W, 4-terminal Kelvin | WCA: best stocked value (stock snapshot 2026-11-15, `analysis/out/shunt_stock_2026-11-15.json`); 32 mohm +/-0.1 % optimum not stocked |
| VS bypass | Murata GCM188R71H104KA57 | 100 nF X7R 50 V 0603, at pin VS | DS section 9 |
| MISO termination | Panasonic ERJ-3EKF | 33 ohm 0603, at the INA229 MISO pin | DESIGN CHOICE placeholder (see open items) |
| ALERT_N pull-up | Panasonic ERJ-3EKF | 10 kohm 0603 to V3V3, DNP-able (`alert_pullup`) | DS Fig 8-2 |

5 parts, 5 BOM lines, all 0603 or larger except the 2512 shunt. No input filter or input protection parts
(decision; see open items).

**Worst-case results (TA 0-70 degC)**
- Shunt voltage at 5 A: 125.8 mV max vs 163.84 mV ADC range (38.0 mV margin, 23.2 %).
- Shunt power at 5 A: 0.63 W vs 1.0 W rating at 70 degC (37 % margin).
- Current error: +/-40.0 mA (+/-0.80 % of reading) at 5 A; about +/-0.87 % at 100 mA. Shunt tolerance is the
  largest term. Plot: `analysis/out/ina229_current_error.png`.
- Bus voltage error: +/-20.2 mV at 12 V, +/-28.6 mV at 18 V, +/-42.6 mV at 28 V, +/-53.8 mV at 36 V, +/-59.4 mV
  at 40 V (0.15 to 0.17 %).
- Firmware constants: CURRENT_LSB = 9.5367e-6 A/LSB (5 A / 2^19), SHUNT_CAL = 3125 (ADCRANGE = 0, R_NOM 25 mohm).

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
- **Shunt accuracy:** no +/-0.1 % WSK2512 fitting 5 A was stocked (2026-11-15); the +/-0.5 % part doubles worst-case
  error to 0.80 %. Options: WSK2512R0200BEA (20 mohm +/-0.1 %, ~0.40 %) if purchasing can source it, or a firmware
  one-point gain calibration plus SHUNT_TEMPCO.
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
