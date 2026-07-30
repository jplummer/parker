# Garage Parking Indicator — Design Doc

Status: phase 1 hardware bring-up done, state machine running on real hardware, two bugs found and fixed via bench testing
Last updated: 2026-07-30

## Project brief

Original idea, verbatim from the project note:

> I'd like to use a TOF sensor and some LEDs and a button to create a parking indicator for the garage. You should be able to set the correct location of the car by holding the button while your car is in the right place – the TOF sensor should be used to note the distance. The device should use power sparingly when the car is in place or gone, and when the car approaches it should indicate that it sees the car approach, when it is in the right place, and when it has gone too far. I'm not sure if it should be powered by a wall wart or by battery, or perhaps rechargeable.
>
> This might be a fun MakerWorld crowdsource project, and simpler than the ISS pointer bot.

Future direction, not built yet: the eventual display is a series of LEDs that converge as the car approaches the right spot, until the whole unit goes solid green. That's the target user experience. Everything below is groundwork for it.

## Decisions log

1. **Microcontroller**: Adafruit QT Py ESP32-C3 (product 5405) was the first pick — small, STEMMA QT connector, deep sleep around 43 µA. It's currently out of stock at Adafruit.
2. Considered QT Py RP2040 (in stock, $9.95, no WiFi/BLE, no true low-microamp deep sleep) as a replacement.
3. **Settled on QT Py S3 with 2MB PSRAM (product 5700, $12.50, in stock)** instead. Reason: Adafruit's own listing documents its GPIO0 button as usable "for entering the ROM bootloader or for user input" — a real, code-readable button after boot. The RP2040's boot-select button is wired into the flash bootloader circuit and isn't reliably readable at runtime. Since we need a calibration button and want to avoid extra hardware for phase 1, the S3's documented dual-use button wins. Deep sleep is about 70 µA — close enough to the C3 for now.
4. **TOF sensor**: Adafruit VL53L1X STEMMA QT breakout (product 3967). Range 30 mm to 4,000 mm over I2C. Connects to the QT Py with a single STEMMA QT cable — no soldering.
5. **LEDs / display**: deferred. Phase 1 uses only the QT Py's onboard NeoPixel as a stand-in for state feedback. The converging-LED-array display is a separate design task, to be scoped once state logic is proven and once we know how the final unit needs to mount and read from a driver's seat.
6. **Button hardware**: deferred, same reasoning as above — using the QT Py S3's onboard GPIO0 button for calibration during bring-up.
7. **Power source** (wall wart vs. battery vs. rechargeable): not yet decided. Deferred until we've measured real current draw from the state machine below. See "Power strategy" for the two paths under consideration.

## Phase 1 bill of materials

- Adafruit QT Py S3 with 2MB PSRAM (5700)
- Adafruit VL53L1X Time of Flight sensor, STEMMA QT breakout (3967)
- STEMMA QT / Qwiic cable, 50mm (4399)
- USB-C cable, for programming and bench power

No breadboard, no external LEDs, no external button. This is the smallest possible rig to prove the state logic.

**Ordered 2026-07-16**: VL53L1X STEMMA QT breakout, 100mm STEMMA QT cable, QT Py S3 with 2MB PSRAM. Once the sensor board is in hand, check whether GPIO1/XSHUT header pins come pre-soldered — resolves the open question under "Power strategy" below.

## State behavior

### States

