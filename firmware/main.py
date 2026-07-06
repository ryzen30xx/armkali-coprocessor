# armkali co-processor firmware — MicroPython for RP2040
# Giao tiếp với x96q qua UART 115200 8N1
# Tránh FEL race: giữ TX idle LOW cho đến khi nhận lệnh đầu tiên từ host.

import sys
import time

from machine import ADC, I2C, PWM, Pin, UART, WDT, reset

VERSION = "armkali-coproc 1.0.0"
BAUD = 115200
TX_PIN = 0
RX_PIN = 1

# Giữ TX ở LOW ban đầu (GPIO output) để tránh H313 BootROM trigger FEL mode.
# Chỉ chuyển TX_PIN sang chức năng UART TX khi nhận byte đầu tiên từ host.
_tx_pin = Pin(TX_PIN, Pin.OUT, value=0)
tx_armed = False

# UART init với timeout=0 (non-blocking read). TX sẽ không được kích hoạt
# cho đến khi arm_tx() được gọi — đảm bảo TX line im lặng lúc boot.
uart = UART(0, baudrate=BAUD, tx=Pin(TX_PIN), rx=Pin(RX_PIN), timeout=0, timeout_char=10)

pins = {}
pwms = {}
i2c = None
wdt = None
watched_pins = []


def arm_tx():
    """Kích hoạt UART TX — gọi 1 lần khi nhận byte đầu tiên từ host."""
    global tx_armed
    if tx_armed:
        return
    # Re-init UART để GP0 chuyển từ GPIO OUT sang UART TX function
    uart.init(baudrate=BAUD, tx=Pin(TX_PIN), rx=Pin(RX_PIN), timeout=0, timeout_char=10)
    tx_armed = True


def reply(s):
    uart.write(str(s) + "\n")


def parse_hex_bytes(s):
    """Parse '0xD0:0x01:0xFF' hoặc 'D0 01 FF' → bytes."""
    if ":" in s:
        parts = s.split(":")
    else:
        parts = s.split()
    return bytes([int(p, 16) for p in parts])


def cmd_ping(_):
    reply("OK PONG")


def cmd_version(_):
    reply("OK " + VERSION)


def cmd_reset(_):
    reply("OK resetting")
    time.sleep_ms(50)
    reset()


def cmd_gpio(args):
    if len(args) < 2:
        reply("ERR usage: GPIO <pin> <IN|OUT|HIGH|LOW|READ|PULL|WATCH>")
        return
    try:
        pin_num = int(args[0])
        action = args[1].upper()
    except ValueError:
        reply("ERR bad pin number")
        return

    if action == "IN":
        pull_mode = args[2].upper() if len(args) > 2 else None
        pull = None
        if pull_mode == "UP":
            pull = Pin.PULL_UP
        elif pull_mode == "DOWN":
            pull = Pin.PULL_DOWN
        pins[pin_num] = Pin(pin_num, Pin.IN, pull=pull)
        reply("OK")
    elif action == "OUT":
        pins[pin_num] = Pin(pin_num, Pin.OUT)
        reply("OK")
    elif action == "HIGH":
        if pin_num not in pins:
            pins[pin_num] = Pin(pin_num, Pin.OUT)
        pins[pin_num].value(1)
        reply("OK")
    elif action == "LOW":
        if pin_num not in pins:
            pins[pin_num] = Pin(pin_num, Pin.OUT)
        pins[pin_num].value(0)
        reply("OK")
    elif action == "READ":
        if pin_num not in pins:
            pins[pin_num] = Pin(pin_num, Pin.IN)
        reply("OK %d" % pins[pin_num].value())
    elif action == "PULL":
        if len(args) < 3:
            reply("ERR usage: GPIO <pin> PULL <UP|DOWN|NONE>")
            return
        mode = args[2].upper()
        pull_map = {"UP": Pin.PULL_UP, "DOWN": Pin.PULL_DOWN, "NONE": None}
        if mode not in pull_map:
            reply("ERR bad pull mode")
            return
        if pin_num not in pins:
            pins[pin_num] = Pin(pin_num, Pin.IN, pull=pull_map[mode])
        else:
            pins[pin_num].init(mode=Pin.IN, pull=pull_map[mode])
        reply("OK")
    elif action == "WATCH":
        if len(args) < 3:
            reply("ERR usage: GPIO <pin> WATCH <RISE|FALL|BOTH>")
            return
        edge = args[2].upper()
        trigger_map = {"RISE": Pin.IRQ_RISING, "FALL": Pin.IRQ_FALLING, "BOTH": Pin.IRQ_RISING | Pin.IRQ_FALLING}
        if edge not in trigger_map:
            reply("ERR bad edge")
            return
        if pin_num not in pins:
            pins[pin_num] = Pin(pin_num, Pin.IN)

        def _irq(p):
            reply("PUSH GPIO %d %d" % (p, pins[p].value()))

        pins[pin_num].irq(trigger=trigger_map[edge], handler=lambda p=pin_num: _irq(p))
        watched_pins.append(pin_num)
        reply("OK")
    else:
        reply("ERR unknown GPIO action")


