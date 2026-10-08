#!/usr/bin/env python3
"""用本机提取的 BDF 构造主线 ath11k 需要的 board-2.bin 容器。

容器格式（2026-10-08 按官方 openwrt/firmware_qca-wireless 的
board-cmcc_mr3000d-ci.{ipq5018,qcn6122} 逐字节复刻，总长 131188）：

    0x00  "QCA-ATH11K-BOARD\0"                       17B
    0x11  "mmm"                                       3B
    0x14  00 00 00 00                                  4B
    0x18  u32 0x00020058                               4B
    0x1C  u32 0                                        4B
    0x20  u32 38（name 长度）                           4B
    0x24  "bus=ahb,qmi-chip-id=0,qmi-board-id=255"    38B
    0x4A  "mm\0\0\0\0"                                 6B
    0x50  u32 23（variant 长度）                        4B
    0x54  "variant=<VARIANT>"                         23B
    0x6B  "m"                                          1B
    0x6C  IE: u32 id=1, u32 len=0x20000                8B
    0x74  body 0x20000                              128KiB

关键修复（相对旧版）：
  1. board 名含 qmi-board-id=255（ath11k QMI 上报的默认值），旧版用 16/80 不匹配；
  2. 增加 "variant=<VARIANT>" 段（DT 里 qcom,ath11k-calibration-variant）；
  3. 头部总长精确 0x74，body 从偶数偏移开始。

用法
----
    mkboard2.py --body bdf/caldata_2g.bin --out openwrt/board/board-h3c_rc3000.ipq5018
    mkboard2.py --body bdf/caldata_1.bin --out openwrt/board/board-h3c_rc3000.qcn6122
"""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from functools import reduce
from pathlib import Path

MAGIC = b"QCA-ATH11K-BOARD\0"                            # 17B
BOARD_NAME = b"bus=ahb,qmi-chip-id=0,qmi-board-id=255"   # 38B
BODY_SIZE = 0x20000
BDF_CHECKSUM_OFFSET = 0x0A
BDF_CHECKSUM_GOAL = 0xFFFF
HEADER_SIZE = 0x74                                       # 116


def bdf_xor(body: bytes) -> int:
    n = len(body) // 2 * 2
    return reduce(int.__xor__, struct.unpack(f"<{n//2}H", body[:n]), 0)


def fix_checksum(body: bytearray) -> bytearray:
    """调整 body 偏移 0x0A 的 LE u16，使整块按 u16 XOR 结果 = 0xFFFF。"""
    delta = bdf_xor(body) ^ BDF_CHECKSUM_GOAL
    old = struct.unpack("<H", body[BDF_CHECKSUM_OFFSET:BDF_CHECKSUM_OFFSET + 2])[0]
    body[BDF_CHECKSUM_OFFSET:BDF_CHECKSUM_OFFSET + 2] = struct.pack("<H", old ^ delta)
    return body


def build(body: bytes, variant: str) -> bytes:
    if len(body) != BODY_SIZE:
        raise ValueError(f"body 必须是 {BODY_SIZE} 字节，实际 {len(body)}")

    var = f"variant={variant}".encode()
    out = bytearray()
    out += MAGIC                                # 0x00-0x10（17B）
    out += b"mmm"                               # 0x11-0x13（3B）
    out += b"\0\0\0\0"                          # 0x14-0x17（4B）
    out += struct.pack("<I", 0x00020058)        # 0x18-0x1B
    out += struct.pack("<I", 0)                 # 0x1C-0x1F
    out += struct.pack("<I", len(BOARD_NAME))   # 0x20-0x23 = 38
    out += BOARD_NAME                           # 0x24-0x49
    out += b"mm\0\0\0\0"                        # 0x4A-0x4F（6B）
    out += struct.pack("<I", len(var))          # 0x50-0x53 = 23
    out += var                                  # 0x54-0x6A
    out += b"m"                                 # 0x6B（1B）
    out += struct.pack("<II", 1, BODY_SIZE)     # 0x6C-0x73 IE(id=1, len)
    out += body                                 # 0x74 + 0x20000
    return bytes(out)


def verify(data: bytes, variant: str) -> list[str]:
    errs: list[str] = []
    var = f"variant={variant}".encode()
    # 动态布局：0x50 len字段(4) + var + "m"(1) → IE(8) → body
    ie_off = 0x50 + 4 + len(var) + 1
    total = ie_off + 8 + BODY_SIZE
    if len(data) != total:
        errs.append(f"总长 {len(data)} != {total}")
    if not data.startswith(MAGIC):
        errs.append("magic 不对")
    if data[0x11:0x14] != b"mmm":
        errs.append("0x11 处不是 'mmm'")
    if struct.unpack("<I", data[0x18:0x1C])[0] != 0x00020058:
        errs.append("0x18 长度标记不是 0x00020058")
    if struct.unpack("<I", data[0x20:0x24])[0] != len(BOARD_NAME):
        errs.append("name_len 字段不是 38")
    if data[0x24:0x24 + len(BOARD_NAME)] != BOARD_NAME:
        errs.append("board 名不是 bus=ahb,...board-id=255")
    if struct.unpack("<I", data[0x50:0x54])[0] != len(var):
        errs.append(f"variant_len 字段不是 {len(var)}")
    if data[0x54:0x54 + len(var)] != var:
        errs.append("variant 段内容不匹配")
    if struct.unpack("<II", data[ie_off:ie_off + 8]) != (1, BODY_SIZE):
        errs.append(f"IE 标记 (1, 0x20000) 不在 0x{ie_off:x}")
    body = data[ie_off + 8:]
    ck = bdf_xor(body)
    if ck != BDF_CHECKSUM_GOAL:
        errs.append(f"body XOR = 0x{ck:04x}，期望 0x{BDF_CHECKSUM_GOAL:04x}")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser(description="构造 ath11k board-2.bin 容器（官方格式）")
    ap.add_argument("--body", required=True, help="本机裸 BDF（caldata_*.bin，131072 字节）")
    ap.add_argument("--variant", default="H3C-RC3000", help="DT 里 qcom,ath11k-calibration-variant 的值")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    body = bytearray(Path(args.body).read_bytes())
    if len(body) != BODY_SIZE:
        print(f"错误: body 大小 {len(body)} != {BODY_SIZE}", file=sys.stderr)
        return 2
    if bdf_xor(body) != BDF_CHECKSUM_GOAL:
        fix_checksum(body)
        print("注: body XOR 已自动修正为 0xFFFF")

    try:
        out = build(bytes(body), args.variant)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2

    errs = verify(out, args.variant)
    if errs:
        for e in errs:
            print(f"自检失败: {e}", file=sys.stderr)
        return 1

    Path(args.out).write_bytes(out)
    print(f"已生成 {args.out}")
    print(f"  variant  : {args.variant}")
    print(f"  总长     : {len(out)} (官方样本 131188)")
    print(f"  body XOR : 0x{bdf_xor(body):04x} (有效)")
    print(f"  SHA-256  : {hashlib.sha256(out).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
