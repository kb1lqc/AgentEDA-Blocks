# ina229 Design Specification

## Purpose

<!-- Describe what this board does, who or what uses it, and how it fits into the larger system. -->

## Requirements

<!-- Capture the important requirements and any known constraints:
- Power: input sources, voltage ranges, and expected current or power budget.
- Interfaces: connectors, protocols, and signals to other boards or devices.
- Functionality: key components, features, and measurable performance targets.
- Physical: board size, mounting, and environmental constraints.
Include only what applies; leave undecided details as open questions.
-->

## Open Questions

<!-- List decisions still to be made, options being considered, and anything blocking implementation. -->
- Ambient temperature range (sets the board `ambient` config; INA229AIDGS rec-op is -40 to 125 degC).
- Project derating factor for abs-max limits (`derate`, default 1.0 = none).
- Bus / common-mode voltage range and transients (INA229 IN+, IN-, VBUS abs max 85 V DC; surge needs separate protection).
- Measured current range and shunt value (full-scale, resolution, shunt power).
- High-side vs low-side sensing.
- Logic supply (VS 2.7 to 5.5 V) and SPI host / connector.