def cmd_pwm(args):
    if len(args) < 2:
        reply("ERR usage: PWM <pin> <freq> <duty> | PWM <pin> OFF")
        return
    try:
        pin_num = int(args[0])
    except ValueError:
        reply("ERR bad pin")
        return
    action = args[1].upper()
    if action == "OFF":
        if pin_num in pwms:
            pwms[pin_num].deinit()
            del pwms[pin_num]
        reply("OK")
        return
    if len(args) < 3:
        reply("ERR need freq and duty")
        return
    try:
        freq = int(action)
        duty = int(args[2])
    except ValueError:
        reply("ERR bad freq/duty")
        return
    if pin_num in pwms:
        pwms[pin_num].deinit()
    p = PWM(Pin(pin_num))
    p.freq(max(1, freq))
    p.duty_u16(max(0, min(65535, duty)))
    pwms[pin_num] = p
    reply("OK")


def cmd_adc(args):
    if not args:
        reply("ERR usage: ADC <pin> [MV]")
        return
    try:
        pin_num = int(args[0])
    except ValueError:
        reply("ERR bad pin")
        return
    if pin_num not in (26, 27, 28, 29):
        reply("ERR ADC pin must be 26-29")
        return
    adc = ADC(Pin(pin_num))
    raw = adc.read_u16()
    if len(args) > 1 and args[1].upper() == "MV":
        mv = raw * 3300 // 65535
        reply("OK %d" % mv)
    else:
        reply("OK %d" % raw)


def cmd_i2c(args):
    global i2c
    if not args:
        reply("ERR usage: I2C <R|W|SCAN> ...")
        return
    if i2c is None:
        i2c = I2C(0, scl=Pin(5), sda=Pin(4), freq=400_000)

    op = args[0].upper()
    if op == "SCAN":
        devs = i2c.scan()
        reply("OK " + " ".join("0x%02X" % d for d in devs))
    elif op == "R":
        if len(args) < 4:
            reply("ERR usage: I2C R <addr> <reg> <nbytes>")
            return
        try:
            addr = int(args[1], 0)
            reg = int(args[2], 0)
            n = int(args[3])
        except ValueError:
            reply("ERR bad args")
            return
        try:
            buf = i2c.readfrom_mem(addr, reg, n)
            reply("OK " + " ".join("%02X" % b for b in buf))
        except OSError as e:
            reply("ERR i2c: %s" % e)
    elif op == "W":
        if len(args) < 3:
            reply("ERR usage: I2C W <addr> <aa:bb:cc...>")
            return
        try:
            addr = int(args[1], 0)
            data = parse_hex_bytes(args[2])
        except ValueError:
            reply("ERR bad hex")
            return
        try:
            i2c.writeto(addr, data)
            reply("OK")
        except OSError as e:
            reply("ERR i2c: %s" % e)
    else:
        reply("ERR unknown I2C op")


