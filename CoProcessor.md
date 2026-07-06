# CoProcessor — Ý tưởng, Thiết kế và Triển khai

> **Tóm tắt 1 dòng:** Dùng MCU ngoài (RP2040) làm "GPIO expander" cho x96q, giao tiếp qua UART bằng protocol ASCII line-based — theo mô hình **LattePanda** (CPU Linux + MCU đồng hành).

Tài liệu này tổng hợp toàn bộ quá trình từ lúc đặt vấn đề đến khi hoàn thành thiết kế, bao gồm cả những bài học phần cứng (FEL race condition) và quyết định kiến trúc.

---

## 1. Nguồn gốc ý tưởng

### 1.1 Vấn đề ban đầu: x96q thiếu GPIO

x96q là TV box chạy Allwinner H313, được cài Armbian để làm pentest drop node qua dự án `armkali`. Nó có:
- CPU ARM64 4 nhân, 1GB RAM, 8GB eMMC
- Kali Linux tools chạy tốt
- Serial console qua UART0 (pad RX/TX/GND)

Nhưng **gần như không có GPIO exposed** — H313 giấu các chân I/O trong BGA package. Muốn điều khiển relay, đọc sensor analog, xuất PWM, giao tiếp I²C với sensor → không làm được trực tiếp từ Linux.

### 1.2 Ý tưởng đầu tiên: "Dùng ESP32-C3 nối UART với x96q"

> *"Trước đó tôi đã từng nối ESP32-C3 vào mạch x96q thông qua UART để đọc UART của x96q từ ESP32-C3 nhưng mà không thể boot được, lý do tại sao?"*

Thử nghiệm: nối ESP32-C3 (TX/RX/GND) với x96q → **x96q không boot được**. Ngắt ESP32 ra → boot bình thường.

### 1.3 Chẩn đoán: FEL race condition

**Nguyên nhân:** BootROM của Allwinner H313 **nghe UART0 trong ~500ms đầu sau reset**. Nếu phát hiện ký tự (bất kỳ) trên RX → nó vào **FEL mode** (USB recovery) thay vì boot từ eMMC.

Khi ESP32-C3 vừa cấp điện:
- Chân TX của ESP32 phát garbage / mức HIGH trong lúc boot
- H313 BootROM nhận nhầm là FEL request
- → Treo ở FEL mode, không boot Linux

**Giải pháp khắc phục:**

| Cách | Độ tin cậy | Chi phí |
|---|---|---|
| Pull-down 10kΩ giữa RX x96q và GND | ~70% | 500đ |
| Delay 30s trước khi ESP32 bật UART | ~80% | 0đ (software) |
| MOSFET gate điều khiển TX line | **100%** | ~2k VNĐ |
| **MCU firmware giữ TX LOW cho đến khi nhận byte đầu tiên từ host** | **100%** | 0đ (firmware) |

→ **Cách 4** là giải pháp phần mềm sạch nhất, sẽ được dùng trong firmware.

### 1.4 Từ ESP32-C3 sang "MCU làm GPIO expander"

Câu hỏi tiếp theo:

> *"Liệu tôi có thể dùng Arduino hoặc ESP32 để làm GPIO cho x96q và 2 mạch giao tiếp cho nhau qua UART không?"*

→ **Có**, đây là pattern kinh điển trong embedded Linux: **Linux SBC thiếu GPIO → mượn MCU qua UART**. Nhiều router công nghiệp, Raspberry Pi HAT, và đặc biệt là **LattePanda** đều dùng kiến trúc này.

---

## 2. Lựa chọn MCU

### 2.1 Tiêu chí

1. **3.3V TTL** — tương thích trực tiếp với H313, không cần level shifter
2. **Đủ GPIO** (≥20) cho relay/sensor/LED/servo
3. **UART** cho giao tiếp x96q
4. **Rẻ** (≤80k VNĐ) — x96q chỉ ~300k, MCU không đắt hơn
5. **Nhỏ** — nhét vừa trong hộp TV box
6. **Sẵn tại VN** — mua được qua Shopee/Tiki
7. **Bonus**: ADC, PWM, I²C, MicroPython để dễ firmware

### 2.2 Yêu cầu chốt

> *"Không cần WiFi và Bluetooth, chỉ cần nó là GPIO thôi, giao tiếp cả in/out"*

→ Loại ESP32-C3 ra khỏi top (vì trả tiền cho WiFi/BLE không dùng). Ưu tiên MCU thuần GPIO.

### 2.3 Bảng so sánh

| MCU | Giá (VNĐ) | Logic | GPIO | Đặc biệt | Chấm |
|---|---:|:---:|:---:|---|:---:|
| **RP2040** | 50-80k | 3.3V ✓ | **30** | PIO, MicroPython, 4×ADC 12-bit, 16×PWM | ⭐⭐⭐⭐⭐ |
| CH582M (WCH) | 40-50k | 3.3V ✓ | 22 | QFN28 5×5mm, BLE 5.3 (không dùng) | ⭐⭐⭐ |
| GD32VF103 | 35-50k | 3.3V ✓ | 37 | RISC-V, STM32-compatible | ⭐⭐⭐ |
| STM32F103 Blue Pill | 35-50k | 3.3V ✓ | 37 | Cổ điển, tài liệu nhiều | ⭐⭐⭐ |
| CH32V003 | 2-5k | 3.3V ✓ | 18 | Rẻ nhất, chỉ 1 UART | ⭐⭐ |
| ATmega328P (Arduino) | 25-40k | **5V** ⚠️ | 23 | ❌ 5V — cháy H313 nếu không shifter | ❌ |
| ATtiny85 | 8-12k | 3.3V/5V | **6** | ❌ Quá ít GPIO | ❌ |
| PIC16F1847 | 20-30k | **5V** ⚠️ | 16 | ❌ 5V, toolchain cũ | ❌ |

### 2.4 Quyết định: **RP2040**

**5 lý do:**

1. **28 GPIO usable** (trong tổng 30 — 2 chân GP0/GP1 dành cho UART) — đủ cho 8 relay + 8 switch + 4 analog + 4 LED + 2 buzzer + 2 dự phòng
2. **PIO (Programmable I/O)** — killer feature: giả lập WS2812 LED, logic analyzer, custom protocol mà MCU khác không làm được
3. **MicroPython** — firmware bằng Python, update từ x96q qua `mpremote`, không cần compile
4. **3.3V** — an toàn tuyệt đối với H313
5. **Không có WiFi/BLE = không có RF emission** — quan trọng khi pentest ở môi trường nhạy cảm (không muốn MCU phát sóng radio ngoài ý muốn)

---

## 3. Kiến trúc LattePanda-style

### 3.1 LattePanda là gì?

LattePanda là SBC x86 có sẵn Arduino onboard:

| | LattePanda |
|---|---|
| CPU | Intel x5-Z8350 (Windows/Linux) |
| MCU | ATmega32u4 (Arduino Leonardo) |
| Giao tiếp | USB Serial (`/dev/ttyACM0`) |
| Protocol | **Firmata** (binary) |
| GPIO | 20 (Arduino) |

→ CPU và MCU chạy 2 hệ điều hành khác nhau, nói chuyện qua serial. CPU gửi lệnh, MCU điều khiển phần cứng.

### 3.2 armkali áp dụng pattern này

