import unittest

from simu8051.core import Core


def make(code):
    c = Core()
    c.code[: len(code)] = bytes(code)
    return c


def exec_one(code, setup=None):
    c = make(code)
    if setup:
        setup(c)
    c.execute_instruction()
    return c


class MoveForms(unittest.TestCase):
    def test_mov_forms(self):
        cases = [
            # code, setup, check
            ([0xE8], lambda c: c.set_reg("R0", 0x11), lambda c: c.get_reg("A") == 0x11),
            ([0xE5, 0x20], lambda c: c.write_direct(0x20, 0x22), lambda c: c.get_reg("A") == 0x22),
            ([0xE6], lambda c: (c.set_reg("R0", 0x20), c.write_direct(0x20, 0x33)), lambda c: c.get_reg("A") == 0x33),
            ([0x74, 0x44], None, lambda c: c.get_reg("A") == 0x44),
            ([0xF8], lambda c: c.set_reg("A", 0x55), lambda c: c.get_reg("R0") == 0x55),
            ([0xA8, 0x20], lambda c: c.write_direct(0x20, 0x66), lambda c: c.get_reg("R0") == 0x66),
            ([0x78, 0x77], None, lambda c: c.get_reg("R0") == 0x77),
            ([0xF5, 0x20], lambda c: c.set_reg("A", 0x88), lambda c: c.read_direct(0x20) == 0x88),
            ([0x88, 0x20], lambda c: c.set_reg("R0", 0x99), lambda c: c.read_direct(0x20) == 0x99),
            ([0x85, 0x20, 0x21], lambda c: c.write_direct(0x20, 0xAA), lambda c: c.read_direct(0x21) == 0xAA),
            ([0x86, 0x21], lambda c: (c.set_reg("R0", 0x20), c.write_direct(0x20, 0xBB)), lambda c: c.read_direct(0x21) == 0xBB),
            ([0x75, 0x20, 0xCC], None, lambda c: c.read_direct(0x20) == 0xCC),
            ([0xF6], lambda c: (c.set_reg("R0", 0x20), c.set_reg("A", 0xDD)), lambda c: c.read_direct(0x20) == 0xDD),
            ([0xA6, 0x21], lambda c: (c.set_reg("R0", 0x20), c.write_direct(0x21, 0xEE)), lambda c: c.read_direct(0x20) == 0xEE),
            ([0x76, 0xFF], lambda c: c.set_reg("R0", 0x20), lambda c: c.read_direct(0x20) == 0xFF),
            ([0xA2, 0x20], lambda c: c.write_bit(0x20, 1), lambda c: c._get_flag("C") == 1),
            ([0x92, 0x20], lambda c: c._set_flag("C", 1), lambda c: c.read_bit(0x20) == 1),
            ([0x90, 0x12, 0x34], None, lambda c: c.get_reg("DPTR") == 0x1234),
        ]
        for code, setup, check in cases:
            with self.subTest(code=code):
                self.assertTrue(check(exec_one(code, setup)))


