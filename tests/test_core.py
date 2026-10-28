import unittest

from simu8051.core import Core, Trap
from simu8051.isa_table import OPCODES


def make(code, size=0x100):
    c = Core()
    c.code[: len(code)] = bytes(code)
    return c


class CoreBasics(unittest.TestCase):
    def test_reset_state(self):
        c = Core()
        self.assertEqual(c.pc, 0)
        self.assertEqual(c.get_sfr("SP"), 0x07)
        self.assertEqual(c.get_sfr("P0"), 0xFF)
        self.assertEqual(c.get_reg("DPTR"), 0)
        self.assertEqual(c.cycle, 0)

    def test_nop_cycle(self):
        c = make([0x00])
        r = c.execute_instruction()
        self.assertEqual(r["cycles"], 1)
        self.assertEqual(c.pc, 1)

    def test_all_opcodes_execute(self):
        for op in range(256):
            if op == 0xA5:
                c = make([0xA5])
                with self.assertRaises(Trap):
                    c.tick_cycle()
                continue
            entry = OPCODES[op]
            c = make([op])
            for _ in range(entry["cycles"]):
                c.tick_cycle()
            self.assertIsNone(c._op, f"opcode 0x{op:02X} did not finish")

    def test_add_flags(self):
        c = make([0x74, 0x7F, 0x24, 0x01])
        c.execute_instruction()
        c.execute_instruction()
        self.assertEqual(c.get_reg("A"), 0x80)
        self.assertEqual(c._get_flag("C"), 0)
        self.assertEqual(c._get_flag("AC"), 1)
        self.assertEqual(c._get_flag("OV"), 1)

    def test_addc_carry(self):
        c = make([0x74, 0x01, 0xD3, 0x34, 0x01])  # MOV A,#1; SETB C; ADDC A,#1
        for _ in range(3):
            c.execute_instruction()
        self.assertEqual(c.get_reg("A"), 3)
        self.assertEqual(c._get_flag("C"), 0)

    def test_subb_borrow(self):
        c = make([0x74, 0x00, 0x94, 0x01])  # MOV A,#0; SUBB A,#1
        c.execute_instruction()
        c.execute_instruction()
        self.assertEqual(c.get_reg("A"), 0xFF)
        self.assertEqual(c._get_flag("C"), 1)

    def test_da(self):
        c = make([0x74, 0x9A, 0xD4])
        c.execute_instruction()
        c.execute_instruction()
        self.assertEqual(c.get_reg("A"), 0x00)
        self.assertEqual(c._get_flag("C"), 1)

    def test_mul_div(self):
        c = make([0x74, 0x50, 0x75, 0xF0, 0xA0, 0xA4])  # A=0x50, B=0xA0, MUL
        c.execute_instruction()
        c.execute_instruction()
        c.execute_instruction()
        self.assertEqual(c.get_reg("A"), 0x00)
        self.assertEqual(c.get_reg("B"), 0x32)
        self.assertEqual(c._get_flag("OV"), 1)

    def test_mov_direct_direct_order(self):
        # 75 20 55 : MOV 20h,#55h ; 85 20 30 : MOV 30h,20h
        c = make([0x75, 0x20, 0x55, 0x85, 0x20, 0x30])
        c.execute_instruction()
        c.execute_instruction()
        self.assertEqual(c.read_direct(0x30), 0x55)

    def test_stack_call_ret(self):
        # LCALL 0x0006; at 6: RET
        c = make([0x12, 0x00, 0x06, 0x00, 0x00, 0x00, 0x22])
        c.execute_instruction()
        self.assertEqual(c.pc, 6)
        self.assertEqual(c.get_sfr("SP"), 9)
        c.execute_instruction()
        self.assertEqual(c.pc, 3)
        self.assertEqual(c.get_sfr("SP"), 7)

    def test_push_pop(self):
        c = make([0x74, 0x5A, 0xC0, 0xE0, 0x74, 0x00, 0xD0, 0xE0])
        for _ in range(4):
            c.execute_instruction()
        self.assertEqual(c.get_reg("A"), 0x5A)
        self.assertEqual(c.get_sfr("SP"), 7)

    def test_bits(self):
        c = make([0xD2, 0x20, 0xA2, 0x20, 0xB2, 0x20, 0xC2, 0x20])  # SETB 20h; MOV C,20h; CPL 20h; CLR 20h
        for _ in range(4):
            c.execute_instruction()
        self.assertEqual(c._get_flag("C"), 1)
        self.assertEqual(c.read_bit(0x20), 0)

    def test_16bit_register_api(self):
        c = Core()
        c.set_reg("DPTR", 0x1234)
        self.assertEqual(c.get_reg("DPTR"), 0x1234)
        c.set_reg("PC", 0x4567)
        self.assertEqual(c.pc, 0x4567)

    def test_movx_movc(self):
        c = make([0x90, 0x01, 0x00, 0xE0, 0x93])
        c.xdata[0x0100] = 0x42
        c.execute_instruction()  # MOV DPTR,#0100h
        c.execute_instruction()  # MOVX A,@DPTR
        self.assertEqual(c.get_reg("A"), 0x42)
        c.code[0x0142] = 0x99
        c.execute_instruction()  # MOVC A,@A+DPTR
        self.assertEqual(c.get_reg("A"), 0x99)

    def test_port_latch_vs_pin(self):
        c = make([0x75, 0x90, 0x0F, 0xE5, 0x90, 0x05, 0x90])
        c.execute_instruction()  # MOV P1,#0F
        c.drive_pin(1, 0, 0)     # external pull bit0 low
        c.execute_instruction()  # MOV A,P1 reads pin
        self.assertEqual(c.get_reg("A"), 0x0E)
        c.execute_instruction()  # INC P1 reads latch (0F -> 10)
        self.assertEqual(c.get_sfr("P1"), 0x10)


class TimersInterrupts(unittest.TestCase):
    def test_timer0_mode1_overflow(self):
        c = make([0x00])
        c.set_sfr("TMOD", 0x01)
        c.set_sfr("TH0", 0xFF)
        c.set_sfr("TL0", 0xFF)
        c.set_sfr("TCON", 0x10)  # TR0
        c.tick_cycle()
        self.assertEqual((c.get_sfr("TCON") >> 5) & 1, 1)
        self.assertEqual(c.get_sfr("TH0"), 0)
        self.assertEqual(c.get_sfr("TL0"), 0)

    def test_timer0_mode2_reload(self):
        c = make([0x00])
        c.set_sfr("TMOD", 0x02)
        c.set_sfr("TH0", 0xFE)
        c.set_sfr("TL0", 0xFF)
        c.set_sfr("TCON", 0x10)
        c.tick_cycle()
        self.assertEqual(c.get_sfr("TL0"), 0xFE)
        self.assertEqual((c.get_sfr("TCON") >> 5) & 1, 1)

    def test_external_interrupt_vector(self):
        c = make([0x00] * 8)
        c.set_sfr("IE", 0x81)     # EA + EX0
        c.set_sfr("TCON", 0x01)   # IT0 edge
        c.drive_pin(3, 2, 1)
        c.tick_cycle()
        c.drive_pin(3, 2, 0)
        c.tick_cycle()
        self.assertIsNotNone(c._vector)
        c.tick_cycle()
        c.tick_cycle()
        self.assertEqual(c.pc, 0x0003)
        self.assertEqual(c._active_prio, [0])


if __name__ == "__main__":
    unittest.main()
