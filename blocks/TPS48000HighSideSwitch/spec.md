# TPS48000HighSideSwitch block specification

## Purpose

A reusable **high-side** electronic load switch, `TPS48000HighSideSwitch`
(`blocks/TPS48000HighSideSwitch/TPS48000HighSideSwitch.zen`): one N-channel FET in the positive rail, driven by the
TI TPS48000QDGXQ1 (library `TexasInstruments/TPS48000QDGXQ1`), with slew-controlled turn-on inrush, external
high-side shunt sensing for short-circuit protection, latch-off on a short, and a power-good output. The bench board
(`benches/TPS48000HighSideSwitch/bench.zen`) carries the reference instance on a 12 V laboratory PSU; its values are
the defaults in `benches/TPS48000HighSideSwitch/params.json`. Other boards drop the block onto a high-side power path
and change only its configs. The block has no reverse-polarity blocking (designer choice; see open items). Low-side
switching is out of scope.

Phase: schematic only. No PCB layout work until it is explicitly started.

## Load turn-on rating (read before using this block)

Loads are expected to simply have power applied and start in whatever state they power up in; **no load is expected
to read PGOOD_N or wait for it** (designer, 2026-11-15). During turn-on the FET ramps the output with the 6.8 nF soft-start (ramp times `ramp_ms_12v` and `ramp_ms_40v`, design-analysis.md section 7; longer at higher bus voltage) and carries the load current while it still drops most of the bus voltage. What the load draws during that ramp is part of the rating:

| Load behaviour during turn-on | Examples | Bus rating |
|---|---|---|
| Current rises with voltage (resistive) | heaters, resistors, solenoids, stalled DC motor windings | **12 to 40 V** |
| Draws little until its own undervoltage lockout and soft-start finish | DC-DC converters, LED drivers, instrument supplies, boards with input capacitance up to the 200 uF reference | **12 to 40 V** |
| Draws full current (up to the 6 A design current) from near 0 V | electronic load in constant-current mode with a low Von; a converter with a very low lockout and no soft-start | **12 to 40 V** |

- Every load behaviour is rated across the full 12-40 V bus at the 6 A design current, with 200 uF and the 6.8 nF
  soft-start. The FET SOA check passes to 45 V bus (`turnon_max_pass_v`, constant 6 A, slow ramp; resistive loads
  to 79.5 V; design-analysis.md section 7), so the 40 V rating has +5.0 V margin (ESTIMATE). Above 6 A the rating
  does not hold: at 10 A the limit was 33 V (option study `hss_study5.py`; not covered by the analysis).
- A load drawing more than 6 A, at turn-on or after, is outside the rating (see the soft-short requirement).
- PGOOD_N is a status output for the host (FET fully on, protection armed), not a load interlock.

## Requirements

Worst-case analysis: `blocks/TPS48000HighSideSwitch/analysis.py` (per instance: `--params`, `--out`) -> for the bench,
`benches/TPS48000HighSideSwitch/docs/design-analysis.md` and charts and `values.json` in
`benches/TPS48000HighSideSwitch/docs/design-analysis/` (all min/max corners; every datasheet number from the zener-library
release that carries it, `analysis/_zl.py`, with its cite). Design values are the instance configs and parts in the
`.zen` (binding: `docs/spec/blocks.md`); `params.json` holds only analysis inputs. Results, margins and status: the
generated account below; every number lives in the report and `values.json`.

**Operating conditions (bench reference instance; a board sets its own per instance)**
- Bus: rated for 12 V to 40 V DC (designer decision 2026-11-15). Bench instance: 12 V nominal.
- Minimum BUS_IN in normal use: 10.8 V (12 V -10 %). Undervoltage (EN/UVLO) turn-on must be guaranteed at or below
  10.8 V worst case, with at least 0.2 V margin (designer: ample headroom for tolerance at 12 V nominal); turn-off is
  as close below that as the threshold hysteresis allows. Overvoltage (OV) must never trip below 13.2 V (12 V +10 %).
  Both are per-instance configs, set from these bus limits, not hard-coded.
- Design maximum continuous load current: 6 A (designer decision 2026-11-15; matches the INA229HighSideMonitor full
  scale, and at 10 A no bounded threshold row with a 1 W shunt meets the soft-short requirement, option study
  `hss_study5.py`).
- Ambient: 5 to 40 degC (laboratory; IEC 61010-1 normal environmental conditions, cl. 1.4.1). Board `ambient` config.
  Derating factor `derate` = 1.0 (none; designer decision 2026-11-15).
- Logic: control input EN is referenced to GND; the open-drain outputs are pulled up to V3V3 (board logic rail, as in
  `blocks/INA229HighSideMonitor/spec.md`).
- Load behaviour: see **Load turn-on rating** above.

**Protection and timing requirements**
- Soft shorts (load current above 6 A but below the worst-case trip maximum): every current up to the trip
  maximum must either trip (latch off) or be survived continuously by the FET (Tj) and the shunt (power rating) at
  40 degC ambient. Met by the analysis with RISCP 10.5 kohm and the 4 mohm shunt: shunt 0.937 W of 1 W (`soft_shunt_w`) and FET Tj
  111.6 degC (`soft_fet_tj_c`) at the highest non-tripping current 14.31 A (`trip_max_a`; design-analysis.md
  section 5); the shunt margin rests on an ESTIMATE (thermal resistance from the derating curve).
- Short-circuit trip: above the 6 A design current with margin. Trip window per the analysis below (`trip_window_a`: 6.78 to 14.31 A, +13.0 % over 6 A).
- Fault response: latch-off on a sustained short (latch reset by toggling EN low, DS p.19), not auto-retry.
- Fault-current design limit: prospective short-circuit current at BUS_IN <= 100 A. This is a system requirement on the
  PSU current limit or a per-channel fuse; a Magna-Power supply feeding several channels needs one (designer decision
  2026-11-15).