```
┌─ LattePanda ──────────────────────┐     ┌─ armkali Co-processor ──────────────┐
│                                   │     │                                     │
│  Windows/Linux + ATmega32u4       │     │  Armbian (x96q) + RP2040            │
│  USB Serial                       │     │  UART hardware                      │
│  Firmata (binary)                 │     │  ASCII line-based (human-readable)  │
│  Arduino IDE                      │     │  MicroPython                        │
│  20 GPIO                          │     │  30 GPIO + PIO                      │
│                                   │     │                                     │
└───────────────────────────────────┘     └─────────────────────────────────────┘
```

**Khác biệt lớn nhất:** protocol **ASCII thay vì Firmata binary**. Lý do:
- Debug được bằng `picocom` — gõ lệnh tay, đọc response bằng mắt
- Dễ viết firmware — 1 dict dispatch
- Dễ port — bất kỳ ngôn ngữ nào cũng parse text được

### 3.3 Sơ đồ khối

```
┌─────────────────────────────┐              ┌────────────────────────────┐
│  x96q — H313 (Linux ARM64)  │              │  RP2040 — Co-processor     │
│                             │              │                            │
│  ┌───────────────────────┐  │   UART       │  ┌────────────────────┐    │
│  │ Python app của bạn    │◄─┼──────────────┼─►│ main.py (uPy)      │    │
│  │                       │  │ 115200 8N1   │  │                    │    │
│  │ from coprocessor      │  │ /dev/ttyS1   │  │ • GPIO (22 pin)    │    │
│  │   import CoProcessor  │  │              │  │ • ADC (4 kênh)     │    │
│  │ co = CoProcessor()    │  │              │  │ • PWM (16 kênh)    │    │
│  │ co.gpio(5, HIGH)      │  │              │  │ • I²C (sensor)     │    │
│  │ co.servo(0, 90)       │  │              │  │ • Servo            │    │
│  └───────────────────────┘  │              │  │ • NeoPixel (PIO)   │    │
│                             │              │  │ • Watchdog         │    │
│  Kali tools + Flask + DB    │              │  └────────────────────┘    │
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

---

## 4. Kết nối phần cứng

### 4.1 Wiring (3 dây)

Pad vật lý trên x96q (nằm giữa USB1 và CVBS) là **UART0 của H313** — thiết bị `/dev/ttyS0` trong Linux. Đây là UART duy nhất được expose ra header trên board x96q (UART1/2 tồn tại trong SoC nhưng không có pad).

```
x96q (pad UART0 giữa USB1 & CVBS)    RP2040 (Pico / RP2040-Zero)
─────────────────────────────────    ──────────────────────────
  TX pad ───(x96q phát)──────────►  GP1 (UART0 RX) — Pico pin 2
  RX pad ◄──(x96q nhận)────────────  GP0 (UART0 TX) — Pico pin 1
  GND pad ─────────────────────────  GND            — Pico pin 3/8
```

> **Nhớ quy tắc chéo:** TX của thiết bị này → RX của thiết bị kia. Nếu nối TX↔TX thì cả 2 bên cùng drive → garbage.

### 4.2 Lưu ý quan trọng

1. **UART0 dùng chung với serial console** — mặc định Armbian chạy `serial-getty@ttyS0` (agetty) trên cùng UART này. Nếu giữ agetty, nó sẽ "nói chuyện" với RP2040 cùng lúc với Python app → loạn protocol. **Fix:** disable agetty khi dùng coprocessor mode:
   ```bash
   sudo systemctl stop    serial-getty@ttyS0
   sudo systemctl disable serial-getty@ttyS0
   sudo systemctl mask    serial-getty@ttyS0   # chặn khởi động lại
   ```
   Sau đó Python mở `/dev/ttyS0` toàn quyền. Nếu vẫn muốn giữ SSH console, dùng SSH qua network thay vì UART.

2. **Chung GND** — bắt buộc, nếu không mức logic trôi → garbage.

3. **Nguồn**: cấp nguồn RP2040 từ **USB 5V của x96q** qua USB-C cable (Pico tự hạ xuống 3.3V qua LDO onboard). Đừng lấy 3.3V từ pad UART (H313 regulator yếu, chỉ ~50mA).

4. **Mức logic**: cả H313 và RP2040 đều **3.3V TTL** → nối trực tiếp, không cần level shifter.

5. **FEL race**: firmware RP2040 giữ TX (GP0) ở mức LOW cho đến khi nhận byte đầu tiên từ x96q — tránh trigger H313 BootROM khi x96q đang boot. (Xem chi tiết §7)

### 4.3 Sơ đồ đầy đủ

```
                x96q (H313)
    ┌──────────────────────────────┐
    │   [USB1]        [CVBS]       │
    │   pads UART0:                │
    │   GND  TX  RX                │
    └────┬────┬───┬────────────────┘
         │    │   │
         │    │   └─────────────────────┐
         │    └───────────┐             │
         │                │             │
         │                ▼             ▼
         │     ┌─────────────────────────────┐
         │     │      RP2040 (Pico)          │
         │     │                             │
         │     │  GP0 (TX) ◄── pad RX x96q   │
         │     │  GP1 (RX) ──► pad TX x96q   │
         │     │  GND      ─── pad GND       │
         │     │                             │
         │     │  GP4  (SDA) ──┐             │
         │     │  GP5  (SCL) ──┤── BME280    │
         │     │  GP6  ──── Button (pull-up) │
         │     │  GP7  ──── LED              │
         │     │  GP8  ──── Servo            │
         │     │  GP9  ──── Relay            │
         │     │  GP26 ─── Potentiometer     │
         │     │  GP27 ─── DHT22             │
         │     │  GP15 ─── NeoPixel WS2812   │
         │     │                             │
         │     │  [USB-C] ── 5V từ USB x96q  │
         │     └─────────────────────────────┘
         │
    ┌────┴────┐
    │ USB 5V  │  (cấp nguồn cho Pico qua USB-C)
    └─────────┘
