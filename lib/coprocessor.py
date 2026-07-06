"""
armkali Co-processor client library

Python client cho x96q (hoặc bất kỳ Linux host nào) để giao tiếp với
RP2040 co-processor qua UART. Protocol: ASCII line-based, 115200 8N1.

Usage:
    from coprocessor import CoProcessor
    co = CoProcessor('/dev/ttyS1')
    print(co.version())       # 'armkali-coproc 1.0.0'
    co.pin_mode(5, 'OUT')
    co.digital_write(5, 1)
    print(co.digital_read(5)) # 1
    co.close()

Push notifications:
    def on_gpio_change(pin, value):
        print(f'GPIO {pin} = {value}')
    co.on('GPIO', on_gpio_change)
    co.watch(5, 'BOTH')
    co.listen()  # start listener thread
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import serial


class CoProcessorError(Exception):
    pass


class CoProcessor:
    """Client cho RP2040 co-processor trên x96q."""

    def __init__(
        self,
        port: str = "/dev/ttyS1",
        baudrate: int = 115200,
        timeout: float = 2.0,
    ):
        self.port = port
        self._ser = serial.Serial(port, baudrate, timeout=timeout)
        self._lock = threading.Lock()
        self._handlers: Dict[str, List[Callable]] = {}
        self._listen_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._flush_input()

    def _flush_input(self) -> None:
        time.sleep(0.1)
        self._ser.reset_input_buffer()

    def close(self) -> None:
        self._stop_event.set()
        if self._listen_thread and self._listen_thread.is_alive():
            self._listen_thread.join(timeout=2)
        if self._ser.is_open:
            self._ser.close()

    def __enter__(self) -> "CoProcessor":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def send(self, cmd: str, timeout: float = 2.0) -> str:
        """Gửi lệnh ASCII, trả về phần data sau 'OK '. Raise CoProcessorError nếu ERR."""
        with self._lock:
            self._ser.write((cmd + "\n").encode())
            self._ser.flush()
            deadline = time.time() + timeout
            while time.time() < deadline:
                raw = self._ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                if line.startswith("PUSH "):
                    self._dispatch_push(line[5:])
                    continue
                return self._parse_response(line)
        raise CoProcessorError(f"Timeout: {cmd}")

    @staticmethod
    def _parse_response(line: str) -> str:
        if line.startswith("OK"):
            return line[3:] if len(line) > 3 else ""
        if line.startswith("ERR"):
            raise CoProcessorError(line[4:] if len(line) > 4 else "unknown error")
        raise CoProcessorError(f"Unexpected response: {line}")

    def _dispatch_push(self, payload: str) -> None:
        parts = payload.split(maxsplit=2)
        if not parts:
            return
        event = parts[0]
        data = parts[1:] if len(parts) > 1 else []
        for cb in self._handlers.get(event, []) + self._handlers.get("*", []):
            try:
                cb(*data)
            except Exception as e:
                print(f"[coproc] push handler error: {e}")

    def on(self, event: str, callback: Callable) -> None:
        """Đăng ký handler cho push notification. Dùng '*' để bắt tất cả."""
        self._handlers.setdefault(event, []).append(callback)

    def listen(self) -> None:
        """Khởi động background thread đọc push notifications."""
        if self._listen_thread and self._listen_thread.is_alive():
            return
        self._stop_event.clear()

        def _loop():
            while not self._stop_event.is_set():
                if not self._ser.is_open:
                    break
                try:
                    raw = self._ser.readline()
                    if raw:
                        line = raw.decode("utf-8", "replace").strip()
                        if line.startswith("PUSH "):
                            self._dispatch_push(line[5:])
                except serial.SerialException:
                    break
                except Exception:
                    time.sleep(0.1)

        self._listen_thread = threading.Thread(target=_loop, daemon=True)
        self._listen_thread.start()

    # === System ===

    def ping(self) -> bool:
        return self.send("PING") == "PONG"

    def version(self) -> str:
        return self.send("VERSION")

    def reset(self) -> None:
        try:
            self.send("RESET")
        except CoProcessorError:
            pass
        time.sleep(1)

    # === GPIO ===

    def pin_mode(self, pin: int, mode: str) -> None:
        """mode: 'IN', 'OUT', 'IN UP', 'IN DOWN'."""
        self.send(f"GPIO {pin} {mode.upper()}")

    def digital_write(self, pin: int, value: int) -> None:
        state = "HIGH" if value else "LOW"
        self.send(f"GPIO {pin} {state}")

    def digital_read(self, pin: int) -> int:
        return int(self.send(f"GPIO {pin} READ"))

    def pull(self, pin: int, mode: str) -> None:
        """mode: 'UP', 'DOWN', 'NONE'."""
        self.send(f"GPIO {pin} PULL {mode.upper()}")

    def watch(self, pin: int, edge: str = "BOTH") -> None:
        """edge: 'RISE', 'FALL', 'BOTH'. Gửi PUSH GPIO khi có edge."""
        self.send(f"GPIO {pin} WATCH {edge.upper()}")

    # === PWM ===

    def pwm(self, pin: int, freq: int, duty: int) -> None:
        """freq: Hz, duty: 0-65535."""
        self.send(f"PWM {pin} {freq} {duty}")

    def pwm_off(self, pin: int) -> None:
        self.send(f"PWM {pin} OFF")

    # === ADC ===

    def analog_read(self, pin: int, mv: bool = False) -> int:
        """pin: 26-29. mv=True trả về millivolt, False trả về raw 16-bit."""
        return int(self.send(f"ADC {pin}{' MV' if mv else ''}"))

    # === I2C ===

    def i2c_scan(self) -> List[int]:
        data = self.send("I2C SCAN")
        return [int(x, 16) for x in data.split()] if data else []

    def i2c_read(self, addr: int, reg: int, nbytes: int) -> bytes:
        data = self.send(f"I2C R {addr:#x} {reg:#x} {nbytes}")
        return bytes(int(b, 16) for b in data.split()) if data else b""

    def i2c_write(self, addr: int, data: bytes) -> None:
        hex_str = ":".join(f"{b:02x}" for b in data)
        self.send(f"I2C W {addr:#x} {hex_str}")

    # === Servo ===

    def servo(self, pin: int, angle: int) -> None:
        """angle: 0-180 độ."""
        self.send(f"SERVO {pin} {angle}")

    def servo_off(self, pin: int) -> None:
        self.send(f"SERVO {pin} OFF")

    # === DHT ===

    def dht(self, pin: int) -> Tuple[float, float]:
        """Trả về (temperature_C, humidity_percent)."""
        parts = self.send(f"DHT {pin}").split()
        return float(parts[0]), float(parts[1])

    # === Ultrasonic HC-SR04 ===

    def distance(self, trig: int, echo: int) -> float:
        """Trả về khoảng cách cm."""
        return float(self.send(f"PING_US {trig} {echo}"))

    # === NeoPixel WS2812 ===

    def neopixel(self, pin: int, colors: List[int]) -> None:
        """colors: list of 0xRRGGBB integers."""
        hex_str = " ".join(f"{c:06x}" for c in colors)
        self.send(f"NEO {pin} {len(colors)} {hex_str}")

    # === Watchdog ===

    def watchdog(self, ms: int) -> None:
        """Bật watchdog (ms). Gửi 0 để tắt. Host phải gọi ping() đều đặn."""
        self.send(f"WD {ms}")

    # === REPL ===

    def repl(self) -> None:
        """Vào MicroPython REPL (interactive). Ctrl+D để thoát."""
        self.send("REPL")
        print(f"[coproc] REPL active on {self.port} — Ctrl+D to exit")
        import select
        import sys

        try:
            while True:
                if select.select([sys.stdin, self._ser], [], [], 0.1)[0]:
                    if sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
                        ch = sys.stdin.read(1)
                        if ch:
                            self._ser.write(ch.encode())
                    if self._ser.in_waiting:
                        sys.stdout.write(self._ser.read(self._ser.in_waiting).decode("utf-8", "replace"))
                        sys.stdout.flush()
        except KeyboardInterrupt:
            pass
        finally:
            self._ser.write(b"\x04")  # Ctrl+D