- Inrush: slew-controlled turn-on into a 200 uF reference load capacitance at 40 V and at 12 V (designer decision
  2026-11-15; the load for the inrush and SOA study).

**Block definition: `TPS48000HighSideSwitch`**
- High-side only. Power path: bus -> shunt -> FET drain; the FET source (SRC) is `BUS_OUT`. CS+ and VS are on the
  BUS_IN side of the shunt, CS- is at the FET drain (DS Fig 7-5, p.16; confirmed by the designer 2026-11-15).
- Gate wiring (DS Fig 7-4 p.15): PU -> R1 -> gate; PD tied directly to the gate (fast turn-off, DS p.16); gate -> R2 -> C1 -> GND, with R2 10 ohm in series with C1. C1 returns to GND, not SRC (designer correction D12).
- All support parts are inside the block: gate ramp, fault timer, ISCP setting, bootstrap, VS and SRC bypass,
  UVLO/OV divider, open-drain pull-ups (DNP-able).
- Topology is fixed. Configs change values and DNP only.

| Port | Type | Role |
|---|---|---|
| `BUS_IN` | Net | High-side power path in (upstream of the shunt); also CS+, VS and the top of the UVLO/OV ladder |
| `BUS_OUT` | Net | High-side power path out (FET source, SRC) |
| `EN` | Net (logic, 0-65 V tolerant; drives INP) | Enable, active high. Low turns the FET off; a low-high toggle clears a latched fault (DS p.19) |
| `FAULT_N` | Net (open drain) | Fault output (FLT): low on short-circuit, UVLO, OV or SCP diagnosis |
| `PGOOD_N` | Net (open drain) | Power-good, active low: low when the FET is fully enhanced (V(PD-SRC) > V(G_GOOD)). Standard port, `reusable-blocks` standard ports table (PGOOD high = good, PGOOD_N low = good) |
| `V3V3` | Net (local in block; board 3.3 V logic rail) | Pull-up rail for FAULT_N and PGOOD_N only. Any logic rail up to 15 V works (FLT/FLT_GD `rec_flt` 0-15 V, `abs_flt_gnd` 20 V); named for the house 3.3 V rail. Unconnected is allowed when both pull-ups are DNP (host pulls up). EN does not use it (accepts 3.3 V or 5 V logic, V(INP_H) max 2 V) |
| `GND` | Net (local in block, board GND) | Ground |