class ArithmeticForms(unittest.TestCase):
    def test_add_addc_subb_forms(self):
        # ADD A,Rn / direct / @Ri / #data
        for code, setup, expected in [
            ([0x28], lambda c: (c.set_reg("A", 1), c.set_reg("R0", 2)), 3),
            ([0x25, 0x20], lambda c: (c.set_reg("A", 1), c.write_direct(0x20, 2)), 3),
            ([0x26], lambda c: (c.set_reg("A", 1), c.set_reg("R0", 0x20), c.write_direct(0x20, 2)), 3),
            ([0x24, 0x02], lambda c: c.set_reg("A", 1), 3),
        ]:
            with self.subTest(code=code):
                self.assertEqual(exec_one(code, setup).get_reg("A"), expected)
        for opcode in (0x38, 0x35, 0x36, 0x34):
            code = [opcode] if opcode in (0x38, 0x36) else [opcode, 0x20] if opcode == 0x35 else [opcode, 0x02]
            c = make(code)
            c.set_reg("A", 1)
            c._set_flag("C", 1)
            if opcode == 0x38:
                c.set_reg("R0", 2)
            elif opcode == 0x35:
                c.write_direct(0x20, 2)
            elif opcode == 0x36:
                c.set_reg("R0", 0x20)
                c.write_direct(0x20, 2)
            c.execute_instruction()
            self.assertEqual(c.get_reg("A"), 4)
        for opcode in (0x98, 0x95, 0x96, 0x94):
            code = [opcode] if opcode in (0x98, 0x96) else [opcode, 0x20] if opcode == 0x95 else [opcode, 0x02]
            c = make(code)
            c.set_reg("A", 5)
            c._set_flag("C", 0)
            if opcode == 0x98:
                c.set_reg("R0", 2)
            elif opcode == 0x95:
                c.write_direct(0x20, 2)
            elif opcode == 0x96:
                c.set_reg("R0", 0x20)
                c.write_direct(0x20, 2)
            c.execute_instruction()
            self.assertEqual(c.get_reg("A"), 3)
            self.assertEqual(c._get_flag("C"), 0)

    def test_inc_dec_forms(self):
        for code, setup, expected in [
            ([0x04], lambda c: c.set_reg("A", 1), ("A", 2)),
            ([0x08], lambda c: c.set_reg("R0", 1), ("R0", 2)),
            ([0x05, 0x20], lambda c: c.write_direct(0x20, 1), ("direct", 2)),
            ([0x06], lambda c: (c.set_reg("R0", 0x20), c.write_direct(0x20, 1)), ("indirect", 2)),
        ]:
            c = exec_one(code, setup)
            if expected[0] == "A":
                self.assertEqual(c.get_reg("A"), expected[1])
            elif expected[0] == "R0":
                self.assertEqual(c.get_reg("R0"), expected[1])
            elif expected[0] == "direct":
                self.assertEqual(c.read_direct(0x20), expected[1])
            else:
                self.assertEqual(c.read_direct(0x20), expected[1])
        c = exec_one([0xA3], lambda c: c.set_reg("DPTR", 0x12FF))
        self.assertEqual(c.get_reg("DPTR"), 0x1300)
        for code, setup, check in [
            ([0x14], lambda c: c.set_reg("A", 2), lambda c: c.get_reg("A") == 1),
            ([0x18], lambda c: c.set_reg("R0", 2), lambda c: c.get_reg("R0") == 1),
            ([0x15, 0x20], lambda c: c.write_direct(0x20, 2), lambda c: c.read_direct(0x20) == 1),
            ([0x16], lambda c: (c.set_reg("R0", 0x20), c.write_direct(0x20, 2)), lambda c: c.read_direct(0x20) == 1),
        ]:
            self.assertTrue(check(exec_one(code, setup)))


