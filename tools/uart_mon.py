#!/usr/bin/env python3
"""RC3000 串口自动接管 v2：断线自动重连 + U-Boot 自动中断 + 文件接口发命令。
用法:
  后台监视:  python3 tools/uart_mon.py /dev/tty.usbserial-XXXX
  发命令:    echo "printenv bootcmd" > /tmp/uart_cmd
  看日志:    tail -50 /tmp/uart.log
"""
import serial, time, sys, os

PORT = sys.argv[1]
log = open("/tmp/uart.log", "ab", 0)

def w(s):
    log.write(s.encode())
    try:
        print(s, end="", flush=True)
    except Exception:
        pass

ser = None
boot_seen = False
burst_until = 0

while True:
    # ---- 保证串口打开（断线自动重连）----
    if ser is None:
        try:
            ser = serial.Serial(PORT, 115200, timeout=0.2)
            boot_seen = False  # 重连后重新武装自动中断
            w(f"\n===== UART OPEN {time.strftime('%H:%M:%S')} on {PORT} =====\n")
        except Exception as e:
            w(f"\n[等待设备 {PORT} 出现...] {time.strftime('%H:%M:%S')}\n")
            time.sleep(2)
            continue

    # ---- 文件接口发命令 ----
    try:
        with open("/tmp/uart_cmd") as f:
            cmd = f.read().strip()
        os.remove("/tmp/uart_cmd")
        if cmd:
            ser.write((cmd + "\n").encode())
            w(f"\n>>> SENT: {cmd} <<<\n")
            time.sleep(0.15)
    except FileNotFoundError:
        pass
    except Exception:
        ser = None
        continue

    # ---- 读数据 ----
    try:
        data = ser.read(4096)
    except Exception:
        w(f"\n[串口断开，2 秒后自动重连...] {time.strftime('%H:%M:%S')}\n")
        try:
            ser.close()
        except Exception:
            pass
        ser = None
        time.sleep(2)
        continue

    if data:
        log.write(data)
        try:
            print(data.decode(errors="replace"), end="", flush=True)
        except Exception:
            pass
        if not boot_seen and (b"U-Boot" in data or b"bootdelay" in data.lower()):
            boot_seen = True
            burst_until = time.time() + 3.0
            w("\n>>> [AUTO] 检测到 U-Boot 启动，3 秒内连发回车尝试中断 <<<\n")

    if boot_seen and time.time() < burst_until:
        try:
            ser.write(b"\n")
        except Exception:
            ser = None
        time.sleep(0.2)