```

---

## 5. Protocol — ASCII line-based

### 5.1 Định dạng

```
Request (x96q → RP2040):   <CMD> [arg1] [arg2] ... <LF>
Response (RP2040 → x96q):  <OK|ERR> [data] <LF>
Push (RP2040 → x96q):      <PUSH> <event> [data] <LF>
```

Mỗi lệnh = **1 dòng ASCII**, kết thúc `\n`. Baud 115200 8N1.

### 5.2 Ví dụ thực tế

| Mục đích | Gửi | Nhận |
|---|---|---|
| Ping | `PING\n` | `OK PONG\n` |
| Bật LED chân 5 | `GPIO 5 HIGH\n` | `OK\n` |
| Đọc mức chân 6 | `GPIO 6 READ\n` | `OK 1\n` |
| Đọc ADC chân 26 | `ADC 26\n` | `OK 2341\n` |
| Đọc ADC theo mV | `ADC 26 MV\n` | `OK 1176\n` |
| Quét I²C | `I2C SCAN\n` | `OK 0x3C 0x76\n` |
| Đọc BME280 | `I2C R 0x76 0xD0 1\n` | `OK 60\n` |
| Servo 90° | `SERVO 0 90\n` | `OK\n` |
| Đọc DHT22 | `DHT 22\n` | `OK 24.5 55.2\n` |
| Khoảng cách | `PING_US 2 3\n` | `OK 42.50\n` |
| NeoPixel | `NEO 15 3 FF0000 00FF00 0000FF\n` | `OK\n` |
| Bật watchdog 8s | `WD 8000\n` | `OK\n` |
| Pull-up chân 6 | `GPIO 6 PULL UP\n` | `OK\n` |
| Theo dõi edge | `GPIO 6 WATCH BOTH\n` | `OK\n` (sau đó nhận `PUSH GPIO 6 0\n`) |
| Vào MicroPython REPL | `REPL\n` | `OK entering REPL\n` (sau đó raw bytes) |
| Lệnh sai | `FOOBAR\n` | `ERR unknown: FOOBAR\n` |

### 5.3 So sánh với Firmata (binary)

Cùng lệnh "bật GPIO 5":

| Protocol | Dữ liệu gửi | Đọc được bằng mắt? |
|---|---|---|
| ASCII (armkali) | `GPIO 5 HIGH\n` | ✅ |
| Firmata (binary) | `0x91 0x05 0x01` | ❌ |

→ **Human-readable**: debug bằng `picocom`, test thủ công, log ra file, grep lỗi dễ dàng.

---

## 6. Pseudocode — Cách hệ thống hoạt động

### 6.1 Hai chương trình chạy song song

```
┌──────────────────────────┐          ┌──────────────────────────┐
│  CHƯƠNG TRÌNH A: x96q    │   UART   │  CHƯƠNG TRÌNH B: RP2040  │
│  (Python trên Linux)     │◄────────►│  (MicroPython firmware)  │
│                          │  3 dây   │                          │
│  Bạn viết + ấn run       │ TX/RX/GND│  Nạp sẵn trong RP2040    │
└──────────────────────────┘          └──────────────────────────┘
```

Hai chương trình chạy **độc lập, cùng lúc** — giống 2 người nói chuyện qua điện thoại.

### 6.2 Chương trình B: RP2040 (firmware) — luôn chạy

```
KHI RP2040 ĐƯỢC CẤP ĐIỆN:
│
├─ BƯỚC 1: Khởi tạo
│   • tx_pin = chân GP0, chế độ OUTPUT, mức LOW  ← GIỮ IM LẶNG
│   • tx_armed = False                           ← CHƯA cho phép phát
│   • Tạo UART 115200 trên GP0(TX)/GP1(RX)
│   • pins = {}, pwms = {}
│   • DISPATCH = {
│         "PING":   hàm_ping,    "GPIO":   hàm_gpio,
│         "ADC":    hàm_adc,     "PWM":    hàm_pwm,
│         "I2C":    hàm_i2c,     "SERVO":  hàm_servo,
│         "DHT":    hàm_dht,     "WD":     hàm_watchdog,
│     }
│
├─ BƯỚC 2: KHÔNG GỬI GÌ CẢ
│   • KHÔNG gửi "PUSH BOOT" lúc này
│   • Lý do: x96q có thể đang boot; nếu phát tín hiệu
│     lên RX của H313 → BootROM vào FEL mode → x96q không boot được
│
└─ BƯỚC 3: Vòng lặp vô hạn
    │
    LẶP LẠI MÃI:
    │
    ├─ chunk = uart.read(256 bytes, timeout=0)
    │   │
    │   ├─ NẾU chunk RỖNG: không làm gì
    │   │
    │   └─ NẾU chunk CÓ DỮ LIỆU:
    │       ├─ NẾU tx_armed == False:
    │       │   • x96q đã gửi byte đầu tiên → x96q đã boot xong
    │       │   • arm_tx(): chuyển GP0 từ GPIO OUTPUT LOW → UART TX
    │       │   • tx_armed = True
    │       │
    │       ├─ Nối chunk vào buffer
    │       │
    │       └─ TRONG KHI buffer chứa "\n":
    │           • Tách 1 dòng (từ đầu đến "\n")
    │           • dispatch(dòng)
    │
    ├─ NẾU watchdog bật: wdt.feed()
    ├─ NẾU có GPIO WATCH thay đổi mức: reply("PUSH GPIO ...")
    └─ Ngủ 5ms rồi lặp lại
```

**Hàm dispatch:**

```
HÀM dispatch(dòng_text):
│
├─ parts = dòng_text.split(" ")     # "GPIO 5 HIGH" → ["GPIO","5","HIGH"]
├─ cmd = parts[0].upper()
├─ args = parts[1:]
│
├─ NẾU cmd trong DISPATCH:
│   • Gọi DISPATCH[cmd](args)
│
└─ NẾU không:
    • reply("ERR unknown: XYZ\n")
```

**Hàm cmd_gpio — ví dụ 1 handler:**

```
HÀM cmd_gpio(args):
│   args = ["5", "HIGH"]
│
├─ pin_num = int(args[0])     → 5
├─ action  = args[1].upper()  → "HIGH"
│
├─ action == "OUT":     pins[5] = Pin(5, OUTPUT);         reply("OK")
├─ action == "HIGH":    pins[5].value(1) ← GP5 = 3.3V;   reply("OK")
├─ action == "LOW":     pins[5].value(0) ← GP5 = 0V;     reply("OK")
├─ action == "READ":    reply("OK " + str(pins[5].value()))
└─ action == "WATCH":   đăng ký ngắt, gửi PUSH khi edge
```

### 6.3 Chương trình A: Python trên x96q (code của bạn)

```
KHI BẠN GÕ: python3 my_script.py
│
├─ BƯỚC 1: Import thư viện
│   from coprocessor import CoProcessor
│
├─ BƯỚC 2: Kết nối
│   co = CoProcessor('/dev/ttyS1')
│   • Mở /dev/ttyS1 với baud 115200
│   • Chờ 100ms, flush buffer
│
├─ BƯỚC 3: (tùy chọn) Đăng ký push handlers
│   co.on('GPIO', my_handler)
│   co.listen()   ← thread ngầm
│
└─ BƯỚC 4: Code của bạn chạy
    • co.ping()
    • co.digital_write(5, 1)
    • val = co.analog_read(26)
    • ...
    • co.close()
```

### 6.4 Luồng chi tiết: `co.digital_write(5, 1)`

```
BẠN GỌI: co.digital_write(5, 1)
│
├─ Python dịch thành ASCII:  self.send("GPIO 5 HIGH")
│
├─ pySerial ghi bytes:       self._ser.write(b"GPIO 5 HIGH\n")
│
├─ UART hardware H313 truyền bits qua dây TX
│   (3.3V = bit 1, 0V = bit 0, 115200 bps)
│
│   ┌────────── RP2040 NHẬN VÀ XỬ LÝ ──────────┐
│   │ • chunk = b"GPIO 5 HIGH\n"                │
│   │ • arm_tx() (nếu lần đầu)                  │
│   │ • dispatch("GPIO 5 HIGH")                 │
│   │ • cmd_gpio(["5","HIGH"])                  │
│   │ • pins[5].value(1)  ← CHÂN GP5 = 3.3V    │
│   │ • reply("OK\n")                           │
│   └────────────────────────────────────────────┘
│
├─ x96q nhận:              raw = self._ser.readline()  → b"OK\n"
├─ Parse:                  line = "OK" → thành công
│
└─ Trả về cho code của bạn.
```

### 6.5 Timeline thực tế

```
Thời gian    x96q (Python)                    RP2040 (firmware)
─────────    ─────────────                    ─────────────────
   0 ms      Bạn gõ: python3 my_script.py     Vòng lặp đọc UART
   5 ms      Mở /dev/ttyS1                    —
  12 ms      Gửi "VERSION\n" ─────────────►   Nhận "VERSION\n"
  13 ms                                        arm_tx() lần đầu
  15 ms                                        cmd_version()
  16 ms      ◄────────────── Nhận "OK armkali-coproc 1.0.0\n"
  18 ms      Print "Phiên bản: ..."
  20 ms      Gửi "GPIO 5 HIGH\n" ─────────►   pins[5].value(1)
  24 ms      ◄────────────── Nhận "OK\n"
  27 ms      Gửi "ADC 26\n" ──────────────►   ADC(Pin(26)) → 2341
  30 ms      ◄────────────── Nhận "OK 2341\n"
  32 ms      Print "ADC: 2341"
  33 ms      time.sleep(1)
