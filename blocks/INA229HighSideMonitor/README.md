# INA229HighSideMonitor

High-side INA229 current, bus-voltage, power, energy and charge monitor with an on-block 4-terminal Kelvin shunt
(Vishay WSK2512), VS decoupling, MISO source termination and an optional ALERT_N pull-up, read over SPI.

- Ports: `BUS_IN`, `BUS_OUT` (power path, positive current BUS_IN -> BUS_OUT, VBUS sensed at BUS_OUT), `V3V3`,
  `GND`, `SPI`, `ALERT_N`. Bus limited to 40 V (IN+/IN- differential abs max), see `spec.md`.
- Configs: `rshunt`, `rshunt_tol`, `vsense_max`, `alert_pullup`, `ambient`, `derate`.
- Analysis: `python3 blocks/INA229HighSideMonitor/analysis.py --params <file> --out <dir> --instance <NAME>`.
- Bench and reference sheet: `benches/INA229HighSideMonitor/`; schematic PDF:
  [`benches/INA229HighSideMonitor/docs/INA229HighSideMonitor_bench.pdf`](../../benches/INA229HighSideMonitor/docs/INA229HighSideMonitor_bench.pdf).
