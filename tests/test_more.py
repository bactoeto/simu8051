import unittest

from simu8051.core import Core
from simu8051.isa_table import OPCODES


def make(code, size=0x100):
    c = Core()
    c.code[: len(code)] = bytes(code)
    return c


class CycleCounts(unittest.TestCase):
    def test_all_opcode_cycle_counts(self):
        for op in range(256):
            if op == 0xA5:
                continue
            c = make([op])
            c.execute_instruction()
            self.assertEqual(c.cycle, OPCODES[op]["cycles"], f"opcode 0x{op:02X}")


class Branches(unittest.TestCase):
    def test_ajmp_page(self):
        c = make([0x00] * 0x200)
        c.code[0x0100] = 0x21
        c.code[0x0101] = 0x23
        c.pc = 0x0100
        c.execute_instruction()
        self.assertEqual(c.pc, 0x0123)

    def test_sjmp_negative(self):
        c = make([0x00] * 0x200)
        c.code[0x0100] = 0x80
        c.code[0x0101] = 0xFE
        c.pc = 0x0100
        c.execute_instruction()
        self.assertEqual(c.pc, 0x0100)

    def test_cjne(self):
        # MOV A,#5; CJNE A,#5,+2 (not taken); NOP; NOP
        c = make([0x74, 0x05, 0xB4, 0x05, 0x02, 0x00, 0x00])
        c.execute_instruction()
        c.execute_instruction()
        self.assertEqual(c.pc, 5)
        self.assertEqual(c._get_flag("C"), 0)

    def test_djnz(self):
        # MOV R0,#2; DJNZ R0,-2 -> loop
        c = make([0x78, 0x02, 0xD8, 0xFE])
        c.execute_instruction()
        c.execute_instruction()
        self.assertEqual(c.get_reg("R0"), 1)
        self.assertEqual(c.pc, 2)
        c.execute_instruction()
        self.assertEqual(c.get_reg("R0"), 0)
        self.assertEqual(c.pc, 4)

    def test_jbc(self):
        # SETB 20h; JBC 20h,+2; NOP
        c = make([0xD2, 0x20, 0x10, 0x20, 0x00, 0x00])
        c.execute_instruction()
        c.execute_instruction()
        self.assertEqual(c.read_bit(0x20), 0)
        self.assertEqual(c.pc, 5)

    def test_call_stack_order(self):
        # LCALL 0x0005 at 0; RET at 5
        c = make([0x12, 0x00, 0x05, 0x00, 0x00, 0x22])
        c.execute_instruction()
        self.assertEqual(c.read_direct(8), 0x03)
        self.assertEqual(c.read_direct(9), 0x00)
        c.execute_instruction()
        self.assertEqual(c.pc, 3)


class Transfers(unittest.TestCase):
    def test_movc_pc(self):
        # at 0x10: MOVC A,@A+PC ; code[0x13]=0x99
        c = make([0x00] * 0x20)
        c.code[0x10] = 0x83
        c.code[0x13] = 0x99
        c.pc = 0x10
        c.set_reg("A", 2)
        c.execute_instruction()
        self.assertEqual(c.get_reg("A"), 0x99)

    def test_movx_ri_uses_p2_latch(self):
        # MOV P2,#12; MOV R0,#34; MOVX @R0,A
        c = make([0x75, 0xA0, 0x12, 0x78, 0x34, 0xF2])
        c.set_reg("A", 0x5A)
        for _ in range(3):
            c.execute_instruction()
        self.assertEqual(c.xdata[0x1234], 0x5A)

    def test_xchd(self):
        # MOV A,#36; MOV R0,#20; MOV 20,#75; XCHD A,@R0
        c = make([0x74, 0x36, 0x78, 0x20, 0x75, 0x20, 0x75, 0xD6])
        for _ in range(4):
            c.execute_instruction()
        self.assertEqual(c.get_reg("A"), 0x35)
        self.assertEqual(c.read_direct(0x20), 0x76)

    def test_pop_sp(self):
        c = make([0x75, 0x81, 0x10, 0xD0, 0x81])  # MOV SP,#10; POP SP
        c.execute_instruction()
        c.write_indirect(0x10, 0x20)
        c.execute_instruction()
        self.assertEqual(c.get_sfr("SP"), 0x20)


class Timers(unittest.TestCase):
    def test_timer0_mode0_13bit(self):
        c = make([0x00])
        c.set_sfr("TMOD", 0x00)
        c.set_sfr("TH0", 0xFF)
        c.set_sfr("TL0", 0x1F)
        c.set_sfr("TCON", 0x10)
        c.tick_cycle()
        self.assertEqual((c.get_sfr("TCON") >> 5) & 1, 1)

    def test_timer0_mode3_split(self):
        c = make([0x00])
        c.set_sfr("TMOD", 0x03)
        c.set_sfr("TL0", 0xFF)
        c.set_sfr("TH0", 0xFF)
        c.set_sfr("TCON", 0x10)  # TR0
        c.tick_cycle()
        self.assertEqual((c.get_sfr("TCON") >> 5) & 1, 1)
        self.assertEqual(c.get_sfr("TL0"), 0)

    def test_timer0_counter_falling_edge(self):
        c = make([0x00])
        c.set_sfr("TMOD", 0x04)  # T0 counter, mode 0
        c.set_sfr("TCON", 0x10)  # TR0
        c.set_sfr("TL0", 0x00)
        c.drive_pin(3, 4, 1)
        c.tick_cycle()           # establish high
        c.drive_pin(3, 4, 0)
        c.tick_cycle()           # edge recognized
        c.tick_cycle()           # increment on the following cycle
        self.assertEqual(c.get_sfr("TL0"), 1)

    def test_timer1_stopped_in_t0_mode3(self):
        c = make([0x00])
        c.set_sfr("TMOD", 0x13)  # T0 mode3, T1 mode1
        c.set_sfr("TL1", 0xFE)
        c.set_sfr("TCON", 0x40)  # TR1
        c.tick_cycle()
        self.assertEqual(c.get_sfr("TL1"), 0xFE)


if __name__ == "__main__":
    unittest.main()