1034 ms      Gửi "GPIO 5 LOW\n" ──────────►   pins[5].value(0)
1040 ms      co.close()
1043 ms      Chương trình kết thúc             Vẫn chạy vòng lặp
```

**Tổng: 4 lệnh ≈ 40 ms** — mỗi lệnh ~10 ms (truyền/nhận/parse).

---

## 7. Vấn đề FEL race — chi tiết kỹ thuật

### 7.1 Tại sao H313 BootROM bị trigger?

```
Chuỗi boot của Allwinner H313 (~100ms đầu):
│
├─ 0 ms: Cấp điện
├─ 1 ms: BootROM chạy từ ROM nội bộ
├─ 5 ms: BootROM kiểm tra:
│         • Có USB host yêu cầu FEL không?
│         • Có ký tự magic trên UART0 RX không?  ← NHẠY CẢM
│         • Nếu KHÔNG → boot từ eMMC/SD
│         • Nếu CÓ → vào FEL mode (chờ PC gửi firmware qua USB)
├─ 10-500 ms: Boot từ eMMC → U-Boot → Linux kernel
```

### 7.2 Kịch bản lỗi (khi dùng ESP32-C3)

```
Thời gian    x96q (H313)                    ESP32-C3
─────────    ─────────────                  ─────────────────
   0 ms      Cấp điện cả 2 board            Cấp điện
   1 ms      BootROM chạy                   Đang boot, chân TX chưa ổn định
   5 ms      BootROM nghe UART0             TX phát garbage (mức HIGH)
  10 ms      BootROM nhận ký tự trên RX
             → NGHĨ LÀ FEL REQUEST
             → VÀO FEL MODE
             → KHÔNG BOOT LINUX!
```

### 7.3 Fix trong firmware RP2040

```
Thời gian    x96q (H313)                    RP2040
─────────    ─────────────                  ─────────────────
   0 ms      Cấp điện                       Cấp điện
   1 ms      BootROM chạy                   Init: GP0 = OUTPUT, value=LOW  ← TX = 0V
   5 ms      BootROM nghe UART0             GP0 vẫn LOW → RX H313 = 0V (idle)
  10 ms      BootROM không thấy gì
             → Boot từ eMMC ✓
  ...        U-Boot → kernel → systemd
  15 s       Linux boot xong
  20 s       User chạy: co.ping()
             Gửi "PING\n" ──────────────►   Nhận "PING\n"
                                            arm_tx() → GP0 chuyển thành UART TX
                                            reply("OK PONG\n") ← TX được phép phát
```

**Đoạn code quan trọng trong `firmware/main.py`:**

```python
# Ban đầu: TX = GPIO OUTPUT LOW (im lặng)
_tx_pin = Pin(TX_PIN, Pin.OUT, value=0)
tx_armed = False

def arm_tx():
    global tx_armed
    if tx_armed: return
    uart.init(baudrate=BAUD, tx=Pin(TX_PIN), rx=Pin(RX_PIN), ...)
    tx_armed = True

# Trong main loop:
chunk = uart.read(256)
if chunk:
    arm_tx()              # chỉ bật TX khi host gửi byte đầu tiên
    buf += chunk
    # ... dispatch ...
```

---

## 8. File triển khai

```
coprocessor/
├── ARCHITECTURE.md       # Kiến trúc tổng quan, so sánh LattePanda
├── CoProcessor.md        # ← File này (ý tưởng, thiết kế, pseudocode)
├── firmware/
│   └── main.py           # MicroPython firmware cho RP2040
├── lib/
│   └── coprocessor.py    # Python client cho x96q
└── examples/
    └── basic_io.py       # Ví dụ GPIO/ADC/PWM/I²C/Servo/Watchdog
```

### 8.1 Firmware (RP2040)

- **Ngôn ngữ:** MicroPython
- **Kích thước:** ~300 dòng
- **Tính năng:** 17 lệnh (PING, VERSION, RESET, GPIO, PWM, ADC, I²C, SERVO, DHT, PING_US, NEO, WD, REPL, WATCH, PULL)
- **Fix FEL race:** TX giữ LOW cho đến khi nhận byte đầu tiên từ host

### 8.2 Client lib (x96q)

- **Ngôn ngữ:** Python 3 + pySerial
- **Kích thước:** ~200 dòng
- **Tính năng:** push notifications, thread-safe, context manager, REPL mode
- **Cài:** `sudo cp lib/coprocessor.py /usr/local/lib/python3/dist-packages/`

### 8.3 Ví dụ cơ bản

```python
from coprocessor import CoProcessor

co = CoProcessor('/dev/ttyS1')
print(co.version())             # armkali-coproc 1.0.0

co.pin_mode(5, 'OUT')
co.digital_write(5, 1)          # LED sáng
print(co.digital_read(5))       # 1

print(co.analog_read(26, mv=True))  # 1176 (mV)

co.servo(0, 90)                 # servo quay 90°

print(co.dht(22))               # (24.5, 55.2) — (temp, humidity)

# Watchdog: RP2040 tự reset nếu x96q không PING trong 8s
co.watchdog(8000)
while True:
    co.ping()
    time.sleep(5)
