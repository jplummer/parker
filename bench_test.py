# Minimal hardware smoke test for the garage parking indicator prototype.
#
# Confirms the VL53L1X is reachable over the STEMMA QT I2C bus and that the
# onboard NeoPixel responds - nothing else. Run this before code.py so a
# hardware problem (bad cable, wrong address) and a state-machine problem
# never get confused with each other.
#
# To run: copy this file's contents into code.py on the CIRCUITPY drive,
# open the serial console (Mu, or `screen`/`tio` on the board's serial
# port), and watch the output. Ctrl-C in the console stops it.
#
# Needs adafruit_vl53l1x and neopixel installed to CIRCUITPY/lib first:
#   pip install circup
#   circup install adafruit_vl53l1x neopixel

import time

import board
import neopixel
import adafruit_vl53l1x

i2c = board.STEMMA_I2C()  # the QT Py S3's second I2C bus, dedicated to the
                           # STEMMA QT connector - separate from board.SCL/SDA
vl53 = adafruit_vl53l1x.VL53L1X(i2c)
vl53.distance_mode = 2  # long mode, up to ~360cm
vl53.timing_budget = 100
vl53.start_ranging()

pixel = neopixel.NeoPixel(board.NEOPIXEL, 1, brightness=0.2)

print("VL53L1X found over STEMMA QT I2C. Reading distance once a second.")

while True:
    if vl53.data_ready:
        d_cm = vl53.distance
        vl53.clear_interrupt()
        if d_cm is None:
            print("no target in range")
            pixel.fill((0, 0, 0))
        else:
            print("{:.1f} cm".format(d_cm))
            pixel.fill((0, 0, 40))  # dim blue = got a reading
    time.sleep(1.0)
