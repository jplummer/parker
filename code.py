# Garage Parking Indicator - phase 1 state machine
# Hardware: QT Py S3 (2MB PSRAM) + VL53L1X (STEMMA QT) + onboard NeoPixel
#           + GPIO0 "boot" button, read as a plain input for calibration.
# See garage-parking-indicator-design.md for the full design rationale.
#
# STATUS: first draft, written against library docs, not yet run on hardware.
# Verify against real parts before trusting any of this, especially:
#   - board.STEMMA_I2C() / board.NEOPIXEL / board.BUTTON pin names for this
#     specific board (confirm against the QT Py S3 CircuitPython pinout)
#   - vl53l1x distance_mode / timing_budget valid ranges
#   - button polarity (assumed active-low with internal pull-up)
#
# Needs these libraries in CIRCUITPY/lib:
#   adafruit_vl53l1x.mpy
#   neopixel.mpy

import struct
import time

import adafruit_vl53l1x
import alarm
import board
import digitalio
import microcontroller
import neopixel

# ---------------------------------------------------------------------------
# Tunable parameters - see design doc "Distance parameters" for rationale.
# These are starting guesses, not measured values. Expect to retune on the
# bench once real distance traces are logged.
# ---------------------------------------------------------------------------
TOLERANCE_MM = 75  # +/- around home = "correct" zone
APPROACH_MARGIN_MM = 1500  # how far out "approaching" starts, beyond home
SETTLE_DEBOUNCE_S = 1.5  # must hold inside tolerance this long to call it CORRECT
DEPART_DEBOUNCE_S = 1.0  # must hold outside approach zone this long to call it EMPTY
CAL_HOLD_S = 2.0  # button hold time to trigger calibration

IDLE_POLL_S = 1.0  # how often we wake to check distance while idle
ACTIVE_POLL_S = 0.15  # how often we sample while actively tracking a car

# VL53L1X distance_mode: 1 = short (<=136cm), 2 = long (<=360cm).
# Long covers more garages; short is more tolerant of bright ambient light.
# Pick based on actual mount distance once measured.
DISTANCE_MODE = 2
TIMING_BUDGET_MS = 100

BRIGHTNESS = 0.2

COLOR_OFF = (0, 0, 0)
COLOR_APPROACH = (40, 40, 255)  # blue: something's coming
COLOR_CORRECT = (0, 255, 0)  # green: right spot
COLOR_TOO_FAR = (255, 20, 0)  # red: overshot
COLOR_CAL_OK = (0, 255, 0)
COLOR_CAL_FAIL = (255, 0, 0)

# Debug prints. Flip to False once things are working - printing over USB
# serial has a small cost and isn't needed once the state machine is trusted.
DEBUG = True


def debug(msg):
    if DEBUG:
        print(msg)

# ---------------------------------------------------------------------------
# Non-volatile storage for the calibrated home distance.
# Survives power loss, unlike anything held in RAM. Layout:
#   byte 0:   0xA5 marker if a calibration has been saved, else unset
#   bytes 1-4: home distance in mm, packed as a little-endian float
# ---------------------------------------------------------------------------
NVM_MARKER = 0xA5


def load_home_distance():
    data = microcontroller.nvm[0:5]
    if data[0] != NVM_MARKER:
        return None
    return struct.unpack("<f", bytes(data[1:5]))[0]


def save_home_distance(mm):
    packed = struct.pack("<f", mm)
    microcontroller.nvm[0:5] = bytes([NVM_MARKER]) + packed


# ---------------------------------------------------------------------------
# Hardware setup
# ---------------------------------------------------------------------------
i2c = board.STEMMA_I2C()
vl53 = adafruit_vl53l1x.VL53L1X(i2c)
vl53.distance_mode = DISTANCE_MODE
vl53.timing_budget = TIMING_BUDGET_MS
vl53.start_ranging()

pixel = neopixel.NeoPixel(board.NEOPIXEL, 1, brightness=BRIGHTNESS)

# GPIO0 / "BOOT" button. Adafruit's product notes describe this pin as usable
# for user input after boot, in addition to its bootloader-select role.
# Confirm the correct board.* pin name once the board's pinout doc is in hand
# — this may be board.BUTTON, board.BOOT, or board.D0 depending on the
# CircuitPython build.
button = digitalio.DigitalInOut(board.BUTTON)
button.switch_to_input(pull=digitalio.Pull.UP)  # assumed active-low


def button_pressed():
    return not button.value


def show_color(rgb):
    pixel.fill(rgb)


def blink(color, times):
    for _ in range(times):
        show_color(color)
        time.sleep(0.15)
        show_color(COLOR_OFF)
        time.sleep(0.15)


def read_distance_mm():
    """Block until a fresh reading is ready, return distance in mm,
    or None if no valid target was found."""
    while not vl53.data_ready:
        time.sleep(0.005)
    d_cm = vl53.distance  # library reports centimeters
    vl53.clear_interrupt()
    if d_cm is None:
        return None
    return d_cm * 10.0