```

---

## 9. Troubleshooting

| Triệu chứng | Nguyên nhân | Fix |
|---|---|---|
| x96q không boot khi nối RP2040 | FEL race | Kiểm tra firmware có giữ TX LOW ban đầu |
| `SerialTimeoutException` | Sai port / baud | `ls /dev/ttyS*`, đảm bảo baud=115200 |
| `ERR unknown command` | Lệnh sai | Xem Command Reference §5 |
| Response lẫn push | Client lib tự lọc | Dùng `send()` (đã có filter) |
| RP2040 treo | Watchdog không bật | Bật `co.watchdog(8000)` + loop `co.ping()` |
| ADC đọc không ổn định | Noise | Thêm tụ 100nF giữa ADC pin và GND |
| I²C không scan ra device | SDA/SCL đảo | Kiểm tra lại dây GP4(SDA) / GP5(SCL) |
| Servo rung | Nguồn yếu | Cấp nguồn riêng cho servo, không lấy từ Pico |

---

## 10. Mở rộng tương lai

- **Nhiều MCU cùng bus**: RP2040 thứ 2, thứ 3 nối UART song song (cần protocol address)
- **Wireless bridge**: ESP32-C6 làm repeater BLE/Zigbee → gửi về RP2040 qua I²C → x96q
- **OTA firmware**: x96q gửi file `.py` mới qua UART → RP2040 ghi vào flash
- **PIO logic analyzer**: dùng PIO của RP2040 bắt tín hiệu I²C/SPI/custom từ thiết bị mục tiêu, stream về x96q qua UART tốc độ cao
- **Real-time control**: chuyển firmware sang C++ (Pico SDK) nếu cần < 1ms latency

---

## 11. Tham khảo

- **LattePanda**: [lattepanda.com/lattepanda](https://www.lattepanda.com/lattepanda) — SBC x86 + Arduino onboard, cảm hứng kiến trúc
- **Firmata protocol**: [github.com/firmata/protocol](https://github.com/firmata/protocol) — binary protocol cho Arduino (chúng tôi KHÔNG dùng, thay bằng ASCII)
- **Allwinner H313 / sunxi FEL**: [linux-sunxi.org/FEL](https://linux-sunxi.org/FEL) — tài liệu về FEL mode của BootROM Allwinner
- **X96Q sunxi wiki**: [linux-sunxi.org/X96Q](https://linux-sunxi.org/X96Q) — schematic, UART pad location, DTS
- **Raspberry Pi Pico / RP2040**: [datasheets.raspberrypi.com/pico/pico-datasheet.pdf](https://datasheets.raspberrypi.com/pico/pico-datasheet.pdf)
- **MicroPython for RP2040**: [docs.micropython.org/latest/esp32/quickref.html](https://docs.micropython.org/) + [firmware download](https://micropython.org/download/RPI_PICO/)
- **pySerial**: [pyserial.readthedocs.io](https://pyserial.readthedocs.io/) — Python serial library
- **XRadio XR819**: WiFi chip của x96q — 2.4GHz b/g/n, không monitor mode, không Bluetooth
- **armkali project**: [github.com/ryzen30xx/armkali](https://github.com/ryzen30xx/armkali) — dự án pentest ARM64 gốc
- **mpremote tool**: [docs.micropython.org/latest/reference/mpremote.html](https://docs.micropython.org/latest/reference/mpremote.html) — flash firmware MicroPython từ Linux host

---

## 12. Use cases cho pentest (thực tế với armkali)

x96q + RP2040 không chỉ là "thêm GPIO" — nó biến TV box thành **pentest drop node** đa năng:

### 12.1 Network drop node
Cắm x96q vào switch của mạng mục tiêu, RP2040 điều khiển **8 relay** để:
- Chuyển vùng giữa các VLAN (relay nối port mirroring)
- Kill switch: ngắt kết nối thiết bị mục tiêu khi bị phát hiện
- Power cycle router/AP từ xa khi cần tạo "sự cố" để bắt WPA handshake

```python
# x96q chạy script, RP2040 đóng relay theo lệnh
co.digital_write(9, 1)   # Relay 1: mirror port 3 → port 7
co.digital_write(10, 1)  # Relay 2: power-cycle AP mục tiêu
```

### 12.2 RF/BLE hunter
RP2040 + module BLE (HM-11) hoặc RF (CC1101) → scan tín hiệu xung quanh:
- Tìm BLE beacons trong tòa nhà (asset tracking của mục tiêu)
- Bắt tín hiệu RF từ keyfob, garage door, IoT device
- Gửi data về x96q phân tích + decode bằng Kali tools (`rtl_433`, `ubertooth`)

### 12.3 Physical security implant
x96q giấu trong hộp điện, RP2040 nối với:
- **PIR sensor** — phát hiện người vào phòng → trigger `tcpdump`
- **Door magnetic switch** — log thời điểm mở cửa server room
- **Buzzer silent alarm** — RP2040 gửi MQTT message khi phát hiện bất thường

### 12.4 Hardware hacking station
RP2040 đọc chip mục tiêu qua I²C/SPI:
- Dump EEPROM 24C256 của router (chứa WPA key, config)
- Sniff traffic giữa MCU và sensor trong IoT device
- **PIO làm logic analyzer 8 kênh @ 100 MSPS** — debug protocol lạ của thiết bị mục tiêu

### 12.5 Power-aware pentest box
Khi chạy bằng pin (power bank):
- RP2040 đọc ADC từ voltage divider → đo mức pin
- Nếu < 3.3V: gửi `PUSH ADC 26 <val>` → x96q shutdown an toàn (`sudo shutdown -h now`)
- Tránh corrupt filesystem trên eMMC khi hết pin đột ngột

---

## 13. Boot sequence tổng thể

Khi cấp điện cả hệ thống (cắm adapter vào x96q, x96q cấp 5V cho RP2040 qua USB):

```
Thời gian    x96q (H313, Armbian)               RP2040 (MicroPython)
─────────    ────────────────────                ─────────────────────
   0 ms      Cấp điện                            Cấp điện qua USB-C
   1 ms      BootROM chạy                        BootROM chạy, load uPy
   5 ms      BootROM nghe UART0 ~100ms           GP0 = OUTPUT LOW  ← TX im lặng
  10 ms      BootROM KHÔNG thấy ký tự            (không gửi gì)
             → Boot từ eMMC ✓
  50 ms      U-Boot start                        main.py start
 500 ms      U-Boot load kernel                  Vòng lặp UART chạy
   3 s       Kernel start, systemd               Đọc UART, buffer rỗng
  15 s       Armbian ready, agetty ttyS0 chạy    Vẫn đọc UART
  16 s       (Nếu agetty KHÔNG bị disable,       Nhận garbage từ agetty →
              agetty sẽ gửi login prompt →        dispatch → ERR → reply ERR
              RP2040 nhận nhưng ignore)            nhưng TX đã arm → OK
  ...       ...                                  ...
  Khi user   User chạy: sudo systemctl
  can thiệp  mask serial-getty@ttyS0
             User chạy: python3 my_script.py
             co.ping() ───────────────────►      Nhận "PING\n" → OK PONG
  Sẵn sàng                                      Sẵn sàng
