#!/usr/bin/env python3
"""工具自测。运行: python3 tests/test_tools.py"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import cfgtool      # noqa: E402
import hwinfo2dts   # noqa: E402
import art2board    # noqa: E402


def make_cfg(body: str, token: str = "/var/run/.tmpcfg", md5: str | None = None) -> bytes:
    b = body.encode()
    m = md5 or hashlib.md5(b).hexdigest()
    return f"{m} {token}\n".encode() + b


SAMPLE_BODY = """@telnet
telnetenable=disable
custconf-strongpwd-status=enable
custconf-common-operator=0001
custconf-common-district=510000
@system
hostname=RC3000
"""


class TestCfgTool(unittest.TestCase):
    def test_split_header(self):
        raw = make_cfg(SAMPLE_BODY)
        token, body = cfgtool.split_header(raw)
        self.assertEqual(token, "/var/run/.tmpcfg")
        self.assertEqual(body.decode(), SAMPLE_BODY)

    def test_bad_header(self):
        with self.assertRaises(cfgtool.CfgError):
            cfgtool.split_header(b"garbage\nbody\n")

    def test_verify_ok_and_bad(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.cfg"
            p.write_bytes(make_cfg(SAMPLE_BODY))
            self.assertTrue(cfgtool.verify(p))

            p2 = Path(d) / "b.cfg"
            p2.write_bytes(make_cfg(SAMPLE_BODY, md5="0" * 32))
            self.assertFalse(cfgtool.verify(p2))

    def test_unlock_rewrites_and_resigns(self):
        raw = make_cfg(SAMPLE_BODY)
        token, body = cfgtool.split_header(raw)
        new_body, changed = cfgtool.set_values(
            body, [("telnetenable", "enable"), ("custconf-common-operator", "0000")]
        )
        out = cfgtool.resign(new_body, token)

        self.assertIn(b"telnetenable=enable", new_body)
        self.assertIn(b"custconf-common-operator=0000", new_body)
        self.assertNotIn(b"custconf-common-operator=0001", new_body)
        self.assertGreaterEqual(len(changed), 2)

        # 重签后必须能过校验
        stored = out[: out.find(b"\n")].decode().split()[0]
        self.assertEqual(stored, cfgtool.body_md5(new_body))

    def test_set_does_not_append_by_default(self):
        _, body = cfgtool.split_header(make_cfg("@telnet\ntelnetenable=disable\n"))
        new_body, changed = cfgtool.set_values(body, [("custconf-common-district", "000000")])
        self.assertNotIn(b"custconf-common-district", new_body)
        self.assertTrue(any(c.startswith("  -") for c in changed))

    def test_set_appends_when_asked(self):
        _, body = cfgtool.split_header(make_cfg("@telnet\ntelnetenable=disable\n"))
        new_body, changed = cfgtool.set_values(
            body, [("custconf-common-district", "000000")], append=True
        )
        self.assertIn(b"custconf-common-district=000000", new_body)
        self.assertTrue(any(c.startswith("  +") for c in changed))

    def test_unlock_values_match_vendor_scheme(self):
        """status 类字段必须是 yes/no，不能写成 enable/disable。"""
        for key, val, _ in cfgtool.UNLOCK_ITEMS:
            if key.endswith("-status"):
                self.assertIn(val, ("yes", "no"), f"{key} 的目标值 {val} 不符合 yes/no 体系")

    def test_preserves_indent_and_no_spaces(self):
        _, body = cfgtool.split_header(make_cfg("@telnet\n\ttelnetenable=disable\n"))
        new_body, _ = cfgtool.set_values(body, [("telnetenable", "enable")])
        self.assertIn(b"\ttelnetenable=enable", new_body)
        self.assertNotIn(b"telnetenable =", new_body)

    def test_unlock_idempotent(self):
        _, body = cfgtool.split_header(make_cfg(SAMPLE_BODY))
        b1, _ = cfgtool.set_values(body, [("telnetenable", "enable")])
        b2, changed = cfgtool.set_values(b1, [("telnetenable", "enable")])
        self.assertEqual(b1, b2)
        self.assertTrue(any("已是目标值" in c for c in changed))

    def test_cli_unlock_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "rc3000.cfg"
            src.write_bytes(make_cfg(SAMPLE_BODY))
            dst = Path(d) / "out.cfg"
            rc = cfgtool.main(["unlock", str(src), "-o", str(dst)])
            self.assertEqual(rc, 0)
            self.assertTrue(cfgtool.verify(dst))
            self.assertIn(b"telnetenable=enable", dst.read_bytes())


class TestHwinfo2Dts(unittest.TestCase):
    INFO = {
        "model": "RC3000",
        "soc": "IPQ5018",
        "mem_mb": "256",
        "nand_mb": "128",
        "switch": "qca8337",
        "rootfs_mtd": "16",
        "kernel_mtd": "mtd21",
        "mtd_count": "29",
        "uboot_baud": "115200",
        "fw_version": "005",
        "mtd_raw": [],
    }

    def test_generate_qca8337(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            r = hwinfo2dts.generate(dict(self.INFO), out)
            self.assertEqual(r["switch"], "qca8337")
            self.assertTrue(Path(r["dts"]).exists())
            self.assertIn("h3c,rc3000", Path(r["dts"]).read_text())
            self.assertIn("h3c_rc3000", Path(r["mk"]).read_text())

    def test_generate_rtl8367s(self):
        with tempfile.TemporaryDirectory() as d:
            info = dict(self.INFO, switch="rtl8367s")
            r = hwinfo2dts.generate(info, Path(d))
            text = Path(r["dts"]).read_text()
            self.assertIn("rtl8365mb", text)
            self.assertIn("kmod-dsa-rtl8365mb", Path(r["mk"]).read_text())

    def test_unknown_switch_falls_back(self):
        with tempfile.TemporaryDirectory() as d:
            r = hwinfo2dts.generate(dict(self.INFO, switch="unknown"), Path(d))
            self.assertIn("qca,qca8337", Path(r["dts"]).read_text())

    def test_validate_shipped_dts(self):
        for f in (ROOT / "openwrt" / "dts").glob("ipq5018-h3c-rc3000*.dts"):
            errs = hwinfo2dts.validate(f)
            self.assertEqual(errs, [], f"{f.name} 校验失败: {errs}")

    def test_validate_detects_broken(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "broken.dts"
            p.write_text("/dts-v1/;\n#include \"x.dtsi\"\n/ {\n\tmodel = \"a\"\n};\n")
            self.assertTrue(hwinfo2dts.validate(p))


def build_container(body: bytes, name: bytes = b"bus=ahb,qmi-chip-id=0,qmi-board-id=144") -> bytes:
    """按已核实的格式拼一个 ath11k board-2 容器：
    MAGIC(17B) + header(到 84) + IE 标记(8B@76) + body(128KiB)。"""
    buf = bytearray(art2board.BOARD_SIZE)
    buf[0 : len(art2board.MAGIC)] = art2board.MAGIC
    buf[20 : 20 + len(name)] = name
    struct.pack_into("<II", buf, 76, 1, art2board.BODY_SIZE)
    buf[84 : 84 + art2board.BODY_SIZE] = body
    return bytes(buf)


class TestArt2Board(unittest.TestCase):
    def _body(self) -> bytearray:
        body = bytearray(art2board.BODY_SIZE)
        n = 0x2758 - 0x2494
        body[0x2494:0x2758] = bytes((i % 251) + 1 for i in range(n))  # 可识别、无 0x00/0xFF
        art2board.repair_checksum(body)
        return body

    def test_parse_container(self):
        c = build_container(bytes(self._body()))
        p = art2board.parse(c)
        self.assertTrue(p["size_ok"])
        self.assertTrue(p["body_ok"])
        self.assertEqual(p["body_start"], 84)
        self.assertIn("bus=ahb,qmi-chip-id=0,qmi-board-id=144", p["names"])

    def test_parse_rejects_bad_magic(self):
        with self.assertRaises(ValueError):
            art2board.parse(b"NOTACONTAINER" + bytes(100))

    def test_parse_rejects_missing_ie(self):
        c = bytearray(build_container(bytes(self._body())))
        struct.pack_into("<II", c, 76, 0, 0)  # 破坏 IE 标记
        with self.assertRaises(ValueError):
            art2board.parse(bytes(c))

    def test_checksum_roundtrip(self):
        body = self._body()
        self.assertEqual(art2board.bdf_xor(bytes(body)), art2board.BDF_CHECKSUM_GOAL)
        body[0x3000] ^= 0xFF                      # 故意改坏
        self.assertNotEqual(art2board.bdf_xor(bytes(body)), art2board.BDF_CHECKSUM_GOAL)
        body = bytearray(body)
        art2board.repair_checksum(body)           # 修回来
        self.assertEqual(art2board.bdf_xor(bytes(body)), art2board.BDF_CHECKSUM_GOAL)

    def test_hybrid_swaps_window_and_repairs_checksum(self):
        base_body = self._body()
        oem_body = bytearray(art2board.BODY_SIZE)
        oem_body[0x2494:0x2758] = b"\xAB" * (0x2758 - 0x2494)   # 造一个明显不同的 OEM 窗口

        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            base = d / "base.bin"
            oem = d / "oem.bin"
            out = d / "out.bin"
            base.write_bytes(build_container(bytes(base_body)))
            oem.write_bytes(bytes(oem_body))          # 裸 body 也应能读

            rc = art2board.cmd_hybrid(
                argparse.Namespace(
                    base=str(base), oem=str(oem), out=str(out),
                    start=0x2494, end=0x2758,
                )
            )
            self.assertEqual(rc, 0)

            p = art2board.parse(out.read_bytes())
            self.assertTrue(p["size_ok"])
            self.assertEqual(art2board.bdf_xor(p["body"]), art2board.BDF_CHECKSUM_GOAL)
            self.assertEqual(p["body"][0x2494:0x2758], b"\xAB" * (0x2758 - 0x2494))
            # 窗口之外必须与 donor 一致
            self.assertEqual(p["body"][0x3000:0x3010], base_body[0x3000:0x3010])

    def test_hybrid_rejects_bad_window(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            base = d / "b.bin"
            oem = d / "o.bin"
            base.write_bytes(build_container(bytes(self._body())))
            oem.write_bytes(bytes(art2board.BODY_SIZE))
            rc = art2board.cmd_hybrid(
                argparse.Namespace(base=str(base), oem=str(oem), out=str(d / "o2.bin"),
                                   start=0x5000, end=0x1000)
            )
            self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
