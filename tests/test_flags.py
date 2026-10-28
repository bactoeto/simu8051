import json
import unittest
from pathlib import Path

from simu8051.core import Core

ROOT = Path(__file__).resolve().parent.parent


def run(code):
    c = Core()
    c.code[: len(code)] = bytes(code)
    while c.pc < len(code):
        c.execute_instruction()
    return c


class ArithmeticFlags(unittest.TestCase):
    def test_add8_exhaustive(self):
        for a in range(256):
            for b in range(256):
                for carry in (0, 1):
                    result, c, ac, ov = Core._add8(a, b, carry)
                    total = a + b + carry
                    self.assertEqual(result, total & 0xFF)
                    self.assertEqual(c, 1 if total > 0xFF else 0)
                    self.assertEqual(ac, 1 if (a & 0xF) + (b & 0xF) + carry > 0xF else 0)
                    self.assertEqual(ov, 1 if ((a ^ result) & (b ^ result) & 0x80) else 0)

    def test_sub8_exhaustive(self):
        for a in range(256):
            for b in range(256):
                for borrow in (0, 1):
                    result, c, ac, ov = Core._sub8(a, b, borrow)
                    total = a - b - borrow
                    self.assertEqual(result, total & 0xFF)
                    self.assertEqual(c, 1 if total < 0 else 0)
                    self.assertEqual(ac, 1 if (a & 0xF) - (b & 0xF) - borrow < 0 else 0)
                    self.assertEqual(ov, 1 if ((a ^ b) & (a ^ result) & 0x80) else 0)

    def test_guide_add_example(self):
        c = run([0x74, 0xC3, 0x78, 0xAA, 0x28])
        self.assertEqual((c.get_reg("A"), c._get_flag("C"), c._get_flag("AC"), c._get_flag("OV")), (0x6D, 1, 0, 1))

    def test_guide_addc_example(self):
        c = run([0x74, 0xC3, 0x78, 0xAA, 0xD3, 0x38])
        self.assertEqual((c.get_reg("A"), c._get_flag("C"), c._get_flag("AC"), c._get_flag("OV")), (0x6E, 1, 0, 1))

    def test_guide_subb_example(self):
        c = run([0x74, 0xC9, 0x7A, 0x54, 0xD3, 0x9A])
        self.assertEqual((c.get_reg("A"), c._get_flag("C"), c._get_flag("AC"), c._get_flag("OV")), (0x74, 0, 0, 1))

    def test_guide_da_example(self):
        c = run([0x74, 0x56, 0x7B, 0x67, 0xD3, 0x3B, 0xD4])
        self.assertEqual((c.get_reg("A"), c._get_flag("C")), (0x24, 1))

    def test_da_uses_ac(self):
        c = run([0x74, 0x0A, 0xD2, 0xD6, 0xD4])  # MOV A,#0A; SETB AC; DA
        self.assertEqual(c.get_reg("A"), 0x10)

    def test_mul_flags(self):
        c = run([0x74, 0x50, 0x75, 0xF0, 0xA0, 0xA4])
        self.assertEqual((c.get_reg("A"), c.get_reg("B")), (0, 0x32))
        self.assertEqual(c._get_flag("C"), 0)
        self.assertEqual(c._get_flag("OV"), 1)
        self.assertEqual(c._get_flag("AC"), 0)

    def test_div_flags(self):
        c = run([0x74, 0xFB, 0x75, 0xF0, 0x12, 0x84])
        self.assertEqual((c.get_reg("A"), c.get_reg("B")), (0x0D, 0x11))
        self.assertEqual(c._get_flag("C"), 0)
        self.assertEqual(c._get_flag("OV"), 0)

    def test_div_by_zero(self):
        c = run([0x74, 0xFB, 0x75, 0xF0, 0x00, 0x84])
        self.assertEqual(c._get_flag("C"), 0)
        self.assertEqual(c._get_flag("OV"), 1)
        self.assertEqual(c.get_reg("A"), 0xFB)  # undefined in guide; implementation leaves it
        self.assertEqual(c.get_reg("B"), 0x00)