| State | What's happening | Power posture |
|---|---|---|
| `EMPTY_IDLE` | No car in the bay. | MCU in light sleep, sensor polled on a slow timer. |
| `PARKED_IDLE` | Car is in the bay, in the correct spot. | MCU in light sleep, sensor polled on a slow timer. |
| `APPROACHING` | Something has entered the near zone and distance is decreasing toward home. | MCU awake, sensor polled fast, live feedback. |
| `CORRECT` | Distance has settled inside the tolerance band around home. | MCU awake briefly to confirm and acknowledge, then drops to `PARKED_IDLE`. |
| `TOO_CLOSE` | Distance has passed home and kept closing — car has overshot, sitting closer to the sensor than the calibrated spot. | MCU awake, sensor polled fast, alert feedback, stays awake until resolved. |
| `LEAVING` | Distance is opening up from a parked or too-far position. | Awake but polls at the same 1 Hz idle cadence, not the fast 150ms rate — no indication shown, drops to `EMPTY_IDLE` or recovers to `CORRECT` once clear. |
| Setting distance | Button long-press while a valid target is in range. Not a peer `state` value in code — it's a function call that runs synchronously from inside whichever state was active, then returns. | MCU awake, brief. Success: saves, 3 green blinks, forces `PARKED_IDLE`. Failure (no valid target): 3 red blinks, no save, returns to whatever state it was already in. |

### State diagram

```mermaid
stateDiagram-v2
    [*] --> EMPTY_IDLE

    EMPTY_IDLE --> APPROACHING: object closer than approach threshold
    APPROACHING --> EMPTY_IDLE: trend reverses, exits approach threshold
    APPROACHING --> CORRECT: settles in tolerance band
    APPROACHING --> TOO_CLOSE: passes home, keeps closing

    CORRECT --> PARKED_IDLE: brief acknowledgment shown

    TOO_CLOSE --> CORRECT: driver corrects position
    TOO_CLOSE --> EMPTY_IDLE: backs out past approach threshold

    PARKED_IDLE --> LEAVING: distance exits tolerance band
    LEAVING --> EMPTY_IDLE: distance exceeds approach threshold
    LEAVING --> CORRECT: distance re-enters tolerance band

    EMPTY_IDLE --> SettingDistance: button long-press
    PARKED_IDLE --> SettingDistance: button long-press
    SettingDistance --> PARKED_IDLE: valid target - saved, 3 green blinks
    SettingDistance --> EMPTY_IDLE: no valid target - not saved, 3 red blinks, unchanged
    SettingDistance --> PARKED_IDLE: no valid target - not saved, 3 red blinks, unchanged
```

