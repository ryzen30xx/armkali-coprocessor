# Co-processor Subsystem — x96q + RP2040

Kiến trúc **LattePanda-style**: CPU Linux (Allwinner H313) điều khiển MCU co-processor (RP2040) qua UART để mở rộng GPIO, ADC, PWM, I2C/SPI — những thứ mà Linux SBC không expose trực tiếp.

## Kiến trúc tổng quan

```
┌─────────────────────────────┐              ┌────────────────────────────┐
│  x96q — H313 (Linux ARM64)  │              │  RP2040 — Co-processor     │
│                             │              │                            │
│  ┌───────────────────────┐  │   UART       │  ┌────────────────────┐    │
│  │ armkali Python app    │◄─┼──────────────┼─►│ main.py (uPy)      │    │
│  │                       │  │ 115200 8N1   │  │                    │    │
│  │ import coprocessor    │  │ /dev/ttyS1   │  │ • GPIO (22 pin)    │    │
│  │ co = CoProcessor()    │  │              │  │ • ADC (4 kênh)     │    │
│  │ co.gpio(5, HIGH)      │  │              │  │ • PWM (16 kênh)    │    │
│  │ co.servo(0, 90)       │  │              │  │ • I²C (sensor)     │    │
│  │ co.neopixel(...)      │  │              │  │ • Servo (8 kênh)   │    │
│  └───────────────────────┘  │              │  │ • NeoPixel (PIO)   │    │
│                             │              │  │ • Watchdog         │    │
│  Kali tools + Flask + DB    │              │  └────────────────────┘    │
│                             │              │                            │
└─────────────────────────────┘              └────────────────────────────┘
         ▲                                             │
         │                                             ▼
   WiFi / LAN                              ┌──────────────────────┐
   (SSH, MQTT, HTTP)                       │  Phần cứng ngoại vi  │
                                           │  • Relay 8 kênh      │
                                           │  • Sensor DHT/BME    │
                                           │  • LED, buzzer       │
                                           │  • Servo, motor      │
                                           │  • Distance HC-SR04  │
                                           └──────────────────────┘
```

## So sánh với LattePanda

| Tiêu chí | LattePanda | armkali Co-processor |
|---|---|---|
| CPU | Intel x5-Z8350 (x86) | Allwinner H313 (ARM64) |
| OS | Windows 10 / Linux | Armbian (Debian-based) |
| Co-processor | ATmega32u4 (Arduino Leonardo) | **RP2040** (Raspberry Pi) |
| Giao tiếp | USB Serial (`/dev/ttyACM0`) | **UART hardware** (`/dev/ttyS1`) |
| Protocol | Firmata (binary) | **ASCII line-based** (human-readable) |
| GPIO | 20 (ATmega32u4) | **30** (RP2040) |
| ADC | 6× 10-bit | **4× 12-bit** |
| PWM | 7 | **16** |
| Đặc biệt | Arduino IDE, Shield compatible | **PIO state machine**, MicroPython |

**Tại sao chọn ASCII protocol thay vì Firmata?**
- **Debug được bằng `picocom`** — gửi `PING\n`, nhận `OK PONG\n`. Không cần tool decode binary.
- **Dễ viết firmware** — 1 hàm dispatch dựa trên `cmd.split()[0]`.
- **Dễ port** — bất kỳ ngôn ngữ nào cũng parse được ASCII line.

## Kết nối phần cứng

### Wiring (3 dây)

```
x96q (pad UART giữa USB1 & CVBS)      RP2040 (Pico / RP2040-Zero)
─────────────────────────────────      ──────────────────────────
     TX pad  ───────────────────────►  GP1 (UART0 RX)  — pin 2
     RX pad  ◄────────────────────────  GP0 (UART0 TX)  — pin 1
     GND pad ────────────────────────  GND             — pin 3/8
```

**Mức logic:** cả H313 và RP2040 đều **3.3V TTL** → nối trực tiếp, không cần level shifter.

### Lưu ý quan trọng

1. **Dùng UART khác UART0** của H313. UART0 (`/dev/ttyS0`) đã dành cho serial console (agetty). H313 có UART1 (`/dev/ttyS1`) hoặc UART2 (`/dev/ttyS2`) — dò bằng `ls /dev/ttyS*` sau khi boot.
2. **Tránh FEL race**: RP2040 firmware giữ TX ở LOW cho đến khi nhận lệnh đầu tiên từ x96q (xem firmware `main.py`).
3. **Chung GND** — bắt buộc, nếu không mức logic trôi → garbage.
4. **Nguồn**: cấp nguồn RP2040 từ cổng **USB 5V** của x96q qua USB-C cable (Pico tự hạ xuống 3.3V qua LDO onboard). Đừng lấy 3.3V từ pad UART (H313 regulator yếu).

## UART Protocol

### Định dạng

```
Request (x96q → RP2040):   <CMD> [arg1] [arg2] ... <LF>
Response (RP2040 → x96q):  <OK|ERR> [data] <LF>
Push (RP2040 → x96q):      <PUSH> <event> [data] <LF>
```

Mỗi lệnh là **1 dòng ASCII**, kết thúc bằng `\n` (LF). Baud **115200 8N1**.

### Command Reference