| Config | Set by board | Meaning |
|---|---|---|
| `rshunt` | yes (no default) | Shunt resistance, from the WCA |
| `rshunt_tol` | yes | WSK2512-LOW tolerance code (D, F) |
| `riscp` | yes | ISCP resistor to GND (sets V(SCP) through the library's `vscp_from_riscp`) |
| `ctmr` | yes | Fault-timer capacitor, TMR to GND |
| `rtmr` | yes | Latch-off resistor across CTMR (DS p.19) |
| `rtmr_fitted` | optional (default fitted) | DNP of RTMR: unfitted gives auto-retry instead of latch-off (changes the fault behaviour, not topology) |
| `rgate` | yes | Gate ramp resistor R1, PU to the gate |
| `cgate` | yes | Gate ramp capacitor C1, gate to GND (DS Fig 7-4 p.15) |
| `r_damp` | yes (default 10 ohm, fitted) | Damping resistor R2 in series with `cgate` |
| `r_top`, `r_mid`, `r_bot` | yes | UVLO/OV ladder: BUS_IN to EN/UVLO (`r_top`), EN/UVLO to OV (`r_mid`), OV to GND (`r_bot`) (DS Fig 7-5 p.16, eq. 12, 13) |
| `fault_pullup`, `pgood_pullup` | optional (default fitted) | Fit the on-block 10 kohm pull-ups to V3V3 (DNP-able) |
| `v_vgs`, `v_abs_cs_pair`, `v_vds_rating` | yes | Declared worst-case pin-pair voltages for the FET VGS, the TPS CS+/CS- pair and the FET VDS, from the analysis (`v_vgs_range`, `v_cs_pair_range`, `v_vds_range`; decision D7) |
| `v_shunt_across`, `v_rdamp_across` | yes | Declared voltage across the shunt (`vsense_op_max`, decision D9) and R2 (`v_rdamp_dc`) |
| `ambient`, `derate` | yes | Passed to every library part's checks (the ISC035 wrapper has no ambient config; the FET temperature is covered by the analysis only) |

**Block design (first instance)**
| Function | Part | Value / choice | Basis |
|---|---|---|---|
| Controller | TI TPS48000QDGXQ1 (library `TexasInstruments/TPS48000QDGXQ1`; orderable MPN TPS48000QDGXRQ1, tape and reel, library `mpn`) | VSSOP-19 | designer choice 2026-11-15 |
| High-side FET | Infineon ISC035N10NM5LF2 (library `Infineon/ISC035N10NM5LF2`) | 100 V, OptiMOS 5 Linear FET 2, PG-TDSON-8 | designer choice 2026-11-15, chosen over BSC040N10NS5 because the BSC040 SOA fails slew-controlled inrush into 200 uF at 40 V at every ramp time; ISC035 Table 1 linear-mode current at 50 V, 10 ms: 7.4 A (library key `id_lin_50v_10ms`, DS p.1, soa branch) |
| Current sense shunt | Vishay WSK2512-LOW, 4 mohm, tol D (library `Vishay/WSK2512-LOW`) | 4 mohm, +/-0.5 % (`tol_d`), 1 W (`p70`), 4-terminal Kelvin, TCR `tcr_3m_4m9` (+/-75 ppm/degC, DS p.2) | option study `hss_study6.py`: with RISCP 10.5 kohm, 3.8-4.6 mohm meets trip minimum > 6 A and the soft-short requirement; 4.0 mohm chosen, MPN WSK25124L000DEA (decision log D1; stock to confirm). 10 mohm reuse rejected: outside the passing range |
| Gate ramp R1 | ERJ-3EKF (0603, 1 %) | 100 kohm | DS p.16 suggestion; same value as RTMR, one BOM line |
| Gate ramp C1 | GCM188 C0G 6.8 nF +/-5 %, 100 V, 0603 (`GCM1885C2A682JE02D`, library `rated_v` 100 V) | 6.8 nF, gate to GND (DS Fig 7-4 p.15; decisions D12, D13) | designer: common soft-start value; same value and part as CTMR, one BOM line. C0G has no DC-bias loss; 6.8 nF is 0.949 to 1.053 x nominal over tolerance and drift (design-analysis.md section 8). Voltage across C1 up to `v_c1_max_v` (53.86 V, bus max plus V(BST-SRC) max, eq. 3 / eq. 4 p.16), PASS against 100 V (summary `v_c1_max_v`). DC block leakage 1e-7 V (`v_rdamp_dc`; the 125 degC IR of the C0G series is ASSUMED for this part, not bounded in the library) |
| Gate damping R2 | ERJ-3EKF (0603, 1 %; `ERJ3EKF10R0V`, library `calc.mpn`) | 10 ohm, in series with C1 | DS p.16 ("~10 ohm" damping resistor) and Fig 7-4 p.15; designer decision 2026-11-15; ERJ-3EKF range starts at 10 ohm (`res_range`). Single-use value: required by the DS gate-ramp circuit. Single-pulse rating NOT BOUNDED (no pulse row in the library; `c1_dump_peak_a`, `c1_dump_tau_ns`; open item) |
| Fault timer C | GCM188 C0G 6.8 nF +/-5 %, 100 V, 0603 (`GCM1885C2A682JE02D`) | CTMR = 6.8 nF | tSC 70.7 to 132.9 us (`tsc_min_us`, `tsc_max_us`; DS p.19 eq. 8, design-analysis.md section 8); 154.5 us (`tsc_max_ext_us`, V(TMR_SC) max) is used for the fault energy. Designer decision D13: 100 V, one BOM line with C1 |
| Latch-off R | ERJ-3EKF (0603, 1 %) | RTMR = 100 kohm across CTMR | DS p.19 (7.3.3.2); same value as R1, one BOM line |
| ISCP resistor | ERJ-3EKF (0603, 1 %) | RISCP = 10.5 kohm | `v_scp_r10k5` (V(SCP) 31.1 / 40 / 48.9 mV, DS p.7): only table rows give bounded thresholds; 10.5 kohm is the exact row value and E96. Designer decision 2026-11-15: the 28.0 kohm row fails the soft-short requirement at 6 A (option study `hss_study6.py`) |
| Bootstrap C | Murata GCM21B X7R 470 nF +/-10 %, 50 V, 0805 (`GCM21BR71H474KA55L`, library GCM21B 0.2.0) | one part, 470 nF, BST to SRC (decision D14; replaces three 100 nF 0603) | minimum from DS eq. 4 with Qg max (ISC035 `qg`) and C1 max (`cbst_min_nf`); low corner `cbst_low_corner_nf` (tolerance and tc only); the DC-bias and aging loss the low corner still tolerates is `cbst_dc_bias_allow_frac` (ESTIMATE; the loss itself is NOT BOUNDED in the library, open item); floor `rec_cap_bst_src` min 0.1 uF (DS p.5) |
| VS bypass | Murata GCM188R71H104KA57 | 100 nF X7R 50 V 0603, at pin VS (two 100 nF parts in total with SRC) | DS p.28 recommends >= 0.1 uF; decoupling rule (100 nF per power pin) |
| SRC bypass | Murata GCM188R71H104KA57 | 100 nF X7R 50 V 0603, SRC to GND; reuses the 100 nF line | DS p.5 Recommended Operating Conditions, external capacitor VS, SRC to GND, min 22 nF (`rec_cap_vs_src`). The block cannot assume a load capacitor is present on BUS_OUT, so C_SRC is fitted inside the block (100 nF, `cbyp_nf`) and does not depend on the load |
| UVLO/OV ladder | ERJ-3EKF (0603, 1 %) x3 | 536 kohm (`r_top_ohm`), 30.1 kohm (`r_mid_ohm`), 51.1 kohm (`r_bot_ohm`) | DS Fig 7-5 p.16, eq. 12, 13 (`vin_ovp_from_divider`, `vin_uvlo_from_divider`); selected by `analysis.py` with the ERJ-3EKF solder-heat BOL in the windows (decision D8; design-analysis.md section 4) |
| FAULT_N pull-up | ERJ-3EKF (0603, 1 %) | 10 kohm to V3V3, DNP-able (`fault_pullup`) | designer choice (same as INA229 ALERT_N pull-up) |
| PGOOD_N pull-up | ERJ-3EKF (0603, 1 %) | 10 kohm to V3V3, DNP-able (`pgood_pullup`) | designer choice 2026-11-15 |
| SCP_TEST, CS_SEL | none | tied to GND | DS p.3 Table 4-1: CS_SEL to GND for high-side sensing; SCP_TEST to GND when diagnosis not used |
| PD to gate | none | PD tied directly to the FET gate | DS p.16 (fast turn-off) |

**BOM line count: 13** (`bom_lines`). Parts: 2 ICs (TPS48000QDGXQ1 controller, orderable TPS48000QDGXRQ1; ISC035N10NM5LF2), 1 shunt, and 10 passive values: 100 nF (qty 2: VS, SRC), 470 nF (qty 1: CBST), 6.8 nF 100 V (qty 2: C1, CTMR), 100 kohm (qty 2: R1, RTMR), 10 kohm (qty 2: FAULT_N, PGOOD_N pull-ups), 10.5 kohm (RISCP), 536 kohm, 30.1 kohm, 51.1 kohm (UVLO/OV ladder), 10 ohm (R2).
Values used in one place only: 10.5 kohm, 536 kohm, 30.1 kohm, 51.1 kohm (each required by its equation), 10 ohm (R2, DS p.16). All
passives 0603 or larger. The shunt (2512) is unique for accuracy and power reasons.

**Control and fault logic (from the library's TPS48000 pin definitions)**
- EN drives INP directly (INP is the FET gate enable). V(INP_H) max 2 V; V(INP_L) min 0.8 V (DS p.7).
- EN/UVLO: rising V(UVLOR) 1.17 / 1.23 / 1.29 V; falling V(UVLOF) 1.09 / 1.14 / 1.19 V (DS p.6). Enable thresholds
  V(ENR) max 1 V, V(ENF) min 0.3 V.
- OV: rising V(OVR) 1.17 / 1.23 / 1.28 V; falling V(OVF) 1.08 / 1.14 / 1.19 V (DS p.6).
- CS_SEL to GND: high-side current sense (DS p.3).
- SCP monitored only after G_GOOD (DS p.17): see open items.
- FAULT_N asserts on short-circuit, UVLO, OV and SCP diagnosis (DS p.3). PGOOD_N = FLT_GD: asserts (low) when
  V(PD-SRC) exceeds V(G_GOOD) (DS p.3, p.8).

**Layout requirement from the analysis**: FET RthJA 50 K/W needs 6 cm2 of copper (section 6; layout not started).

**Interfaces (board level)**
- EN: active-high enable, logic at V3V3 or below, 0-65 V tolerant; the board drives it. Board nets: `VBUS_IN` ->
  `BUS_IN`, `VBUS_OUT` -> `BUS_OUT`, `SW_EN`, `SW_FAULT_N`, `SW_PGOOD_N`, `V3V3`, `GND`.
- FAULT_N: open drain, active low; on-block 10 kohm pull-up, or DNP when the host or a shared wired-OR pull-up is used.
- PGOOD_N: open drain, active low; status to the host (FET fully enhanced, SCP armed). Loads do not read it.
- Bus rail sizing: the block shares the bus with other channels only through the 100 A fault-limit system requirement
  above.

**Schematic deliverable**
- The block is a reusable hierarchical sub-sheet (`Project()` in the module; child sheet `eda/TPS.kicad_sch` for
  instance `TPS`), drawn to the house style: `docs/style-notes.md` and the zener-library `schematic-style` skill.
- Title block filled (title, revision, date, company, author, repo + commit, sheet description).
- Engineering notes and titled group boxes whose numbers come only from the spec and the worst-case analysis report.
- A titled **LOAD TURN-ON RATING** note box on the reference sheet, beside the FET: any load behaviour, 12-40 V, up to
  the 6 A design current with 200 uF; loads above 6 A are outside the rating. Numbers filled from `values.json` by
  the notes script.
- Checks: `pcb apply schematic` twice (second unchanged), `pcb build -Dwarnings` exit 0, KiCad ERC reviewed.

**Library dependency**
- zener-library packages TPS48000QDGXQ1, ISC035N10NM5LF2 (carries the SOA curve rows, `id_lin_50v_10ms` and the
  pin-pair `v_<key>` configs), WSK2512-LOW, GCM188, GCM21B (bootstrap capacitor), ERJ-3EKF (versions in `pcb.toml`,
  tool-managed). Python analysis: the release `analysis/_zl.py` pins (the account names it). All symbols are KiCad 10
  format, required by `pcb apply`.

## Decisions made without designer input (2026-11-15, for review)

The designer asked for the build to proceed without stopping; each of these would otherwise have been a question.
- D1 Shunt value 4.0 mohm, WSK25124L000DEA (library `calc.mpn`): the only value in the passing 3.8-4.6 mohm range with
  orderability evidence (4L000 F listed at Mouser; D in a Vishay PCN list); round values are the stocked ones.
  Option study at 4.0 mohm passes trip minimum and soft shorts. Alternative: 4.1-4.2 mohm (better shunt margin) if
  stocked.
- D2 Library pinning (re-pinned at the 2026-11-17 release): Zener deps are the released packages ISC035N10NM5LF2 0.3.0
  and TPS48000QDGXQ1 0.3.0; Python analysis and the schematic helpers (`place.py`, `overlap.py`, `kicad_sexpr.py`) come
  from zener-library v0.4.0. Before the release the Zener deps were pinned to dev pseudo-version commit f563e7e and
  the analysis ran on `feat/isc035-soa` @ 0426982.
- D3 (SUPERSEDED by D14) Bootstrap 3 x 100 nF instead of 2: the GCM188 DC-bias loss is not bounded in the library and 2 x 100 nF leaves little room above the 156 nF minimum after any loss; same BOM line, one more part.
  little room above the 156 nF minimum after any loss; same BOM line, one more part.
- D4 Instance name `TPS`, child sheet `eda/TPS.kicad_sch` (matches the INA convention of naming by part).
- D5 (SUPERSEDED by D13: 100 V) 6.8 nF capacitors: C0G/NP0 0603 (GCM188 C0G) if the library catalog has it, for timer and slew accuracy; otherwise X7R with its drift in the analysis (see the build report).
  otherwise X7R with its drift in the analysis (see the build report).
- D7 Library change for pin-pair checks: the wrappers checked FET VGS and TPS CS+/CS- with independent net ranges,
  which no high-side design can pass; added optional `v_<key>` configs fed by this analysis (zener-library PR #14).
  That change was pinned to dev pseudo-version commit f563e7e until the 2026-11-17 release; ISC035 and TPS48000 are now
  on their released v0.3.0 packages, and the Zener deps no longer carry a pseudo-version.
- D8 Divider includes the ERJ-3EKF solder-heat BOL in the judged windows (ee-design): reselected 536 k / 30.1 k /
  51.1 k. The OV floor (never below 13.2 V) binds with +3 mV margin: thin but met; widening it needs a tighter
  resistor (ERA 0.1 %, new BOM lines) or a lower OV floor.
- D9 Shunt `v_across` in Zener = the 6 A operating shunt voltage (INA229 precedent); the wrapper's rated-voltage check
  uses nominal R and would wrongly fail the soft-short voltage, whose survival is judged by power in the analysis
  (0.937 W of 1 W, ESTIMATE).
- D10 The 100 A prospective fault-current system requirement applies in both directions: it includes reverse
  discharge of the load capacitance (200 uF reference) back through the switch into an upstream short. The CS+/CS-
  pair is then declared at +/-(100 A x R_hi) (`v_cs_pair_range`). Without reverse blocking the block cannot limit
  that current itself; the board meets it (source and wiring impedance, fusing).
- D11 The re-runnable placement generator for the reference sheet lives with its bench
  (`benches/TPS48000HighSideSwitch/arrange_sheet.py`), using the generic `place.py` helpers (zener-library PR #12, released in v0.4.0)
  through `ZL_TOOLS` (default `../zener-library/tools/schematic`, zener-library v0.4.0); block-specific scripts stay out
  of the shared `tools/`.
- D6 Agent models: Haiku for the Zener module and bench; Sonnet for `analysis.py` (size and accuracy) and for
  placement; fresh Haiku verifiers; fresh Sonnet adversarial review.

- D12 (2026-11-16, designer correction) Gate ramp capacitor C1 returns to GND, not SRC. DS Fig 7-4 p.15 shows PU -> R1 -> gate and gate -> R2 -> C1 -> GND, with PD tied to the gate; eq. 3 and eq. 4 (p.16) size R1/C1 and CBST. The earlier design tied C1 to BUS_OUT, which was wrong. C1 now sees V(gate - GND) up to the bus maximum plus V(BST-SRC) max (`v_c1_max_v`, 53.86 V, PASS against 100 V). The C1 dump into PD at turn-off and the R2 pulse are reported (`c1_dump_peak_a`), not judged: open items.
- D13 (2026-11-16) C_GATE and C_TMR: GCM188 C0G 6.8 nF +/-5 % at 100 V, `GCM1885C2A682JE02D` (library, `rated_v` 100 V), one BOM line. The library has no `v_across` on capacitor wrappers, so C_GATE's rating check uses the net ranges (59 V, PASS); see open items.
- D14 (2026-11-16) The three 100 nF bootstrap capacitors become ONE capacitor C_BST: Murata GCM21B X7R 470 nF 50 V +/-10 % 0805 (`GCM21BR71H474KA55L`, library GCM21B 0.2.0). Reason: the minimum is 159.6 nF (eq. 4, C1 max; `cbst_min_nf`); a 0603 X7R 50 V part tops out at 220 nF in the library, which leaves almost no room for the unbounded DC-bias loss; 470 nF tolerates `cbst_dc_bias_allow_frac` of loss at its low corner.

<!-- BEGIN GENERATED ACCOUNT: python3 tools/bind.py --write-spec; do not edit by hand -->

## Results account (generated)

Bench reference instance `TPS`, from `benches/TPS48000HighSideSwitch/docs/design-analysis.md` (zener-library v0.7.0). Library rows read by the analysis: 150; checked by a person (current human stamp): **0 of 150**; the rest carry a machine `verified` stamp or none. The library does not attribute rows to individual results, so the count is per analysis, not per row below.

| Requirement | Result (worst case) | Limit | Margin | Status | Rests on (assumption / estimate) | Open items | Section |
|---|---|---|---|---|---|---|---|
| UVLO rising trip max (turn-on guaranteed below) | 10.527 V | <= 10.6 V (10.8 - 0.2) | +0.073 V | PASS | none | none | 4 |
| Turn-on margin to the minimum bus | 0.273 V | >= 0.2 V | +0.073 V | PASS | none | none | 4 |
| OV rising trip min | 13.203 V | >= 13.2 V | +0.003 V | PASS | none | none | 4 |
| Divider current at the bench bus (worst-case range) | 19.21 to 19.68 uA | 10 to 20 uA | +0.32 uA | PASS | none | none | 4 |
| EN/UVLO pin at 40 V bus (max) | 5.417 V | <= 65 V rec | +59.58 V | PASS | none | none | 4 |
| OV pin at 40 V bus (max) | 3.417 V | <= 70 V abs | +66.58 V | PASS | none | none | 4 |
| Short-circuit trip, minimum (self-heated, tol, TCR, solder BOL) | 6.78 A | > 6 A design current | +0.78 A (+13.0 %) | ESTIMATE, PASS | ESTIMATE shunt self-heating (derating-curve theta); ASSUMPTION t_ref_c (shunt TCR reference) | OI-ACCURACY | 5 |
| Shunt power at the highest non-tripping current, 40 degC | 0.937 W | <= 1 W | +0.063 W | ESTIMATE, PASS | ESTIMATE shunt self-heating (derating-curve theta); ASSUMPTION t_ref_c (shunt TCR reference) | OI-SOFT | 5 |
| FET Tj at the highest non-tripping current, 40 degC | 111.6 degC | <= 175 degC | +63.4 K | ESTIMATE, PASS | ESTIMATE FET Rds(on) below VGS 10 V and thermal (typical curves) | OI-RDS | 5 |
| FET Tj at 6 A, 40 degC | 49.4 degC | <= 175 degC | +125.6 K | ESTIMATE, PASS | ESTIMATE FET Rds(on) below VGS 10 V and thermal (typical curves) | OI-RDS, OI-COPPER | 6 |
| Shunt power at 6 A, 40 degC | 0.164 W | <= 1 W | +0.836 W | ESTIMATE, PASS | ESTIMATE shunt self-heating (derating-curve theta); ASSUMPTION t_ref_c (shunt TCR reference) | none | 6 |
| Shunt element temperature at 6 A, 40 degC | 56.4 degC | <= 170 degC (ASSUMED) | +113.6 K | ESTIMATE, PASS only if the element limit is the assumed one | ESTIMATE shunt self-heating (derating-curve theta); ASSUMPTION t_ref_c (shunt TCR reference); ASSUMPTION t_elem_limit_c (element limit) | none | 6 |
| TPS48000 Tj at 40 degC (rth_ja typical) | 40.21 degC | <= 150 degC | +109.8 K | ESTIMATE, PASS | ESTIMATE rth_ja (typical only) | OI-DATA | 6 |
| FET Tj peak during turn-on, 12 and 40 V, both loads, both ramp corners | 95.4 degC | <= 175 degC | +79.6 K | ESTIMATE, PASS | ESTIMATE SOA and Zth curve rows (typical only); design input cload_f | OI-LOADS | 7 |
| Turn-on I_eq / SOA limit (TC 125 degC curve), worst | 0.857 | <= 1 | +0.143 | ESTIMATE, PASS | ESTIMATE SOA and Zth curve rows (typical only); design input cload_f | OI-LOADS | 7 |
| Largest passing bus voltage at turn-on (grid 0.5 V, to 80 V) | 45 V | >= 40 V rated | +5.0 V | ESTIMATE, PASS | ESTIMATE SOA and Zth curve rows (typical only); design input cload_f | OI-LOADS | 7 |
| FET Tj after 100 A for tSC max, from the soft-short state | 116.7 degC | <= 175 degC | +58.3 K | ESTIMATE, PASS | ESTIMATE FET Rds(on) below VGS 10 V and thermal (typical curves); design input i_fault_a | OI-RDS | 8 |
| Fault current with Tj at the limit (tSC max, soft-short start) | 312 A | >= 100 A | +212 A | ESTIMATE, PASS | ESTIMATE FET Rds(on) below VGS 10 V and thermal (typical curves) | OI-RDS | 8 |
| Fault current vs pulsed drain current | 100 A | <= 656 A (`id_pulse`) | +556 A | PASS | design input i_fault_a | none | 8 |
| CS+ to CS- in the fault | 0.454 V | <= 100 V abs | +99.5 V | PASS | design input i_fault_a | none | 8 |
| Avalanche energy, worst of the L sweep (0.25-5 uH) | 41.7 mJ | `eas` 398 mJ at 50 A only | 9.6x | ESTIMATE (eas not valid above 50 A) | design input l_lead_uh (inductance not known) | OI-AVALANCHE | 8 |
| Turn-off through lead inductance, no TVS: avalanche clamps BUS_IN at V(BR)DSS min or above vs VS / CS+ abs max | >= 100 V | <= 100 V (`abs_vs_cs_gnd`) | +0 V (V(BR) is a minimum, so the clamp can be higher) | FAIL (risk: unprotected transient; TVS deferred by the designer; DS p.27-28 recommends a TVS) | none | OI-TVS | 8 |
| Bootstrap capacitor minimum needed (eq. 4, C1 max) | 159.6 nF | <= 360 nF (low corner, no DC bias loss) | +200 nF | ESTIMATE: DC-bias loss not bounded in the library | none | OI-CBST | 9 |
| V(G_GOOD) max above V(BST-SRC) ON min | 0.24 V | <= 0 V (no overlap) | -0.24 V | FAIL (accepted residual risk, OPEN ITEM) | none | OI-GGOOD | 9 |
| C1 gate to GND, worst case (bus max + V(BST-SRC_OFF) max) | 53.86 V | <= 100 V (C0G rated_v, derate 1) | +46.14 V | PASS | none | OI-CGATE | 9 |
| Largest DC-bias and aging loss of CBST that still meets eq. 4 at the low corner (not a DS equation) | 55.6 % allowed loss | loss NOT BOUNDED in the library | - | OPEN ITEM: DC-bias loss not bounded | none | OI-CBST | 9 |
| C1 dump peak current into PD through R2 (v_c1_max_v / R2 min, not a DS equation) | 5.45 A | PD peak sink not bounded (i_pd_peak: 2 A typ only) | - | OPEN ITEM: PD sink and R2 pulse rating not bounded | none | OI-R2-PULSE, OI-PD-SINK | 9 |
| Smallest rating margin (table in section 10, normalised: see table) | 32 rows | all margins >= 0 | none failing | PASS | none | none | 10 |

11 further results are REPORT ONLY, REFERENCE or CHECK (no limit set): see the report summary.

**Design values (read from the .zen through the bench netlist export)**

| Analysis input | Value | Source |
|---|---|---|
| `rshunt_ohm` | 0.004 | config `rshunt` = `4m` |
| `rshunt_tol_code` | 'D' | config `rshunt_tol` = `D` |
| `riscp_ohm` | 10500.0 | config `riscp` = `10.5k` |
| `ctmr_f` | 6.8e-09 | config `ctmr` = `6.8nF` |
| `rtmr_ohm` | 100000.0 | config `rtmr` = `100k` |
| `r1_ohm` | 100000.0 | config `rgate` = `100k` |
| `c1_f` | 6.8e-09 | config `cgate` = `6.8nF` |
| `r2_ohm` | 10.0 | R_DAMP MPN `ERJ3EKF10R0V` value (library) |
| `r_top_ohm` | 536000.0 | config `r_top` = `536k` |
| `r_mid_ohm` | 30100.0 | config `r_mid` = `30.1k` |
| `r_bot_ohm` | 51100.0 | config `r_bot` = `51.1k` |
| `r_fault_pullup_ohm` | 10000.0 | R_FAULT MPN `ERJ3EKF1002V` value (library) |
| `r_pgood_pullup_ohm` | 10000.0 | R_PGOOD MPN `ERJ3EKF1002V` value (library) |
| `c_small_rated_v` | 100.0 | C_TMR MPN `GCM1885C2A682JE02D` rated_v (library) |
| `c_small_dielectric` | 'C0G' | C_TMR MPN `GCM1885C2A682JE02D` dielectric (library) |
| `c_small_tolerance` | '+/-5%' | C_TMR MPN `GCM1885C2A682JE02D` tolerance (library) |
| `cbst_f` | 4.7e-07 | C_BST MPN `GCM21BR71H474KA55L` value (library) |
| `cbst_rated_v` | 50.0 | C_BST MPN `GCM21BR71H474KA55L` rated_v (library) |
| `cbst_dielectric` | 'X7R' | C_BST MPN `GCM21BR71H474KA55L` dielectric (library) |
| `cbst_tolerance` | '+/-10%' | C_BST MPN `GCM21BR71H474KA55L` tolerance (library) |
| `cbyp_f` | 1e-07 | C_VS MPN `GCM188R71H104KA57D` value (library) |
| `cbyp_rated_v` | 50.0 | C_VS MPN `GCM188R71H104KA57D` rated_v (library) |
| `vbus_lo_v` | 12.0 | net `VBUS_IN` (`12–40V (26V nom.)`) min |
| `vbus_hi_v` | 40.0 | net `VBUS_IN` (`12–40V (26V nom.)`) max |
| `v3v3_nom_v` | 3.3 | net `V3V3` (`3.3V 3%`) nominal |
| `v3v3_tol_frac` | 0.03 | net `V3V3` (`3.3V 3%`) tolerance |
| `ta_min_c` | 5.0 | bench config `ambient` = `5–40°C (22.5°C nom.)` min |
| `ta_max_c` | 40.0 | bench config `ambient` = `5–40°C (22.5°C nom.)` max |
| `derate` | 1.0 | bench config `derate` = `1.0` |

**Design outputs (analysis result -> .zen config; tools/bind.py fails when they differ)**

| Config | Value | Basis |
|---|---|---|
| `r_top` | 536kohm | UVLO/OV ladder selected by the analysis (section 4) |
| `r_mid` | 30.1kohm | UVLO/OV ladder selected by the analysis (section 4) |
| `r_bot` | 51.1kohm | UVLO/OV ladder selected by the analysis (section 4) |
| `v_vgs` | 0V to 13.86V | V(G) - V(S) range (values.json v_vgs_range) |
| `v_abs_cs_pair` | -454.45225mV to 454.45225mV | CS+ - CS- range, both fault directions (D10) (values.json v_cs_pair_range) |
| `v_vds_rating` | -1.2V to 40V | V(D) - V(S) range in operation (values.json v_vds_range) |
| `v_rdamp_across` | 100nV | R_DAMP DC voltage (values.json v_rdamp_dc) |
| `v_shunt_across` | 27.3mV | shunt voltage at the design current, rounded up to 0.1 mV (values.json vsense_op_max, D9) |

Topology preconditions: 16 (`blocks/TPS48000HighSideSwitch/topology.toml`), checked against the bench netlist by tools/bind.py on every verify run.

<!-- END GENERATED ACCOUNT -->

## Open Questions

- Input protection: no input TVS and no output diode (designer: "not yet"). DS p.27-28 recommends them. See open items.
- Copper area for the FET (6 cm2 for RthJA 50 K/W) and the shunt current path: layout requirements, not started.

## Open items

- `OI-TVS` **Turn-off transient without a TVS (FAIL, highest risk; `turnoff_transient_no_tvs`, design-analysis.md section 8):** at turn-off the lead-inductance energy is clamped by the FET avalanche at V(BR)DSS min 100 V or above (a minimum, so the clamp can be higher). The analysis result is `turnoff_transient_no_tvs` against `abs_vs_cs_gnd` (margin in the account). Section 8 also names the VS pin rating `abs_input_vs_pos` 70 V as the tighter basis for a long lead inductance with no local bulk capacitance (analysis open item, not judged). DS p.27-28 recommends a TVS across the input (hot-plug and fast turn-off transients), a diode or TVS across the output (negative spikes), and an RVS ~100 ohm / CVS filter on VS. The designer has deferred them ("not yet"); this stays the top open item until the designer decides.
- `OI-SOFT` **Soft shorts (resolved by design current, 2026-11-15):** the TPS48000 has only the short-circuit comparator (no
  continuous overcurrent or I2t protection), so currents up to the trip maximum must be survivable. At 10 A no bounded
  threshold row met this with a 1 W shunt (`hss_study5.py`); at 6 A, RISCP 10.5 kohm with 3.8-4.6 mohm does
  (`hss_study6.py`). Residual: the shunt margin (`soft_shunt_w`, section 5) depends on the derating-curve thermal
  resistance ESTIMATE; the analysis reports it. Calibration cannot move the trip (bounded discrete rows over full
  temperature), so it is not credited.
- `OI-STOCK` **Shunt stock not confirmed:** WSK25124L000DEA (4.0 mohm, D) chosen (decision log D1); distributor pages could not
  be read on 2026-11-15. Confirm stock / lead time at Mouser, Digi-Key or Newark before release; a Vishay PCN (Feb
  2022) lists this MPN (not opened).
- `OI-CHAIN` **Separate shunts when chained with an INA229 block:** the switch's 4 mohm and the monitor's shunt are in series
  (extra loss and BOM line). Kept separate by default (designer, 2026-11-15) for clean block reuse; a shared-shunt
  "monitored switch" would be a different block.

- `OI-RDS` **Rds(on) only at VGS = 10 V (`rds_on_10v`, main branch); minimum gate drive is V(BST-SRC_ON) min 8.24 V
  (`v_bst_src_on`).** Soa-branch keys (typical curve reads, not bounds, Tj 25 degC unless stated):
  `rds_tj25_vgs8p24v_id50a` typ 3.94 mohm; `rds_tj175_vgs8p24v_id50a` typ 7.92 mohm (Tj 175 degC).
  Saturation current at VGS 8 V: `ioutput_tj25_vgs8v_vds5v` typ 224 A (Diagram 6, typical only), so the saturation
  current at the 8.24 V minimum drive against the fault current is not bounded.
- `OI-GGOOD` **PGOOD_N / G_GOOD window:** V(G_GOOD) max 8.48 V (`v_g_good`) exceeds V(BST-SRC_ON) min 8.24 V. On a worst-case part
  PGOOD_N may not assert or may chatter, and SCP (monitored only after G_GOOD, DS p.17) may be intermittently blanked.
  Designer decision 2026-11-15: no TI query. FAIL as a corner check (`gate_overlap_v`, section 9). Carried as an accepted residual risk: the analysis reports the corner
  overlap; bench verification on built units measures V(BST-SRC) minimum ripple and FLT_GD behaviour.
- `OI-OVERLOAD` **Shunt short-time overload:** test limit `perf_short_time_overload` +/-0.5 % (plus 0.0005 ohm), overload level not
  printed (external calculator only). The fault energy (`fault_shunt_energy_mj`, section 8) is a design-review case, not a bound.
- `OI-AVALANCHE` **FET avalanche:** the `eas` row (398 mJ) is valid only at 50 A, RGS = 25 ohm (DS p.3). Avalanche above 50 A is not
  bounded by a table row. Soa-branch avalanche curve (Diagram 14) tops out at 50 A (`iav_tjstart*` rows, typical). Analysis worst: `avalanche_max_mj` (ESTIMATE, section 8).
- `OI-REVERSE` **Reverse polarity (no reverse blocking):** the FET body diode conducts with a reversed BUS_IN, and the loads are
  unprotected. The controller tolerates VS down to -65 V (DS p.20, 7.3.5), so the IC survives, but the power path does
  not. Designer choice; confirm for the bench PSU and the 12-40 V boards.
- `OI-PROTECTION` **TVS and output diode:** not fitted (designer decision, "not yet"). DS p.27-28 recommends a TVS across the input
  (hot-plug and fast turn-off transients), a diode or TVS across the output (negative spikes), and an RVS ~100 ohm /
  CVS filter on VS. Without them, turn-off energy from lead inductance goes into FET avalanche (see above). Open.
- `OI-LOADS` **Loads drawing current during turn-on:** handled by the Load turn-on rating table (all behaviours 12-40 V at up to
  6 A). Constant-power loads (converters) draw more current at low voltage once their lockout releases; the
  table relies on their lockout and soft-start, which belong to the load, not this block.
- `OI-LIBRARY` **Library release:** for a later release, bump `ZL_VERSION` and the Zener `pcb.toml` dependencies
  together. The UVLO/OV ladder order in an older library `calc.py` docstring is superseded by DS Fig 7-5.
- `OI-SHUNT-BOL` **Shunt BOL terms:** `perf_solder_heat` total limit is +/-(0.5 % of R + `perf_offset_ohm` 0.0005 ohm), a fixed term
  that dominates a 4 mohm shunt; TCR `tcr_3m_4m9` +/-75 ppm/degC has no stated reference temperature (DS p.2).
  Applied by analysis.
- `OI-DS-TEXT` **Datasheet text conflicts (library decision log):** the DS text uses "TPS48000-Q1" for the part; eq. 6 uses an ITMR
  of 80 uA while the table gives I(TMR_SRC_CB) 65 / 87 / 106 uA (`i_tmr_src_cb`; the analysis uses the table);
  V(OVR) of 1.225 V in the DS example is not a table row (table 1.17 / 1.23 / 1.28 V); the ISCP bias current (`i_iscp`)
  differs from the 2 uA constant in eq. 5.
- `OI-COPPER` **Copper area and current path rating:** BUS_IN/BUS_OUT copper, shunt pads and FET tab must be rated for the trip maximum
  continuously (`trip_max_a`; soft shorts below it do not trip), and the 6 cm2 copper for RthJA 50 K/W is a layout requirement (layout not started).
- `OI-DATA` **Typical-only or partial data:** TPS `rth_ja` 92.3 degC/W is typical only (no JEDEC board stated); the
  -40 to 85 degC quiescent subset `i_gnd_m40_85` (53 uA max) is covered by the full-range bound `i_gnd_full`; the DC-bias capacitance loss of the X7R parts is not bounded in the library (DS p.13 example only): the GCM21B 470 nF bootstrap capacitor (`cbst_dc_bias_allow_frac`) and the GCM188 100 nF VS and SRC bypass capacitors.
- `OI-ACCURACY` **Accuracy of the trip and timing:** the trip window (`trip_window_a`) and tSC (`tsc_us`) are the analysis values. They apply the shunt tolerance, TCR and BOL terms, the `i_tmr_src_cb` and `v_tmr_sc` bounds and the C0G drift (`ctmr_latch`). The trip minimum and the shunt power still rest on the self-heating ESTIMATE (theta_est, section 5).
- `OI-R2-PULSE` **R2 single-pulse rating (NOT BOUNDED):** the ERJ-3EKF library part has no pulse row. The C1 dump at turn-off or on a hard short (`c1_dump_peak_a`, `c1_dump_energy_uj`, `c1_dump_tau_ns`, `c1_dump_peak_w`) is a single pulse in R2. The DS circuit (p.16, "~10 ohm") is used as drawn. Open: pick a pulse-rated resistor or get the pulse curve.
- `OI-PD-SINK` **PD peak sink (NOT BOUNDED):** DS p.7 prints I(PD) peak 2 A typical only (`i_pd_peak`). The C1 dump peak (`c1_dump_peak_a`) is above that typical value. It is reported, not judged, because the sink has no bound.
- `OI-CBST` **CBST DC-bias and aging loss (NOT BOUNDED):** the GCM21B 470 nF CBST is judged at its low corner only. It tolerates `cbst_dc_bias_allow_frac` of loss before the eq. 4 minimum (`cbst_min_nf`) is missed. Verify on built units (BST ripple) or from the manufacturer's DC-bias tool; the same holds for the GCM188 VS and SRC bypass capacitors.
- `OI-CGATE` **C_GATE rating uses net ranges (library change needed):** the capacitor wrappers (GCM188, GCM21B) take no `v_across` config, unlike the ERJ and the TPS and ISC wrappers. C_GATE's rating check therefore uses the net ranges (59 V against 100 V, PASS, conservative) and not `v_c1_max_v`. Fix: add an optional `v_across` config to the Murata capacitor wrappers (zener-library change, then release).
