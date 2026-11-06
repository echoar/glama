# pc-health: Read-Only Machine Diagnostics & Thermal Check

1. Call `get_machine_diagnostics()` to inspect GPU temperature, fan speed, power draw, top RAM-consuming processes, and disk free space.
2. Flag any disk with `< 15 GB` free, GPU temp `> 80°C`, or runaway memory hogs (`> 4 GB` RAM).
3. Present findings with a 1-line overall health verdict followed by a bulleted breakdown.