class BitFlags(unittest.TestCase):
    def test_rotate_flags(self):
        self.assertEqual(run([0x74, 0xC5, 0xC3, 0x33]).get_reg("A"), 0x8A)
        self.assertEqual(run([0x74, 0xC5, 0xC3, 0x33])._get_flag("C"), 1)
        self.assertEqual(run([0x74, 0xC5, 0xC3, 0x13]).get_reg("A"), 0x62)
        self.assertEqual(run([0x74, 0xC5, 0xC3, 0x13])._get_flag("C"), 1)
        self.assertEqual(run([0x74, 0xC5, 0xD3, 0x23]).get_reg("A"), 0x8B)
        self.assertEqual(run([0x74, 0xC5, 0xD3, 0x23])._get_flag("C"), 1)
        self.assertEqual(run([0x74, 0xC5, 0xD3, 0x03]).get_reg("A"), 0xE2)
        self.assertEqual(run([0x74, 0xC5, 0xD3, 0x03])._get_flag("C"), 1)

    def test_bit_logic(self):
        c = run([0xD3, 0xA2, 0x20, 0xB2, 0x20])  # SETB C; MOV C,20; ORL C,20
        self.assertEqual(c._get_flag("C"), 0)

    def test_clr_setb_cpl_c(self):
        self.assertEqual(run([0xD3, 0xC3])._get_flag("C"), 0)
        self.assertEqual(run([0xC3, 0xD3])._get_flag("C"), 1)
        self.assertEqual(run([0xD3, 0xB3])._get_flag("C"), 0)

    def test_psw_direct_write(self):
        c = run([0x74, 0x01, 0x75, 0xD0, 0x80])  # A=1 (odd), MOV PSW,#80
        self.assertTrue(c._get_flag("C"))
        self.assertEqual(c.get_sfr("PSW") & 1, 1)  # parity of A=1

    def test_parity_is_maintained_by_hardware(self):
        # Guide p.9: P is set/cleared by hardware each instruction cycle, so a
        # direct PSW write cannot override it. ucsim keeps the written bit.
        self.assertEqual(run([0x74, 0x01, 0x75, 0xD0, 0x00]).get_sfr("PSW"), 0x01)
        self.assertEqual(run([0xD3, 0x05, 0xD0]).get_sfr("PSW"), 0x80)  # SETB C; INC PSW
        self.assertEqual(
            run([0x74, 0x01, 0x75, 0x81, 0x07, 0x75, 0x07, 0x00, 0xD0]).get_sfr("PSW"),
            0x01,  # POP PSW of 0x00 still leaves P=1 for A=1
        )


class JsonFlagTable(unittest.TestCase):
    def test_flag_table_shape(self):
        data = json.loads((ROOT / "data" / "isa.json").read_text())
        all_flags = {i["syntax"]: i["flags"] for i in data["instructions"]}
        affected = {
            syntax: flags for syntax, flags in all_flags.items()
            if any(v != "unchanged" for v in flags.values())
        }
        for syntax in (
            "ADD A,Rn", "ADDC A,Rn", "SUBB A,Rn", "MUL AB", "DIV AB", "DA A",
            "RLC A", "RRC A", "CLR C", "SETB C", "CPL C", "CJNE A,#data,rel",
            "ANL C,bit", "ANL C,/bit", "ORL C,bit", "ORL C,/bit", "MOV C,bit",
        ):
            self.assertIn(syntax, affected)
        self.assertEqual(all_flags["RL A"], {"C": "unchanged", "AC": "unchanged", "OV": "unchanged"})
        self.assertEqual(affected["MUL AB"]["C"], "cleared")
        self.assertEqual(affected["CLR C"]["C"], "cleared")
        self.assertEqual(affected["SETB C"]["C"], "set")


if __name__ == "__main__":
    unittest.main()
