#!/usr/bin/env python3
"""Ví dụ cơ bản: GPIO, ADC, PWM, I2C, Servo với armkali co-processor."""

import time
import sys

sys.path.insert(0, "..")
from lib.coprocessor import CoProcessor, CoProcessorError


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyS1"
    co = CoProcessor(port)

    try:
        print(f"[+] Firmware: {co.version()}")

        # GPIO output — bật LED trên GP5
        print("\n[*] GPIO output — blink LED on GP5")
        co.pin_mode(5, "OUT")
        for i in range(3):
            co.digital_write(5, 1)
            time.sleep(0.3)
            co.digital_write(5, 0)
            time.sleep(0.3)

        # GPIO input — đọc button trên GP6 (pull-up)
        print("[*] GPIO input — read button on GP6 (pull-up)")
        co.pin_mode(6, "IN UP")
        print(f"    GP6 = {co.digital_read(6)}")

        # PWM — dim LED trên GP7
        print("[*] PWM — fade LED on GP7")
        for duty in range(0, 65536, 5000):
            co.pwm(7, 1000, duty)
            time.sleep(0.05)
        co.pwm_off(7)

        # ADC — đọc potentiometer trên GP26
        print("[*] ADC — read pot on GP26")
        raw = co.analog_read(26)
        mv = co.analog_read(26, mv=True)
        print(f"    raw={raw} ({mv} mV)")

        # I2C — scan bus
        print("[*] I2C scan")
        devs = co.i2c_scan()
        print(f"    found: {['0x%02x' % d for d in devs]}")

        # I2C — đọc BME280 chip ID (0x60) tại 0x76/0xD0
        if 0x76 in devs or 0x77 in devs:
            addr = 0x76 if 0x76 in devs else 0x77
            chip_id = co.i2c_read(addr, 0xD0, 1)
            print(f"    BME280 chip ID: 0x{chip_id[0]:02x} (expected 0x60)")

        # Servo — quay 0 → 180 → 90
        print("[*] Servo on GP8")
        for angle in [0, 90, 180, 90]:
            co.servo(8, angle)
            time.sleep(0.8)
        co.servo_off(8)

        # Watchdog — RP2040 tự reset nếu host không PING trong 8s
        print("[*] Watchdog armed (8s)")
        co.watchdog(8000)
        for _ in range(3):
            co.ping()
            time.sleep(2)
        co.watchdog(0)

        # Push notifications — theo dõi GP6
        print("[*] Push — watching GP6 edges (press button 3x)")

        def on_gpio(*args):
            print(f"    PUSH GPIO pin={args[0]} val={args[1]}")

        co.on("GPIO", on_gpio)
        co.watch(6, "BOTH")
        co.listen()
        time.sleep(10)

    except CoProcessorError as e:
        print(f"[-] error: {e}")
    except KeyboardInterrupt:
        print("\n[*] interrupted")
    finally:
        co.close()


if __name__ == "__main__":
    main()