def cmd_servo(args):
    if len(args) < 2:
        reply("ERR usage: SERVO <pin> <angle|OFF>")
        return
    try:
        pin_num = int(args[0])
    except ValueError:
        reply("ERR bad pin")
        return
    action = args[1].upper()
    if action == "OFF":
        if pin_num in pwms:
            pwms[pin_num].deinit()
            del pwms[pin_num]
        reply("OK")
        return
    try:
        angle = int(action)
    except ValueError:
        reply("ERR bad angle")
        return
    angle = max(0, min(180, angle))
    us = 500 + (angle * 2000) // 180
    if pin_num in pwms:
        pwms[pin_num].deinit()
    p = PWM(Pin(pin_num))
    p.freq(50)
    p.duty_u16((us * 65535) // 1_000_000)
    pwms[pin_num] = p
    reply("OK")


def cmd_dht(args):
    if not args:
        reply("ERR usage: DHT <pin>")
        return
    try:
        from dht import DHT22
    except ImportError:
        reply("ERR dht module not available")
        return
    try:
        pin_num = int(args[0])
        sensor = DHT22(Pin(pin_num))
        sensor.measure()
        reply("OK %.1f %.1f" % (sensor.temperature(), sensor.humidity()))
    except Exception as e:
        reply("ERR dht: %s" % e)


def cmd_ping_us(args):
    """HC-SR04 ultrasonic distance sensor: PING_US <trig> <echo>"""
    if len(args) < 2:
        reply("ERR usage: PING_US <trig> <echo>")
        return
    try:
        from hcsr04 import HCSR04
    except ImportError:
        reply("ERR hcsr04 module not installed")
        return
    try:
        trig = int(args[0])
        echo = int(args[1])
        sensor = HCSR04(trigger_pin=trig, echo_pin=echo)
        reply("OK %.2f" % sensor.distance_cm())
    except Exception as e:
        reply("ERR ping: %s" % e)


def cmd_neo(args):
    """NeoPixel WS2812: NEO <pin> <n_leds> <rrggbb rrggbb ...>"""
    if len(args) < 3:
        reply("ERR usage: NEO <pin> <n> <rrggbb...>")
        return
    try:
        from neopixel import NeoPixel
    except ImportError:
        reply("ERR neopixel not available")
        return
    try:
        pin_num = int(args[0])
        n = int(args[1])
        colors = []
        for c in args[2:]:
            v = int(c, 16)
            colors.append(((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF))
        np = NeoPixel(Pin(pin_num), n)
        for i, rgb in enumerate(colors):
            if i < n:
                np[i] = rgb
        np.write()
        reply("OK")
    except Exception as e:
        reply("ERR neo: %s" % e)


def cmd_wd(args):
    """Watchdog: WD <ms> để bật, WD 0 để tắt."""
    global wdt
    if not args:
        reply("ERR usage: WD <ms>")
        return
    try:
        ms = int(args[0])
    except ValueError:
        reply("ERR bad ms")
        return
    if ms == 0:
        wdt = None
        reply("OK wd off")
    else:
        wdt = WDT(timeout=ms)
        reply("OK")


def cmd_repl(_):
    reply("OK entering REPL")
    sys.exit(0)


DISPATCH = {
    "PING": cmd_ping,
    "VERSION": cmd_version,
    "RESET": cmd_reset,
    "GPIO": cmd_gpio,
    "PWM": cmd_pwm,
    "ADC": cmd_adc,
    "I2C": cmd_i2c,
    "SERVO": cmd_servo,
    "DHT": cmd_dht,
    "PING_US": cmd_ping_us,
    "NEO": cmd_neo,
    "WD": cmd_wd,
    "REPL": cmd_repl,
}


def dispatch(line):
    parts = line.strip().split()
    if not parts:
        reply("ERR empty")
        return
    cmd = parts[0].upper()
    if cmd in DISPATCH:
        try:
            DISPATCH[cmd](parts[1:])
        except Exception as e:
            reply("ERR %s: %s" % (cmd, e))
    else:
        reply("ERR unknown: %s" % cmd)


def main():
    # KHÔNG gửi PUSH BOOT ngay — giữ TX im lặng cho đến khi host gửi
    # lệnh đầu tiên (tránh FEL race trên H313 BootROM khi x96q đang boot).
    buf = b""
    while True:
        try:
            chunk = uart.read(256)
            if chunk:
                # Host đã gửi byte đầu tiên → x96q đã boot xong → an toàn bật TX
                arm_tx()
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    try:
                        dispatch(line.decode("utf-8", "replace"))
                    except Exception as e:
                        reply("ERR decode: %s" % e)
            if wdt is not None:
                wdt.feed()
            time.sleep_ms(5)
        except KeyboardInterrupt:
            break
    reply("OK bye")


if __name__ == "__main__":
    main()
