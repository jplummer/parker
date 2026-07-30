# Parker — a garage parking indicator

A TOF sensor, an indicator light, and a button, telling you when your car is in the right spot in the garage — no more tennis ball on a string.

Originally scoped as a possible MakerWorld crowdsource project, and simpler than the ISS pointer bot.

## Status

Phase 1 hardware bring-up is done: sensor is talking to the board over I2C, distance readings confirmed on the bench. The state machine (`code.py`) is written but not yet validated against a real car — that's the next milestone.

See [`garage-parking-indicator-design.md`](garage-parking-indicator-design.md) for the full design doc: state machine, distance parameters, power strategy trade-offs, and a running bring-up/troubleshooting log. That doc is the source of truth for how and why this thing works the way it does — this README is just the front door.

## How it works

The device sits in the garage watching for a car. Hold the button while the car is parked exactly where you want it, and it calibrates to that distance. After that, it stays low-power while the bay is empty or the car's correctly parked, wakes up as a car approaches, and gives feedback for three situations: approaching, correctly parked, and parked too far.

A future version replaces the single status LED with a row of LEDs that converge as the car gets closer, going solid green once it's in the right spot. Not built yet — see the design doc's next-steps section.

## Hardware (phase 1)

- Adafruit QT Py S3, 2MB PSRAM, STEMMA QT (product 5700)
- Adafruit VL53L1X Time of Flight sensor, STEMMA QT breakout (product 3967)
- STEMMA QT / Qwiic cable
- USB-C cable

No breadboard, no soldering, no external LEDs or button yet — everything in phase 1 runs off what's already on the dev board.

## Repo contents

- `garage-parking-indicator-design.md` — the design doc. State machine, parameters, power strategy, bring-up notes, troubleshooting log.
- `code.py` — the state machine, written for CircuitPython. First draft; hasn't been run against real car behavior yet.
- `bench_test.py` — minimal hardware smoke test. Confirms the sensor answers over the STEMMA QT bus and prints distance readings, with no state machine logic. Run this before `code.py` on any new board to isolate hardware problems from logic problems.
- `.gitignore` — excludes firmware binaries, compiled libraries, `settings.toml`, and the usual OS/editor cruft.

## Getting started

1. Flash CircuitPython onto the QT Py S3 (see the design doc's bring-up notes — there's a known bootloader/partition gotcha on the 4MB flash variant that looks like a USB problem but isn't).
2. `circup install adafruit_vl53l1x neopixel`
3. Connect the VL53L1X over STEMMA QT.
4. Copy `bench_test.py`'s contents to `code.py` on the CIRCUITPY drive and confirm distance readings over serial.
5. Swap in the real `code.py` and start testing against an actual car.

Full detail on each step, plus what to do when something goes wrong, is in the design doc.