class LogicForms(unittest.TestCase):
    def test_anl_orl_xrl_forms(self):
        # (code, setup, expected A/mem)
        cases = [
            ([0x58], lambda c: (c.set_reg("A", 0xF0), c.set_reg("R0", 0x0F)), 0x00),
            ([0x55, 0x20], lambda c: (c.set_reg("A", 0xF0), c.write_direct(0x20, 0x3C)), 0x30),
            ([0x56], lambda c: (c.set_reg("A", 0xF0), c.set_reg("R0", 0x20), c.write_direct(0x20, 0x3C)), 0x30),
            ([0x54, 0x3C], lambda c: c.set_reg("A", 0xF0), 0x30),
        ]
        for code, setup, expected in cases:
            self.assertEqual(exec_one(code, setup).get_reg("A"), expected)
        c = exec_one([0x52, 0x20], lambda c: (c.set_reg("A", 0x0F), c.write_direct(0x20, 0xF0)))
        self.assertEqual(c.read_direct(0x20), 0x00)
        c = exec_one([0x53, 0x20, 0x0F], lambda c: c.write_direct(0x20, 0xF0))
        self.assertEqual(c.read_direct(0x20), 0x00)
        c = exec_one([0x82, 0x20], lambda c: (c._set_flag("C", 1), c.write_bit(0x20, 1)))
        self.assertEqual(c._get_flag("C"), 1)
        c = exec_one([0xB0, 0x20], lambda c: (c._set_flag("C", 1), c.write_bit(0x20, 0)))
        self.assertEqual(c._get_flag("C"), 1)

        c = exec_one([0x48], lambda c: (c.set_reg("A", 0xF0), c.set_reg("R0", 0x0F)))
        self.assertEqual(c.get_reg("A"), 0xFF)
        c = exec_one([0x42, 0x20], lambda c: (c.set_reg("A", 0x0F), c.write_direct(0x20, 0xF0)))
        self.assertEqual(c.read_direct(0x20), 0xFF)
        c = exec_one([0x43, 0x20, 0x0F], lambda c: c.write_direct(0x20, 0xF0))
        self.assertEqual(c.read_direct(0x20), 0xFF)
        c = exec_one([0x72, 0x20], lambda c: (c._set_flag("C", 0), c.write_bit(0x20, 1)))
        self.assertEqual(c._get_flag("C"), 1)
        c = exec_one([0xA0, 0x20], lambda c: (c._set_flag("C", 0), c.write_bit(0x20, 0)))
        self.assertEqual(c._get_flag("C"), 1)

        c = exec_one([0x68], lambda c: (c.set_reg("A", 0xF0), c.set_reg("R0", 0x0F)))
        self.assertEqual(c.get_reg("A"), 0xFF)
        c = exec_one([0x62, 0x20], lambda c: (c.set_reg("A", 0x0F), c.write_direct(0x20, 0xF0)))
        self.assertEqual(c.read_direct(0x20), 0xFF)
        c = exec_one([0x63, 0x20, 0x0F], lambda c: c.write_direct(0x20, 0xF0))
        self.assertEqual(c.read_direct(0x20), 0xFF)

    def test_swap(self):
        c = exec_one([0xC4], lambda c: c.set_reg("A", 0xC5))
        self.assertEqual(c.get_reg("A"), 0x5C)
        self.assertEqual(c._get_flag("C"), 0)

    def test_bit_clr_cpl_setb(self):
        c = exec_one([0xE4], lambda c: c.set_reg("A", 0x55))
        self.assertEqual(c.get_reg("A"), 0)
        c = exec_one([0xF4], lambda c: c.set_reg("A", 0x55))
        self.assertEqual(c.get_reg("A"), 0xAA)
        c = exec_one([0xC2, 0x20], lambda c: c.write_bit(0x20, 1))
        self.assertEqual(c.read_bit(0x20), 0)
        c = exec_one([0xD2, 0x20], lambda c: c.write_bit(0x20, 0))
        self.assertEqual(c.read_bit(0x20), 1)
        c = exec_one([0xB2, 0x20], lambda c: c.write_bit(0x20, 1))
        self.assertEqual(c.read_bit(0x20), 0)


class ControlForms(unittest.TestCase):
    def test_ljmp_acall_lcall_ajmp(self):
        c = exec_one([0x02, 0x12, 0x34])
        self.assertEqual(c.pc, 0x1234)
        c = make([0x00] * 0x200)
        c.code[0x0100] = 0x31
        c.code[0x0101] = 0x23
        c.pc = 0x0100
        c.execute_instruction()
        self.assertEqual(c.pc, 0x0123)
        self.assertEqual(c.get_sfr("SP"), 9)
        c = exec_one([0x12, 0x12, 0x34])
        self.assertEqual(c.pc, 0x1234)
        self.assertEqual(c.get_sfr("SP"), 9)

    def test_conditional_branches(self):
        for code, setup, target in [
            ([0x40, 0x02], lambda c: c._set_flag("C", 1), 4),
            ([0x50, 0x02], lambda c: c._set_flag("C", 0), 4),
            ([0x60, 0x02], lambda c: c.set_reg("A", 0), 4),
            ([0x70, 0x02], lambda c: c.set_reg("A", 1), 4),
            ([0x20, 0x20, 0x02], lambda c: c.write_bit(0x20, 1), 5),
            ([0x30, 0x20, 0x02], lambda c: c.write_bit(0x20, 0), 5),
            ([0x10, 0x20, 0x02], lambda c: c.write_bit(0x20, 1), 5),
        ]:
            c = exec_one(code, setup)
            self.assertEqual(c.pc, target, code)
        c = exec_one([0x73], lambda c: (c.set_reg("A", 0x10), c.set_reg("DPTR", 0x0200)))
        self.assertEqual(c.pc, 0x0210)

    def test_cjne_djnz_forms(self):
        # CJNE A,direct; A,#; Rn,#; @Ri,#
        cases = [
            ([0xB5, 0x20, 0x02], lambda c: (c.set_reg("A", 1), c.write_direct(0x20, 2)), 1, 5),
            ([0xB4, 0x02, 0x02], lambda c: c.set_reg("A", 1), 1, 5),
            ([0xB8, 0x02, 0x02], lambda c: c.set_reg("R0", 1), 1, 5),
            ([0xB6, 0x02, 0x02], lambda c: (c.set_reg("R0", 0x20), c.write_direct(0x20, 1)), 1, 5),
        ]
        for code, setup, cflag, target in cases:
            c = exec_one(code, setup)
            self.assertEqual(c.pc, target)
            self.assertEqual(c._get_flag("C"), cflag)
        c = exec_one([0xD8, 0x02], lambda c: c.set_reg("R0", 2))
        self.assertEqual(c.get_reg("R0"), 1)
        self.assertEqual(c.pc, 4)
        c = exec_one([0xD5, 0x20, 0x02], lambda c: c.write_direct(0x20, 2))
        self.assertEqual(c.read_direct(0x20), 1)
        self.assertEqual(c.pc, 5)

    def test_ret_reti(self):
        c = make([0x22])
        c.set_sfr("SP", 0x09)
        c.write_direct(8, 0x34)
        c.write_direct(9, 0x12)
        c.execute_instruction()
        self.assertEqual(c.pc, 0x1234)
        self.assertEqual(c.get_sfr("SP"), 7)
        c = make([0x32])
        c.set_sfr("SP", 0x09)
        c.write_direct(8, 0x34)
        c.write_direct(9, 0x12)
        c._active_prio = [0]
        c.execute_instruction()
        self.assertEqual(c.pc, 0x1234)
        self.assertEqual(c._active_prio, [])


