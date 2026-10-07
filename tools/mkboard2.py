#!/usr/bin/env python3
"""用本机提取的 BDF 构造主线 ath11k 需要的 board-2.bin 容器。

背景
----
主线 OpenWrt 的 ath11k 从 /lib/firmware/ath11k/<SoC>/hw1.0/board-2.bin 读取板级数据，
格式是「容器」：84 字节头（含 board 名）+ 128 KiB body。

原厂的 BDF 是**裸 body**（bdwlan.bXX，131072 字节），运行时由 cnss 驱动加载；
主线 ath11k 不吃裸 body，必须包一层容器。

容器头格式（由参考项目 H3C-RT3000-Product-R1 的成品文件逐字节核对）:

    0x00  "QCA-ATH11K-BOARD\0"        17 字节
    0x11  "mmm\0"                      4 字节（未知用途，沿用）
    0x15  \x00\x00\x00                 3 字节
    0x18  u32  0x00020038              沿用参考值
    0x1c  u32  0
    0x20  u32  38                      board 名字串长度（固定 38）
    0x24  board 名 38 字节             bus=ahb,qmi-chip-id=0,qmi-board-id=<N>
    0x4a  "mm"                         2 字节填充，使 IE 对齐到 0x4c
    0x4c  IE 标记 struct("<II",1,0x20000)
    0x54  body 131072 字节
    总计  131156 字节

board-id 来自内核命令行 cnss2.bdf_* 参数：
    bdf_pci0=0x50      -> 5G(QCN6102/6122) 用 bdwlan.b50 -> board-id = 80
    bdf_integrated=0x10 -> 2.4G(IPQ5018)   用 bdwlan.b10 -> board-id = 16

用法
----
    mkboard2.py --body bdf/caldata_1.bin   --board-id 80 --out board-h3c_rc3000.qcn6122
    mkboard2.py --body bdf/caldata_2g.bin  --board-id 16 --out board-h3c_rc3000.ipq5018
"""

from __future__ import annotations

import argparse
import struct
import sys
from functools import reduce
from pathlib import Path

MAGIC = b"QCA-ATH11K-BOARD\0"
NAME_FIELD_LEN = 38
BODY_SIZE = 0x20000
HEADER_SIZE = 84
CONTAINER_SIZE = HEADER_SIZE + BODY_SIZE      # 131156
BDF_CHECKSUM_GOAL = 0xFFFF

# 本机的 board-id（来自 cnss2.bdf_*）
DEFAULT_IDS = {"5g": 80, "2g": 16}


def bdf_xor(body: bytes) -> int:
    if len(body) % 2:
        raise ValueError("body 长度必须是偶数")
    return reduce(int.__xor__, struct.unpack(f"<{len(body)//2}H", body), 0)


def build(body: bytes, board_id: int) -> bytes:
    if len(body) != BODY_SIZE:
        raise ValueError(f"body 必须是 {BODY_SIZE} 字节，实际 {len(body)}")
    name = f"bus=ahb,qmi-chip-id=0,qmi-board-id={board_id}".encode()
    if len(name) > NAME_FIELD_LEN:
        raise ValueError(f"board 名超长: {len(name)} > {NAME_FIELD_LEN}")

    hdr = bytearray(HEADER_SIZE)
    hdr[0x00:0x11] = MAGIC
    hdr[0x11:0x15] = b"mmm\0"
    hdr[0x18:0x1C] = struct.pack("<I", 0x00020038)
    hdr[0x1C:0x20] = struct.pack("<I", 0)
    hdr[0x20:0x24] = struct.pack("<I", NAME_FIELD_LEN)
    hdr[0x24:0x24 + NAME_FIELD_LEN] = name.ljust(NAME_FIELD_LEN, b"\0")
    hdr[0x4A:0x4C] = b"mm"
    hdr[0x4C:0x54] = struct.pack("<II", 1, BODY_SIZE)
    return bytes(hdr) + body


def verify(data: bytes, board_id: int) -> list[str]:
    errs = []
    if len(data) != CONTAINER_SIZE:
        errs.append(f"总长 {len(data)} != {CONTAINER_SIZE}")
    if not data.startswith(MAGIC):
        errs.append("magic 不对")
    if data.find(struct.pack("<II", 1, BODY_SIZE), 20, 512) != 0x4C:
        errs.append("IE 标记不在 0x4c")
    want = f"qmi-board-id={board_id}".encode()
    if want not in data[:HEADER_SIZE]:
        errs.append(f"header 里找不到 {want.decode()}")
    body = data[HEADER_SIZE:]
    ck = bdf_xor(body)
    if ck != BDF_CHECKSUM_GOAL:
        errs.append(f"body XOR = 0x{ck:04x}，期望 0x{BDF_CHECKSUM_GOAL:04x}")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser(description="构造 ath11k board-2.bin 容器")
    ap.add_argument("--body", required=True, help="本机裸 BDF（caldata_*.bin，131072 字节）")
    ap.add_argument("--board-id", type=int, required=True, help="如 5G=80, 2.4G=16")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    body = Path(args.body).read_bytes()
    try:
        out = build(body, args.board_id)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2

    errs = verify(out, args.board_id)
    if errs:
        for e in errs:
            print(f"自检失败: {e}", file=sys.stderr)
        return 1

    Path(args.out).write_bytes(out)
    print(f"已生成 {args.out}")
    print(f"  board-id : {args.body} -> {args.board_id}")
    print(f"  总长     : {len(out)}")
    print(f"  body XOR : 0x{bdf_xor(body):04x} (有效)")
    import hashlib
    print(f"  SHA-256  : {hashlib.sha256(out).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