```

**Tổng thời gian từ cấp điện đến sẵn sàng:** ~16-20 giây (chủ yếu là Linux boot).  
RP2040 sẵn sàng trong <1 giây nhưng phải đợi x96q boot xong.

**Quan trọng:** agetty trên `/dev/ttyS0` **phải được disable** trước khi Python app chạy — nếu không agetty sẽ gửi login prompt định kỳ, làm RP2040 nhận garbage và reply ERR liên tục.

---

## 14. Limitations

| Hạn chế | Mô tả | Giải pháp / Workaround |
|---|---|---|
| **RP2040 không có crypto hardware** | ESP32 có AES/SHA hardware accelerator, RP2040 phải tính bằng software | Nếu cần crypto tốc độ cao, xem xét CH582M hoặc ESP32-C3 thay thế |
| **ADC noisy** | RP2040 ADC 12-bit có noise ~10 LSB nếu không có bypass cap | Thêm tụ 100nF giữa ADC pin và GND; lấy trung bình 4-8 mẫu |
| **Watchdog max ~8.3s** | RP2040 WDT max timeout = 8388 ms (24-bit counter @ 12µs tick) | Nếu cần watchdog dài hơn, tự implement bằng timer + counter |
| **UART tốc độ thấp** | 115200 bps = ~11.5 KB/s — không phù hợp stream video/audio | Chỉ dùng cho control + sensor data. Stream lớn thì qua WiFi/LAN |
| **Plaintext protocol** | ASCII không mã hóa — ai có physical access vào UART đều inject lệnh được | Seal hộp, dùng tamper-evident tape (xem §15 Security) |
| **Không có RTC** | RP2040 không có real-time clock onboard — mất thời gian khi mất điện | Dùng DS3231 I²C RTC (10k VNĐ) nối với RP2040 |
| **1 UART duy nhất trên x96q** | UART0 phải chọn giữa serial console HOẶC coprocessor | Disable agetty, dùng SSH qua network thay thế |
| **MicroPython chậm hơn C** | ~100× chậm hơn native ARM cho CPU-intensive tasks | Chỉ ảnh hưởng nếu làm DSP/FFT. GPIO/I²C/PWM vẫn <1ms |
| **RP2040 không sleep mode thấp** | Dormant mode cần external trigger, không tự wake theo timer | Cho idle loop chạy bình thường, consumo ~25mA |

---

## 15. Security considerations

Đây là **pentest drop node** — sẽ được đặt trong mạng / hiện trường mục tiêu. Security là yếu tố sống còn:

### 15.1 Physical security

| Rủi ro | Mô tả | Giảm thiểu |
|---|---|---|
| **UART plaintext** | Bất kỳ ai nối UART adapter vào RP2040 đều inject lệnh được | Seal hộp + tamper-evident tape + epoxy phủ UART pads của RP2040 |
| **USB-C port** | Cắm USB vào RP2040 → flash firmware khác → chiếm quyền | Epoxy phủ USB-C port hoặc dùng Pico không có USB header |
| **x96q USB ports** | USB ports của x96q expose cho attacker plug-in | Disable USB port trong kernel (usbcore.nousb=1) hoặc epoxy |
| **eMMC dễ tháo** | x96q có thể bị đọc eMMC qua NAND reader | Full-disk encryption (LUKS) — nhưng nặng với 1GB RAM |

### 15.2 Network security

| Rủi ro | Giảm thiểu |
|---|---|
| SSH brute-force | Đổi password mạnh, tắt password auth (chỉ SSH key), đổi port |
| Kali tools bị phát hiện | Chạy tools trong container (Docker/Podman), tắt service khi không dùng |
| Reverse shell bị bắt | Mã hóa traffic bằng WireGuard / OpenVPN về C2 server |

### 15.3 Firmware security

- **MicroPython read-back**: ai có USB access có thể dump firmware `.py` từ RP2040 → lộ logic. **Fix:** compile `.py` → `.mpy` (bytecode) + strip comments.
- **Không có secure boot trên Pico**: RP2040 standard không hỗ trợ secure boot. RP2350 (Pico 2) có secure boot + OTP — nếu cần, upgrade lên Pico 2.
- **Protocol không signed**: lệnh ASCII không có HMAC → attacker có thể MITM trên UART line (cần physical access). Với drop node trong hộp seal → chấp nhận được.

### 15.4 Deployment checklist

- [ ] Seal hộp bằng tamper-evident tape
- [ ] Epoxy phủ UART pads + USB-C của RP2040
- [ ] Disable agetty trên x96q (`systemctl mask serial-getty@ttyS0`)
- [ ] Đổi SSH password mặc định
- [ ] Setup WireGuard VPN về C2
- [ ] Test watchdog hoạt động (RP2040 tự reset nếu x96q treo)
- [ ] Flash firmware `.mpy` thay vì `.py`

---

## 16. BOM — Mua gì ở đâu (thị trường Việt Nam)

### 16.1 Tối thiểu (chạy được)

| Linh kiện | Giá (VNĐ) | Nơi mua | Ghi chú |
|---|---:|---|---|
| x96q TV box (H313, 1GB/8GB) | 300-350k | Shopee, Lazada | Cài sẵn Armbian hoặc flash qua USB-C |
| RP2040-Zero (Waveshare) | 55-65k | Shopee, nshop.vn | Nhỏ 23×17mm, USB-C, expose 20 GPIO |
| Dây dupont F-F (3 sợi) | 5k | Shopee | Nối TX/RX/GND |
| **Tổng** | **~370k** |  |  |

### 16.2 Đầy đủ (như sơ đồ §4.3)

| Linh kiện | Giá (VNĐ) | Nơi mua |
|---|---:|---|
| Module tối thiểu (trên) | 370k | — |
| Relay 8 kênh 5V (opto-isolated) | 35k | Shopee |
| LED 5mm + điện trở 220Ω ×4 | 5k | Linh kiện điện tử |
| Button tact ×4 | 5k | Linh kiện điện tử |
| Servo SG90 9g | 25k | Shopee |
| BME280 I²C sensor | 25k | Shopee |
| DHT22 sensor | 30k | Shopee |
| Potentiometer 10kΩ | 3k | Linh kiện điện tử |
| NeoPixel WS2812 strip 8 LED | 35k | Shopee |
| HC-SR04 ultrasonic | 15k | Shopee |
| Buzzer 5V | 5k | Linh kiện điện tử |
| Tụ 100nF ×10 (bypass ADC) | 2k | Linh kiện điện tử |
| USB-C cable (cấp nguồn Pico) | 15k | Shopee |
| Hộp nhựa đựng (enclosure) | 25k | Shopee |
| **Tổng đầy đủ** | **~565k** |  |

### 16.3 Tùy chọn bổ sung

| Linh kiện | Giá | Mục đích |
|---|---:|---|
| USB WiFi adapter RTL8812AU | 150-200k | Monitor mode cho aircrack-ng |
| DS3231 I²C RTC | 10k | Real-time clock cho logging |
| SD card 32GB (cho x96q) | 70k | Lưu log + kết quả scan |
| Power bank 10000mAh | 150k | Chạy hiện trường ~4-6 giờ |

---

## 17. Alternative approaches

UART ASCII không phải cách duy nhất. Đây là các hướng khác đã được cân nhắc:

| Approach | Ưu | Nhược | Tại sao KHÔNG chọn |
|---|---|---|---|
| **SPI** (RP2040 SPI slave) | Tốc độ cao (~10 Mbps), full-duplex | Cần 4 dây (MOSI/MISO/SCK/CS), H313 SPI không expose trên x96q | x96q chỉ có UART pad exposed |
| **I²C multi-master** | Chỉ 2 dây, multi-drop nhiều MCU | Chậm (400 kbps), cần pull-up, H313 I²C cũng không expose | Cùng lý do với SPI |
| **USB-CDC** (RP2040 giả lập USB serial qua USB-C) | Tốc độ cao, chuẩn USB | Chiếm 1 USB port của x96q, vẫn cần firmware, **không fix được FEL race** (USB-C của Pico không nối với UART boot của H313) | Không giải quyết được FEL race |
| **Firmata binary** | Thư viện sẵn (pyFirmata), chuẩn Arduino | Khó debug, cần tool decode, overkill cho use case đơn giản | Chọn ASCII để đơn giản + debug được |
| **MQTT over UART (SLIP)** | Message queue, async, multi-subscriber | Overhead lớn, cần broker, phức tạp cho 2-node | Single drop node không cần message queue |
| **CAN bus** | Chuẩn công nghiệp, noise-immune, multi-drop | Cần transceiver (MCP2551), H313 không có CAN controller, overkill | Chi phí + phức tạp không đáng |
| **I²C expander thuần** (MCP23017) | Rẻ (~10k), 16 GPIO, không firmware | Cần I²C master (H313 không expose), không có ADC/PWM/servo | x96q không expose I²C |
| **USB-to-GPIO bridge** (MCP2221, FT232H, CH347) | Không firmware, plug-and-play | Chiếm USB port, ~35-120k, không custom được logic | Chi phí cao, không linh hoạt |

→ **UART ASCII** là điểm ngọt (sweet spot) cho x96q vì:
1. Pad UART0 **đã được expose sẵn** trên board (duy nhất trong các bus)
2. Đơn giản: 3 dây, 1 dict dispatch, 1 thư viện pySerial
3. Giải quyết được FEL race bằng firmware (không cần thêm phần cứng)
4. Debug được bằng tay, log được ra file

---

## 18. Deployment diagram — x96q trong mạng mục tiêu

Kịch bản triển khai thực tế khi pentest: x96q + RP2040 được cắm vào mạng của target, attacker điều khiển từ xa qua VPN/WireGuard.

```
                       MẠNG MỤC TIÊU (target network)
  ┌──────────────────────────────────────────────────────────────────────┐
  │                                                                      │
  │  ┌─────────────┐    ┌────────────┐    ┌────────────────────────┐    │
  │  │ Workstation │    │ Server     │    │ IP Camera              │    │
  │  │ mục tiêu    │    │ Windows AD │    │ (PoE)                  │    │
  │  │ 192.168.1.10│    │ 192.168.1.2│    │ 192.168.1.50           │    │
  │  └──────┬──────┘    └─────┬──────┘    └──────────┬─────────────┘    │
  │         │                 │                      │                   │
  │         └────────┬────────┴──────────┬───────────┘                   │
  │                  ▼                   ▼                               │
  │            ┌──────────────────────────────┐                         │
  │            │     Switch 24-port mục tiêu  │                         │
  │            │     Port 3: mirror → Port 7  │                         │
  │            └──────────────┬───────────────┘                         │
  │                           │                                          │
  │                           │ Port 7 (mirrored)                       │
  │                           ▼                                          │
  │              ┌────────────────────────────┐                         │
  │              │  x96q (giấu trong hộp điện)│                         │
  │              │  ┌─────────────────────┐   │                         │
  │              │  │  H313 — Armbian     │   │                         │
  │              │  │  • tcpdump          │   │                         │
  │              │  │  • nmap             │   │  USB WiFi (RTL8812AU)   │
  │              │  │  • wireshark        │◄──┼──────────► Internet     │
  │              │  │  • impacket         │   │             (hidden SSID│
  │              │  │  • Flask API        │   │              hoặc 4G)   │
  │              │  │  • Python app       │   │                         │
  │              │  └──────────┬──────────┘   │                         │
  │              │             │ UART0        │                         │
  │              │             ▼              │                         │
  │              │  ┌─────────────────────┐   │                         │
  │              │  │  RP2040 co-processor│   │                         │
  │              │  │  • Relay ×8         │   │                         │
  │              │  │    → kill switch    │   │                         │
  │              │  │    → port mirror    │   │                         │
  │              │  │  • BME280 sensor    │   │                         │
  │              │  │  • PIR detector     │   │                         │
  │              │  │  • Door switch      │   │                         │
  │              │  │  • Buzzer silent    │   │                         │
  │              │  └─────────────────────┘   │                         │
  │              └────────────────────────────┘                         │
  │                                                                      │
  └──────────────────────────────────────────────────────────────────────┘
                                    │
                              WireGuard VPN
                              (UDP 51820)
                                    │
                                    ▼
  ┌─────────────────────────────────────────────────────────────────────┐
  │   Attacker (bên ngoài)                                              │
  │                                                                     │
  │   ┌────────────┐          ┌────────────┐          ┌────────────┐   │
  │   │ Laptop     │          │ C2 Server  │          │ Điện thoại │   │
  │   │ SSH vào    │          │ Lưu loot   │          │ Alert      │   │
  │   │ x96q qua   │          │ + logs     │          │ PIR/door   │   │
  │   │ VPN        │          │            │          │ MQTT       │   │
  │   └────────────┘          └────────────┘          └────────────┘   │
  └─────────────────────────────────────────────────────────────────────┘