class TransferForms(unittest.TestCase):
    def test_xch_forms(self):
        c = exec_one([0xC8], lambda c: (c.set_reg("A", 0x11), c.set_reg("R0", 0x22)))
        self.assertEqual((c.get_reg("A"), c.get_reg("R0")), (0x22, 0x11))
        c = exec_one([0xC5, 0x20], lambda c: (c.set_reg("A", 0x11), c.write_direct(0x20, 0x22)))
        self.assertEqual((c.get_reg("A"), c.read_direct(0x20)), (0x22, 0x11))
        c = exec_one([0xC6], lambda c: (c.set_reg("A", 0x11), c.set_reg("R0", 0x20), c.write_direct(0x20, 0x22)))
        self.assertEqual((c.get_reg("A"), c.read_direct(0x20)), (0x22, 0x11))

    def test_movx_forms(self):
        c = exec_one([0xE2], lambda c: (c.set_reg("R0", 0x34), c.set_sfr("P2", 0x12), c.write_xdata(0x1234, 0x5A)))
        self.assertEqual(c.get_reg("A"), 0x5A)
        c = exec_one([0xE0], lambda c: (c.set_reg("DPTR", 0x1234), c.write_xdata(0x1234, 0x5A)))
        self.assertEqual(c.get_reg("A"), 0x5A)
        c = exec_one([0xF2], lambda c: (c.set_reg("A", 0x5A), c.set_reg("R0", 0x34), c.set_sfr("P2", 0x12)))
        self.assertEqual(c.read_xdata(0x1234), 0x5A)
        c = exec_one([0xF0], lambda c: (c.set_reg("A", 0x5A), c.set_reg("DPTR", 0x1234)))
        self.assertEqual(c.read_xdata(0x1234), 0x5A)

    def test_movc_forms(self):
        c = make([0x93])
        c.set_reg("A", 2)
        c.set_reg("DPTR", 0x0100)
        c.code[0x0102] = 0x99
        c.execute_instruction()
        self.assertEqual(c.get_reg("A"), 0x99)
        c = make([0x00] * 0x20)
        c.code[0x10] = 0x83
        c.pc = 0x10
        c.set_reg("A", 2)
        c.code[0x13] = 0x77
        c.execute_instruction()
        self.assertEqual(c.get_reg("A"), 0x77)

    def test_push_pop(self):
        c = exec_one([0xC0, 0x20], lambda c: c.write_direct(0x20, 0x5A))
        self.assertEqual(c.get_sfr("SP"), 8)
        self.assertEqual(c.read_direct(8), 0x5A)
        c = make([0xD0, 0x20])
        c.set_sfr("SP", 8)
        c.write_direct(8, 0x5A)
        c.execute_instruction()
        self.assertEqual(c.read_direct(0x20), 0x5A)
        self.assertEqual(c.get_sfr("SP"), 7)


if __name__ == "__main__":
    unittest.main()
