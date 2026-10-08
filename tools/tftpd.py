#!/usr/bin/env python3
"""极简 TFTP server，只实现 WRQ（接收设备上传），用于 RC3000 分区备份。

只收不发，不接受读请求，避免把本机文件暴露出去。

用法:
    python3 tools/tftpd.py --port 6969 --dir ./backups
"""

from __future__ import annotations

import argparse
import os
import socket
import struct
import sys
import time
from pathlib import Path

OP_RRQ, OP_WRQ, OP_DATA, OP_ACK, OP_ERROR = 1, 2, 3, 4, 5
BLOCK = 512
MAXBUF = 65536 + 4          # 支持对端用 -b 指定更大的块
TFTP_MAX = 65535 * BLOCK    # 33553408 字节，16 位块号的硬上限


def recv_file(addr: tuple, sock: socket.socket, filename: str, outdir: Path) -> None:
    mode = b"octet"
    ack = struct.pack("!HH", OP_ACK, 0)
    sock.sendto(ack, addr)

    out = outdir / sanitize(filename)
    total = 0
    expect = 1
    t0 = time.time()
    with open(out, "wb") as fh:
        while True:
            sock.settimeout(20)
            try:
                data, src = sock.recvfrom(MAXBUF)
            except socket.timeout:
                print(f"  ! {filename}: 超时，已收 {total} 字节")
                return
            if src != addr:
                continue
            op, blk = struct.unpack("!HH", data[:4])
            if op == OP_ERROR:
                print(f"  ! {filename}: 对端报错 {data[4:].decode('utf-8','replace')}")
                return
            if op != OP_DATA:
                continue
            payload = data[4:]
            if blk == expect:
                fh.write(payload)
                total += len(payload)
                expect += 1
            # 回 ACK 用收到的块号（重复块也要 ACK，否则对端会一直重传）
            sock.sendto(struct.pack("!HH", OP_ACK, blk), addr)
            if len(payload) < BLOCK:
                break
    dt = time.time() - t0
    size = out.stat().st_size
    # TFTP 块号是 16 位：最多 65535 × 512 = 33553408 字节，超过会回绕。
    # 40MB 的 rootfs 正好踩这个坑，文件被静默截断，必须分卷传输。
    if expect > 65536 or size > TFTP_MAX:
        print(f"  ! {out.name}: {size} bytes —— 已到/超过 TFTP 的 32MiB 上限，"
              f"文件很可能被截断了！请用 dd count/skip 分卷后重传。")
        return
    print(f"  ok {out.name}: {size} bytes ({size/1024/1024:.1f} MiB) in {dt:.1f}s "
          f"[{size/max(dt,0.01)/1024:.0f} KiB/s]")


def sanitize(name: str) -> str:
    """防目录穿越，把名字压成安全的文件名。"""
    base = os.path.basename(name.replace("\\", "/"))
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in base)
    return safe or "upload.bin"


def main() -> int:
    ap = argparse.ArgumentParser(description="只接收的 TFTP server（用于 RC3000 备份）")
    ap.add_argument("--port", type=int, default=6969)
    ap.add_argument("--dir", default="./backups")
    ap.add_argument("--bind", default="0.0.0.0")
    args = ap.parse_args()

    outdir = Path(args.dir)
    outdir.mkdir(parents=True, exist_ok=True)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.bind, args.port))
    print(f"TFTP 接收服务已启动: UDP {args.port} -> {outdir.resolve()}")
    print("等待设备上传…（Ctrl-C 结束）")

    try:
        while True:
            # [修复] recv_file 里 settimeout(20) 会泄漏到主循环：
            # 20 秒内没有新上传就 TimeoutError 未捕获 → 整进程崩溃。
            # 每轮回主循环前恢复阻塞模式。
            sock.settimeout(None)
            data, addr = sock.recvfrom(1500)
            if len(data) < 4:
                continue
            op = struct.unpack("!H", data[:2])[0]
            if op == OP_WRQ:
                parts = data[2:].split(b"\0")
                fname = parts[0].decode("utf-8", "replace")
                print(f"[接收] {addr[0]} -> {fname}")
                try:
                    recv_file(addr, sock, fname, outdir)
                except Exception as e:  # noqa: BLE001
                    print(f"  ! 处理 {fname} 出错: {e}")
            elif op == OP_RRQ:
                err = struct.pack("!HH", OP_ERROR, 2) + b"read not allowed\0"
                sock.sendto(err, addr)
                print(f"[拒绝] {addr[0]} 的读请求")
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        sock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
