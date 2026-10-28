import tempfile
import unittest
from pathlib import Path

from simu8051 import hex as hx


ROOT = Path(__file__).resolve().parent.parent


class HexTests(unittest.TestCase):
    def test_load_as31_fixture(self):
        img = hx.load_hex(ROOT / "tests" / "fixtures" / "sub1.hex")
        self.assertEqual(img.min_addr, 0)
        assert img.max_addr is not None
        self.assertGreaterEqual(img.max_addr, 0x40)
        inst = hx.decode_at(img, 0)
        self.assertEqual(inst.form, "LJMP_addr16")
        self.assertEqual(inst.size, 3)

    def test_reserved_opcode(self):
        img = hx.Image()
        img.code[0] = 0xA5
        inst = hx.decode_at(img, 0)
        self.assertTrue(inst.reserved)

    def test_checksum_error(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "bad.hex"
            path.write_text(":0100000001FF\n:00000001FF\n")
            with self.assertRaises(hx.HexError):
                hx.load_hex(path)

    def test_missing_eof(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "bad.hex"
            path.write_text(":0100000001FE\n")
            with self.assertRaises(hx.HexError):
                hx.load_hex(path)

    def test_extended_segment_address(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "ext.hex"
            # segment 0x0100 -> base 0x1000
            path.write_text(":020000020100FB\n:0100000042BD\n:00000001FF\n")
            img = hx.load_hex(path)
            self.assertEqual(img.code[0x1000], 0x42)

    def test_disassemble_known(self):
        img = hx.Image()
        img.code[:] = bytes([0x00, 0x04, 0x74, 0x12])
        insts = hx.disassemble(img, 0, 4)
        self.assertEqual([i.form for i in insts], ["NOP", "INC_A", "MOV_A_IMM_data"])


if __name__ == "__main__":
    unittest.main()