("Setting distance" isn't a peer state the way the others are - it's a function call, not a branch of the state variable. Its two "no valid target" arrows above just mean "returns to whichever state it was called from, unchanged"; they aren't really new states to reach.)

Kept as Mermaid rather than draw.io on purpose: it's text, so it lives in this file and changes with the design instead of drifting out of sync in a separate binary. Most markdown viewers (GitHub, VS Code, many note apps) render it inline. If you want a polished version for a presentation later, draw.io is the better tool for that specific job — but for a working doc that changes weekly, text-as-diagram wins.

### Transitions

Starting from `EMPTY_IDLE`: the sensor watches for anything closer than the approach threshold. When it sees one, wake into `APPROACHING`.

From `APPROACHING`: if distance keeps decreasing and settles within the tolerance band around home for the debounce period, move to `CORRECT`, acknowledge, then `PARKED_IDLE`. If distance decreases past the near edge of the tolerance band, move to `TOO_CLOSE` and stay awake — a driver mid-overshoot needs live feedback, not a device that's gone back to sleep. If instead the distance trend reverses and grows back past the approach threshold — someone pulled up, changed their mind, and backed out — drop straight back to `EMPTY_IDLE` with no indication. That's the "ignore a car that's leaving" case: it never reached `CORRECT` or `TOO_CLOSE`, so there's nothing to walk back.

Renamed from `TOO_FAR` to `TOO_CLOSE`: the old name described the driver's frame of reference (pulled too far into the garage), but the state itself is defined purely by the sensor's reading — distance has gotten *closer* to the sensor than the calibrated spot. `TOO_FAR` reads backwards next to a distance value that's shrinking. `TOO_CLOSE` matches what the number is actually doing, and stays correct no matter which way the device ends up mounted.

From `PARKED_IDLE`: the sensor watches for distance leaving the tolerance band. When it does, wake into `LEAVING`. Show nothing — a departing car doesn't need approach or overshoot feedback, those only make sense for an arrival. Stay in `LEAVING`, unlit, until distance exceeds the approach threshold, then drop to `EMPTY_IDLE` — unless distance re-enters the tolerance band first, in which case recover straight to `CORRECT`.

Bench-testing caught a real bug in the first draft here: `LEAVING` only checked for the distance exceeding the approach threshold, with no path back to `CORRECT`. A car (or a hand, on the bench) that drifts out of tolerance and settles back into place — without ever crossing the much larger approach threshold — got stuck showing nothing indefinitely, because the only way out of `LEAVING` was a departure that was never actually happening. Fixed by checking `in_tolerance` first, same pattern already used in `TOO_CLOSE`.

From `TOO_CLOSE`: if the driver corrects and distance moves back into the tolerance band, go to `CORRECT`. If they give up and back out past the approach threshold, go to `EMPTY_IDLE`, same as any other departure.

The key trick, and the reason this doesn't need velocity sensing or anything fancy: the meaning of a distance reading depends entirely on which idle state we woke from. Waking from `EMPTY_IDLE` means someone's arriving, so we run the approach/correct/too-far logic. Waking from `PARKED_IDLE` means someone's leaving, so we suppress all of that and just wait for the bay to clear. Same sensor, same readings, different interpretation based on context.

### Distance parameters (starting points, to be tuned on the bench)

- **Home distance** (`D_home`): set by calibration.
- **Tolerance band**: ± 75 mm around `D_home`. This is the `CORRECT` zone.
- **Approach threshold**: `D_home` + 1.5 m. Anything closer than this, when the bay was empty, counts as an arrival in progress. This needs tuning against actual garage depth — a shallow bay may need a smaller margin.
- **Settle debounce**: distance must stay inside the tolerance band for 1.5 seconds before declaring `CORRECT`. Avoids flicker from a car still creeping or from sensor noise at the boundary.
- **Departure debounce**: distance must stay outside the approach threshold for 1 second before declaring `EMPTY_IDLE`. Avoids a false "gone" from a single bad reading.
- **Calibration hold time**: 2 seconds.

None of these are validated yet — they're reasonable starting guesses, not measurements. First bench session should log real distance traces for a car pulling in, parking, and pulling out, so these numbers can be set from data instead of intuition.

**Idle poll rate** (`IDLE_POLL_S`, currently 1.0 s): this sets how often the sensor is checked in `EMPTY_IDLE`, `PARKED_IDLE`, and `LEAVING`. A car doesn't appear or finish leaving in under a second, so this could likely slow to 2–3 s without missing anything real, trading a little responsiveness for less time spent awake per hour. Worth revisiting once current-draw measurements are in — the power difference across a whole day of mostly-idle time adds up more than the fast states do.

### Calibration

Button long-press while the car is parked where you want it. On release after the hold threshold, read the current distance, validate it's a real in-range reading (30 mm–4,000 mm, not a "no target" result), store it as `D_home` in non-volatile memory so it survives power loss, and acknowledge with the NeoPixel — a short green flash sequence for now, standing in for whatever the final acknowledgment looks like on the real display. If the reading is invalid (no target in range), skip the save and flash a different color to signal the calibration didn't take.

Second bug the diagram review caught: the function that runs this used to report success back to the main loop regardless of whether the save actually happened — a failed attempt (no target in range) still forced a jump to `PARKED_IDLE`. On a first-ever calibration attempt that failed, that meant landing in `PARKED_IDLE` with `D_home` still unset, and the very next distance comparison would have crashed. Fixed: it now only reports success when a distance was actually saved; a failed attempt returns to whatever state it was called from, unchanged.

### Power strategy — open question

Two ways to keep this low-power, and they trade off differently:

**Option A — sensor-driven interrupt wake.** The VL53L1X can run fully autonomously: it takes its own readings on a timer (as slow as 1 Hz), compares against a programmed threshold window, and only pulls its interrupt pin (GPIO1) low when the target crosses that threshold. The host MCU can be in deep sleep the entire time and only wake on that interrupt. ST's own numbers put this around 65 µA at 1 Hz. Combined with the QT Py S3's own ~70 µA deep sleep, total idle draw would land somewhere near 150 µA — good battery life. The catch: GPIO1 (and XSHUT, for controlling sensor power) aren't part of the STEMMA QT connector, which only carries I2C and power. Using this mode needs a wire from the sensor board's GPIO1 pin to a QT Py GPIO pin — meaning either the breakout ships with header pins pre-installed (needs checking on the product page before ordering) or it needs soldering. That conflicts with the no-solder goal for a future kit.

**Option B — timer-driven polling, I2C only.** Skip the interrupt pin entirely. The MCU wakes itself on an internal timer (once a second while idle), takes a single distance reading over I2C — the same STEMMA QT cable already in use — decides what state that implies, and goes back to sleep. Simpler, works with the plug-and-play cable alone, no extra wiring ever.

Correction to the first draft of this doc: this needs CircuitPython's **light sleep**, not deep sleep. Deep sleep shuts down the CPU and RAM entirely — `code.py` restarts from the top on every wake, which means the running state machine would need to be reconstructed from scratch every single second. Light sleep pauses execution and resumes exactly where it left off, so the in-memory state (which state we're in, current readings) survives naturally. It costs more than deep sleep — light sleep on the S3 draws roughly 2–4 mA versus deep sleep's ~70 µA — but at a once-a-second wake cadence, deep sleep's reboot overhead would dominate anyway. Deep sleep only starts to make sense if Option A's multi-second-or-longer, interrupt-driven wake pattern is adopted later. `D_home` is still written to non-volatile memory regardless, since that needs to survive a full power cycle, not just a sleep cycle.

