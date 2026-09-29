# Farm manager mock

Run `python3 experiments/farm-manager/farm.py`.

The JSON fixture contains one printing, one idle, and one offline printer. Output
answers which printer is free, the earliest compatible candidate, and theoretical
24-hour capacity. It is entirely static simulation: no hardware, network API,
live queue, event history, or measured telemetry.