def light_sleep(seconds):
    """Pause execution without losing RAM state. Cheaper than staying fully
    awake, much cheaper to resume than deep sleep. See design doc for why
    deep sleep is wrong for a once-a-second poll cadence."""
    time_alarm = alarm.time.TimeAlarm(monotonic_time=time.monotonic() + seconds)
    alarm.light_sleep_until_alarms(time_alarm)


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------
def try_calibrate():
    """Call as soon as the button is observed pressed. Blocks until release.
    Only saves if held past CAL_HOLD_S with a valid target in range."""
    debug("button pressed, waiting for {}s hold to calibrate".format(CAL_HOLD_S))
    press_start = time.monotonic()
    while button_pressed():
        if time.monotonic() - press_start >= CAL_HOLD_S:
            d = read_distance_mm()
            debug("hold threshold reached, distance={}".format(d))
            if d is not None:
                save_home_distance(d)
                debug("calibrated: home_mm={}".format(d))
                blink(COLOR_CAL_OK, 3)
            else:
                debug("calibration failed: no valid target")
                blink(COLOR_CAL_FAIL, 3)
            while button_pressed():
                time.sleep(0.01)
            return True
        time.sleep(0.01)
    debug("button released before hold threshold - no calibration")
    return False  # released before the hold threshold - not a calibration


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------
STATE_EMPTY_IDLE = "EMPTY_IDLE"
STATE_PARKED_IDLE = "PARKED_IDLE"
STATE_APPROACHING = "APPROACHING"
STATE_CORRECT = "CORRECT"
STATE_TOO_FAR = "TOO_FAR"
STATE_LEAVING = "LEAVING"


def in_tolerance(d_mm, home_mm):
    return d_mm is not None and abs(d_mm - home_mm) <= TOLERANCE_MM


def within_approach(d_mm, home_mm):
    return d_mm is not None and d_mm <= (home_mm + APPROACH_MARGIN_MM)


def run():
    home_mm = load_home_distance()
    state = STATE_EMPTY_IDLE if home_mm is None else STATE_PARKED_IDLE
    debug("startup: state={} home_mm={}".format(state, home_mm))

    while True:
        # Calibration can happen from any idle moment, regardless of state.
        if button_pressed():
            if try_calibrate():
                home_mm = load_home_distance()
                state = STATE_PARKED_IDLE
                debug("-> PARKED_IDLE (just calibrated)")
            continue

        if state == STATE_EMPTY_IDLE:
            if home_mm is None:
                # Nothing to compare against yet - just wait for calibration.
                light_sleep(IDLE_POLL_S)
                continue
            d = read_distance_mm()
            debug("EMPTY_IDLE d={}".format(d))
            if within_approach(d, home_mm):
                state = STATE_APPROACHING
                debug("-> APPROACHING")
            else:
                light_sleep(IDLE_POLL_S)

        elif state == STATE_PARKED_IDLE:
            d = read_distance_mm()
            debug("PARKED_IDLE d={}".format(d))
            if not in_tolerance(d, home_mm):
                state = STATE_LEAVING
                debug("-> LEAVING")
            else:
                light_sleep(IDLE_POLL_S)

        elif state == STATE_APPROACHING:
            show_color(COLOR_APPROACH)
            settle_start = None
            while True:
                if button_pressed():
                    break
                d = read_distance_mm()
                debug("APPROACHING d={}".format(d))
                if not within_approach(d, home_mm):
                    # Backed out or gave up before arriving. No indication -
                    # this is the "ignore a car that's leaving" case; it
                    # never reached CORRECT or TOO_FAR, so there's nothing
                    # to walk back.
                    state = STATE_EMPTY_IDLE
                    show_color(COLOR_OFF)
                    debug("-> EMPTY_IDLE (backed out before arriving)")
                    break
                if in_tolerance(d, home_mm):
                    if settle_start is None:
                        settle_start = time.monotonic()
                    elif time.monotonic() - settle_start >= SETTLE_DEBOUNCE_S:
                        state = STATE_CORRECT
                        debug("-> CORRECT")
                        break
                elif d < home_mm - TOLERANCE_MM:
                    state = STATE_TOO_FAR
                    debug("-> TOO_FAR")
                    break
                else:
                    settle_start = None  # bounced back out of the band, reset
                time.sleep(ACTIVE_POLL_S)

        elif state == STATE_CORRECT:
            show_color(COLOR_CORRECT)
            time.sleep(1.0)  # brief acknowledgment before going quiet
            show_color(COLOR_OFF)
            state = STATE_PARKED_IDLE
            debug("-> PARKED_IDLE")

        elif state == STATE_TOO_FAR:
            show_color(COLOR_TOO_FAR)
            while True:
                if button_pressed():
                    break
                d = read_distance_mm()
                debug("TOO_FAR d={}".format(d))
                if not within_approach(d, home_mm):
                    state = STATE_EMPTY_IDLE
                    show_color(COLOR_OFF)
                    debug("-> EMPTY_IDLE (backed all the way out)")
                    break
                if in_tolerance(d, home_mm):
                    state = STATE_CORRECT
                    debug("-> CORRECT (corrected from too far)")
                    break
                time.sleep(ACTIVE_POLL_S)

        elif state == STATE_LEAVING:
            # Deliberately unlit. A departing car doesn't need approach or
            # overshoot feedback - those only make sense for an arrival.
            show_color(COLOR_OFF)
            depart_start = None
            while True:
                if button_pressed():
                    break
                d = read_distance_mm()
                debug("LEAVING d={}".format(d))
                if not within_approach(d, home_mm):
                    if depart_start is None:
                        depart_start = time.monotonic()
                    elif time.monotonic() - depart_start >= DEPART_DEBOUNCE_S:
                        state = STATE_EMPTY_IDLE
                        debug("-> EMPTY_IDLE")
                        break
                else:
                    depart_start = None  # car paused or crept back in, reset
                time.sleep(IDLE_POLL_S)


run()