Recommendation: **build phase 1 on Option B.** It matches the "STEMMA QT cable only, no soldering" constraint that a kit will need anyway, and it's simpler to get right first. If bench testing shows the battery life isn't good enough, revisit Option A as a deliberate power upgrade, and decide then whether the extra wire is worth it or whether a different sensor breakout with GPIO1 broken out to a second STEMMA QT connector exists.

## Open questions

- Real garage bay depth and sensor mount position — needed to set the approach threshold sensibly, and to pick the VL53L1X distance mode (short/medium/long) and timing budget that covers the actual range needed without wasting power on unnecessary accuracy.
- Whether the VL53L1X STEMMA QT breakout ships with GPIO1/XSHUT header pins pre-soldered, in case Option A becomes necessary later.
- Confirmed, not open anymore: a false target — a person walking through, a bicycle leaned against the wall — needs no special handling. See "Future considerations" below.
- Power source decision (wall wart / battery / rechargeable) is blocked on real current measurements from the state machine above.
- Sensor mount orientation (facing the door vs. facing something else) — see "Future considerations" below.

## Future considerations: sensor mount orientation

Raised as a brainstorm 2026-07-30, not designed or coded yet. Two likely mounting positions:

- **Facing the garage door.** The sensor's empty-bay baseline reading is the closed door. When the door opens, that baseline usually jumps to a much longer reading first — the beam now reaches past the door to the driveway or street — before the car itself ever comes into view. That's a second, earlier signal than "car approaching."
- **Facing something else** (a wall, a shelf, the far end of the bay). The sensor never sees the door at all. The baseline is just whatever's in front of it, and the only signal available is the car breaking that baseline by getting closer — the logic already built for phase 1.