```

### Flow hoạt động

1. **Attacker** vào building mục tiêu (disguise thành technician), đặt x96q vào hộp điện, nối dây LAN vào switch port 7
2. RP2040 relay **đóng port mirror** (port 3 → port 7) — switch mirror traffic của workstation mục tiêu về x96q
3. x96q chạy `tcpdump -i eth0` bắt traffic → lưu vào eMMC hoặc stream qua WireGuard về C2
4. **PIR sensor** phát hiện người vào phòng → RP2040 gửi `PUSH GPIO` → x96q tạm dừng noisy scan (nmap), chỉ chạy passive sniff
5. **Door switch** mở → gửi alert MQTT về điện thoại attacker
6. Khi bị nghi ngờ, attacker remote SSH → `co.digital_write(9, 0)` để **ngắt relay** → x96q biến mất khỏi mạng không để lại dấu vết (chỉ là 1 box trong hộp điện)

---

## 19. Performance benchmarks (ước tính lý thuyết)

> **Lưu ý:** các con số dưới đây là **ước tính dựa trên datasheet RP2040 + pySerial latency**, chưa đo trên thiết bị thực. Sai số ±30%. Khi có board thật sẽ bổ sung số đo thực tế.

### 19.1 UART round-trip latency

| Kịch bản | Ước tính | Ghi chú |
|---|---:|---|
| **Lệnh ngắn** (`PING`) | **~8-12 ms** | 13 byte gửi + 9 byte nhận @ 115200 bps + Python pySerial overhead |
| **Lệnh trung bình** (`GPIO 5 HIGH`) | **~10-15 ms** | 13 byte + RP2040 dispatch ~100µs |
| **Lệnh có ADC read** (`ADC 26`) | **~12-18 ms** | ADC conversion ~50µs + hex format + reply |
| **Lệnh I²C read 1 byte** (`I2C R 0x76 0xD0 1`) | **~15-25 ms** | I²C @ 400kHz ~50µs/byte + parse hex response |
| **Lệnh I²C read 32 bytes** | **~25-40 ms** | 32 bytes I²C + 64 hex chars response |
| **Servo set angle** (`SERVO 0 90`) | **~10-15 ms** | PWM reconfigure ~5µs, settle time servo ~20ms (không tính) |
| **DHT22 read** (`DHT 22`) | **~25-30 ms** | DHT protocol cần 5ms start + 4ms data + retry |
| **NeoPixel 8 LED** (`NEO 15 8 ...`) | **~10-15 ms** | WS2812 ~30µs/LED = 240µs + Python parse hex |

### 19.2 Throughput

| Kịch bản | Ước tính | Ghi chú |
|---|---:|---|
| **GPIO toggle rate (từ Python)** | ~80-100 lần/giây | Round-trip 10ms → max 100 Hz |
| **GPIO toggle rate (firmware loop)** | ~1 MHz | Native M0+ @ 133MHz, không qua UART |
| **I²C throughput (400 kHz mode)** | ~35 KB/s | Lý thuyết 50 KB/s, overhead ACK/NACK |
| **ADC sampling rate (firmware local)** | ~500 kS/s | RP2040 ADC spec @ 12-bit |
| **ADC samples streamed về x96q** | ~2 kS/s | Bottleneck UART + Python parse |
| **PWM frequency tối đa** | 100 MHz | RP2040 PWM clock, nhưng duty resolution giảm |
| **Servo update rate** | 50 Hz | Chuẩn hobby servo (20ms period) |

### 19.3 So sánh với các giải pháp khác

| Giải pháp | GPIO toggle từ Linux | I²C throughput |
|---|---:|---:|
| **RP2040 qua UART ASCII (armkali)** | ~100 Hz | ~35 KB/s |
| **RP2040 qua USB-CDC** | ~1 kHz | ~1 MB/s |
| **MCP2221A (USB-I²C/GPIO)** | ~500 Hz | ~100 KB/s |
| **FT232H (USB-MPSSE)** | ~5 kHz | ~6 MB/s |
| **GPIO sysfs native (nếu có)** | ~100 kHz | N/A |
| **Linux libgpiod (native)** | ~1 MHz | N/A |

→ **UART ASCII là chậm nhất** nhưng vẫn đủ cho use case điều khiển relay/sensor (không cần real-time < 1ms). Nếu cần cao hơn: chuyển firmware sang C++ hoặc dùng USB-CDC.

### 19.4 Resource usage

| Tài nguyên | RP2040 | x96q (Python client) |
|---|---:|---:|
| **RAM (firmware idle)** | ~50 KB (uPy interpreter) | ~5 MB (Python + pySerial) |
| **Flash (firmware)** | ~1 MB (uPy + main.py) | N/A |
| **CPU idle** | ~25 mA @ 3.3V (~82 mW) | <0.1% H313 CPU |
| **CPU khi xử lý lệnh** | +10 mA transient | +0.5% H313 CPU |

---

## 20. Schematic PCB custom — hàn RP2040 trực tiếp vào x96q

Nếu không dùng module Pico có sẵn (kích thước lớn, ~50×21mm), bạn có thể thiết kế PCB custom **30×20mm** hàn trực tiếp vào board x96q — nhỏ hơn, chắc chắn hơn, khó bị phát hiện hơn khi triển khai hiện trường.

### 20.1 File schematic

📎 **File SVG:** [`schematic/pcb-custom-rp2040.svg`](./schematic/pcb-custom-rp2040.svg)

Mở bằng trình duyệt hoặc Inkscape để xem chi tiết.

### 20.2 Component list (PCB custom)

| Component | Package | Số lượng | Chức năng |
|---|---|:---:|---|
| RP2040 | QFN-56 (7×7mm) | 1 | MCU chính |
| W25Q16 | SOIC-8 | 1 | SPI Flash 2MB (lưu firmware uPy) |
| AMS1117-3.3 | SOT-223 | 1 | LDO hạ 5V → 3.3V |
| 12 MHz crystal | 3215 (3.2×1.5mm) | 1 | Clock cho RP2040 (PLL nhân lên 133MHz) |
| 100nF MLCC | 0402 | 10 | Bypass cap cho mỗi VCC pin |
| 10µF tantalum | 0805 | 2 | Bulk cap input/output LDO |
| 27Ω resistor | 0402 | 2 | USB D+/D- termination (nếu cần USB) |
| Header 3-pin 2.54mm | THT | 1 | UART (TX/RX/GND → pad x96q) |
| Header 4-pin 2.54mm | THT | 1 | I²C (3V3/SDA/SCL/GND) |
| Header 2×16 pin 2.54mm | THT | 1 | GPIO breakout |
| Tact switch 6×6mm | THT | 1 | RESET + BOOTSEL (2 chức năng) |
| LED 0805 | SMD | 2 | PWR (xanh) + USR (đỏ) |

**Tổng chi phí linh kiện:** ~60k VNĐ/board (nếu mua số lượng 10+ từ LCSC/JLCPCB).

### 20.3 Layout considerations

| Yếu tố | Khuyến nghị |
|---|---|
| **Số lớp** | **2-layer** đủ cho RP2040 (không cần 4-layer như STM32H7) |
| **Độ dày PCB** | 1.6mm tiêu chuẩn, hoặc 1.0mm nếu muốn mỏng để nhét vào khe |
| **Copper weight** | 35µm (1 oz) — đủ cho dòng max ~500mA của LDO |
| **Ground plane** | Pour GND ở cả 2 lớp, nối qua nhiều via — giảm noise ADC |
| **Crystal placement** | Đặt gần RP2040 (<5mm), trace ngắn và symmetric |
| **Bypass caps** | 100nF đặt **ngay sát mỗi VCC pin** của RP2040 (<2mm) |
| **ADC pins** | Tách xa digital signals, thêm ground guard ring nếu cần accuracy cao |
| **USB traces** | 90Ω differential impedance, dài bằng nhau (chỉ cần nếu dùng USB) |

### 20.4 Cách hàn vào x96q

**Option A: Header pin (removable)**
- Hàn 3-pin header vào PCB custom
- Dùng dây dupont nối với pad UART của x96q
- **Ưu:** tháo ra được, dễ debug
- **Nhược:** cồng kềnh, dễ lỏng

**Option B: Solder trực tiếp (permanent)**
- Hàn 3 dây trực tiếp từ PCB custom vào pad UART x96q
- Dùng kapton tape cách ly
- **Ưu:** chắc chắn, nhỏ gọn
- **Nhược:** khó tháo, cần kỹ năng hàn tốt

**Option C: Pogo pins (production)**
- PCB custom có 3 test-point pads
- Dùng pogo pin jig khi flash/debug
- **Ưu:** không cần hàn, test hàng loạt
- **Nhược:** cần jig, chỉ phù hợp sản xuất số lượng

### 20.5 Flash firmware lần đầu

PCB custom **không có USB** (để tiết kiệm diện tích) → phải flash qua **SWD** (Serial Wire Debug):

```bash
# Dùng Raspberry Pi Debug Probe hoặc Picoprobe (~150k VNĐ)
# Nối SWDIO + SWCLK + GND từ probe → test-point trên PCB custom
openocd -f interface/cmsis-dap.cfg -f target/rp2040.cfg \
        -c "program firmware.uf2 reset exit"