| Command | Arguments | Response | Mô tả |
|---|---|---|---|
| `PING` | — | `OK PONG` | Kiểm tra kết nối |
| `VERSION` | — | `OK armkali-coproc 1.0.0` | Firmware version |
| `RESET` | — | (reboot) | Khởi động lại MCU |
| `GPIO <pin> <mode>` | pin=0-29, mode=`IN`/`OUT`/`HIGH`/`LOW` | `OK` | Cấu hình hoặc set GPIO |
| `GPIO <pin> READ` | pin | `OK 0` hoặc `OK 1` | Đọc mức logic |
| `GPIO <pin> PULL <mode>` | mode=`UP`/`DOWN`/`NONE` | `OK` | Cấu hình pull resistor |
| `GPIO <pin> WATCH <edge>` | edge=`RISE`/`FALL`/`BOTH` | `OK` | Theo dõi edge → gửi PUSH |
| `PWM <pin> <freq> <duty>` | freq (Hz), duty 0-65535 | `OK` | Cấu hình PWM |
| `PWM <pin> OFF` | — | `OK` | Tắt PWM |
| `ADC <pin>` | pin=26/27/28/29 | `OK <0-65535>` | Đọc ADC 16-bit |
| `ADC <pin> MV` | — | `OK <millivolts>` | Đọc theo millivolt |
| `I2C R <addr> <reg> <n>` | addr, reg, n bytes | `OK AA BB CC` (hex) | Đọc I²C register |
| `I2C W <addr>:<bb>:<cc>` | hex bytes, `:` separator | `OK` | Ghi I²C |
| `I2C SCAN` | — | `OK 0x3C 0x76 ...` | Quét bus |
| `SERVO <pin> <angle>` | angle 0-180 | `OK` | Điều khiển servo |
| `SERVO <pin> OFF` | — | `OK` | Nhả servo |
| `DHT <pin>` | pin | `OK <temp> <hum>` | Đọc DHT11/22 |
| `PING_US <trig> <echo>` | 2 pin | `OK <cm>` | Cảm biến siêu âm HC-SR04 |
| `NEO <pin> <n> <rrggbb...>` | pin, số LED, hex colors | `OK` | NeoPixel WS2812 (PIO) |
| `WD <ms>` | ms > 0 để bật, 0 để tắt | `OK` | Watchdog timer |
| `REPL` | — | `OK entering REPL` | Vào MicroPython REPL |

### Push notifications

RP2040 có thể gửi chủ động (không đợi lệnh):

| Event | Format | Khi nào |
|---|---|---|
| `PUSH GPIO <pin> <val>` | pin + mức mới | GPIO watched thay đổi |
| `PUSH WATCHDOG` | — | Watchdog timeout (x96q không PING) |
| `PUSH BOOT` | — | RP2040 vừa reset |
| `PUSH ADC <pin> <val>` | pin + giá trị | ADC vượt ngưỡng (tùy config) |

Client lib tự dispatch push đến handler đã đăng ký (`on(event, callback)`).

## Setup

### 1. Flash MicroPython lên RP2040

```bash
# Tải firmware từ https://micropython.org/download/RPI_PICO/
# Giữ BOOTSEL, cắm USB, thả BOOTSEL, copy .uf2 vào ổ RPI-RP2
cp RPI_PICO-*.uf2 /Volumes/RPI-RP2/
```

### 2. Nối RP2040 vào x96q

```bash
# Cấp nguồn RP2040 từ USB của x96q
# Nối 3 dây: TX-RX-GND (xem sơ đồ trên)
```

### 3. Upload firmware

```bash
# Từ x96q (cần cài mpremote):
sudo apt-get install -y python3-pip
pip3 install mpremote

mpremote connect /dev/ttyS1 cp coprocessor/firmware/main.py :main.py
mpremote connect /dev/ttyS1 reset
```

### 4. Cài client lib

```bash
# Copy lib vào Python path
sudo cp coprocessor/lib/coprocessor.py /usr/local/lib/python3/dist-packages/
# Hoặc dùng trực tiếp:
python3 coprocessor/examples/basic_io.py
```

## Sử dụng nhanh

```python
from coprocessor import CoProcessor

co = CoProcessor('/dev/ttyS1')
print(co.version())              # 'armkali-coproc 1.0.0'

# GPIO
co.pin_mode(5, 'OUT')
co.digital_write(5, 1)
print(co.digital_read(5))        # 1

# Servo
co.servo(0, 90)                  # 90 độ

# Sensor
print(co.dht(22))                # (24.5, 55.2) — (temp, humidity)

# I²C (BME280)
print(co.i2c_read(0x76, 0xD0, 1))  # b'\x60' — chip ID

# Watchdog: nếu x96q treo, RP2040 tự reset sau 8s
co.watchdog(8000)
while True:
    co.ping()                    # phải gọi đều đặn
    time.sleep(5)
```

## Tích hợp với armkali installer

Script `install.sh` có thể thêm option **"Install co-processor support"**:
- Cài `python3-serial` (pySerial)
- Cài `mpremote` để flash firmware
- Copy `coprocessor/lib/coprocessor.py` vào `/usr/local/lib/python3/dist-packages/`
- Enable UART1 trong device tree (nếu cần)

## Files

```
coprocessor/
├── ARCHITECTURE.md       # File này
├── firmware/
│   └── main.py           # MicroPython firmware cho RP2040
├── lib/
│   └── coprocessor.py    # Python client cho x96q
└── examples/
    └── basic_io.py       # Ví dụ GPIO/ADC/PWM/I²C/Servo
```

## Troubleshooting

| Triệu chứng | Nguyên nhân | Fix |
|---|---|---|
| x96q không boot khi nối RP2040 | FEL race | RP2040 firmware đã có fix — kiểm tra `main.py` có giữ TX idle |
| `SerialTimeoutException` | Sai port / baud | Kiểm tra `ls /dev/ttyS*`, đảm bảo baud=115200 |
| `ERR unknown command` | Lệnh sai cú pháp | Xem Command Reference ở trên |
| Response lẫn push | Client lib tự lọc | Dùng `CoProcessor.send()` (đã có filter) |
| RP2040 treo | Watchdog không bật | Bật `co.watchdog(8000)` + loop `co.ping()` |

## Giấy phép

MIT — cùng license với armkali.