Rather than build two modes and make the person choose one during setup, the design should adapt on its own. The idea: instead of only watching for the reading to get closer than the approach threshold (today's logic), also watch the same reading for any deviation from the idle-state baseline in *either* direction — closer or farther — beyond ordinary sensor noise, and treat any deviation as a reason to poll faster and pay closer attention. In the door-facing case, that catches the door opening as an early signal, well before the car is close enough to trip the existing approach logic. In the wall-facing case there's no farther-direction event to catch, so the existing closer-direction logic does all the work — the code doesn't need to know or be told which mount case it's in.

Caveat: door-opening jumps aren't reliably large. Usually the driveway or street beyond is much farther than the closed door, so the jump is big and easy to catch. But if a car is already idling close behind the door, waiting for it to open, the reading only grows by roughly the door's own thickness — a small jump, easy to miss or mistake for noise. Whatever deviation threshold gets picked needs to tolerate that case without false-triggering on ordinary sensor jitter.

This dovetails with the `IDLE_POLL_S` tuning note above: stay at the slow idle rate right up until a deviation is detected, then step polling up, rather than running fast continuously on the chance a door might open.

Confirmed, not a concern: people or objects passing through the bay, with or without a car present, don't need special handling either way. They look like any other approach — the state machine wakes, doesn't settle within tolerance, and times back out to `EMPTY_IDLE`. Costs a wake cycle, not correctness. Same reasoning covers a false door-jump trigger above: worst case is an extra wake cycle spent watching for a car that never comes.

Not designed further than this — no new state, no tuned threshold, no code — until phase 1's core car-facing logic is validated on the bench. How the idle baseline itself gets established and kept current (a fixed value learned once vs. something that adapts if, say, a car ends up parked outside a window in the sensor's view) is itself an open question once this gets built.

## Bring-up notes (confirmed against Adafruit's QT Py S3 pinout guide)

- **macOS + UF2 drag-and-drop**: Finder can fail with "the device disappeared" when dragging a `.uf2` file onto `QTPYS3BOOT`. Sometimes this is a benign Finder/extended-attributes race (check if `CIRCUITPY` appeared anyway; if not, `cp -X path/to/firmware.uf2 /Volumes/QTPYS3BOOT/` from Terminal is the fix). **But if the write drops consistently, even direct-connected and even via `cp -X`, the real cause on this board is something else entirely — see next point.**
- **4MB board + CircuitPython 10.x bootloader mismatch**: our board is the 4MB Flash / 2MB PSRAM variant. CircuitPython 10.0.0 and later use a new single 2.8MB firmware partition layout on 4MB Espressif boards; the factory-shipped bootloader still expects the old two-partition layout. Loading a CircuitPython 10.x `.uf2` onto the old bootloader causes the write to fail partway through (macOS reports `fcopyfile failed: Input/output error` and/or "the device disappeared") — this looks identical to a cable/hub/Finder problem but isn't one. Fix: update the bootloader first via the OPEN INSTALLER button on the board's page at circuitpython.org (Chrome or Firefox only, not Safari — needs WebSerial). This erases the board and installs bootloader 0.33.0+ with the new partition layout. After that, double-tap reset, confirm `INFO_UF2.TXT` on `QTPYS3BOOT` shows 0.33.0 or later, then load CircuitPython normally.

- `board.STEMMA_I2C()` and `board.NEOPIXEL` are correct as used in `code.py` — confirmed against the official pinout page.
- Important gotcha: the STEMMA QT connector is a **second, separate I2C bus** (`SCL1`/`SDA1`), not the same as the `board.SCL`/`board.SDA` header pins. `board.STEMMA_I2C()` addresses the right one. Don't wire anything to the SCL/SDA header pads expecting it to talk to the STEMMA QT sensor — it won't.
- The GPIO0 "boot" button is confirmed reusable as a plain input-with-pullup after boot. Confirmed via REPL (`import board; print(dir(board))`) that the correct constant on this board is `board.BUTTON` — `code.py` already uses it.

## Next steps

1. `bench_test.py` in this same folder is a minimal hardware smoke test — just confirms the VL53L1X answers over the STEMMA QT bus and prints a distance reading once a second, no state machine. Run this first.
2. `code.py` is the first-draft state machine. **Not yet run on real hardware.** Once `bench_test.py` confirms the sensor is talking, install `adafruit_vl53l1x` and `neopixel` to CIRCUITPY/lib (`circup install adafruit_vl53l1x neopixel` is the easy path), confirm the button pin name per above, then load `code.py`.
3. Bench-test with an actual car (or a stand-in object on a cart) to validate the distance parameters and debounce timings in the doc above — they're starting guesses, not measurements.
4. Measure real current draw in each state to inform the power source decision.
5. Scope the converging-LED-array display as its own design task.