```

Sau lần flash đầu, firmware MicroPython có thể update qua UART từ x96q (dùng `mpremote` qua `/dev/ttyS0`).

### 20.6 Khi nào dùng PCB custom vs Pico module?

| Tiêu chí | Pico module | PCB custom |
|---|---|---|
| Prototype nhanh | ✅ | ❌ |
| Sản xuất >10 cái | ❌ (55k/cái) | ✅ (~25k/cái) |
| Kích thước | 50×21mm | **30×20mm** |
| Cần USB để debug | ✅ | ❌ (cần SWD) |
| Triển khai hiện trường | ⚠️ (cồng kềnh) | ✅ (nhỏ, khó phát hiện) |

→ **Khuyến nghị:** prototype với Pico module, khi đã ổn thì thiết kế PCB custom cho deployment thật.

---

## 21. Tóm tắt cuối

> **Ý tưởng cốt lõi:** biến TV box x96q ~300k thành **pentest drop node có GPIO đầy đủ** bằng cách ghép nó với RP2040 ~60k theo kiến trúc LattePanda, giao tiếp qua UART bằng protocol ASCII đơn giản — giải quyết đồng thời 3 vấn đề: thiếu GPIO trên H313, FEL race khi boot, và khó debug với binary protocol.

**Tổng chi phí:** ~360k VNĐ cho 1 pentest drop node hoàn chỉnh với 28 GPIO usable + WiFi (USB adapter) + Kali tools — rẻ hơn **~5×** so với setup tương đương dùng Raspberry Pi 4 4GB (~1.5M) + GPIO HAT (~300k) = ~1.8M.
