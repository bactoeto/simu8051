import unittest

from simu8051.core import Core


def make(code):
    c = Core()
    c.code[: len(code)] = bytes(code)
    return c


class MoreInterrupts(unittest.TestCase):
    def test_timer0_interrupt_vector(self):
        c = make([0x00] * 8)
        c.set_sfr("IE", 0x82)  # EA + ET0
        c.set_sfr("TMOD", 0x01)
        c.set_sfr("TH0", 0xFF)
        c.set_sfr("TL0", 0xFF)
        c.set_sfr("TCON", 0x10)
        c.tick_cycle()  # IE write inhibits this boundary
        c.tick_cycle()
        assert c._vector is not None
        self.assertEqual(c._vector["target"], 0x000B)

    def test_timer1_interrupt_vector(self):
        c = make([0x00] * 8)
        c.set_sfr("IE", 0x88)  # EA + ET1
        c.set_sfr("TMOD", 0x10)
        c.set_sfr("TH1", 0xFF)
        c.set_sfr("TL1", 0xFF)
        c.set_sfr("TCON", 0x40)
        c.tick_cycle()  # IE write inhibits this boundary
        c.tick_cycle()
        assert c._vector is not None
        self.assertEqual(c._vector["target"], 0x001B)

    def test_level_triggered_reissues(self):
        c = make([0x00] * 16)
        c.set_sfr("IE", 0x81)  # EA + EX0
        c.set_sfr("TCON", 0x00)  # IT0 level
        c.drive_pin(3, 2, 0)
        c.tick_cycle()  # IE write inhibits this boundary
        c.tick_cycle()
        assert c._vector is not None
        self.assertEqual(c._vector["target"], 0x0003)
        self.assertEqual((c.get_sfr("TCON") >> 1) & 1, 1)  # flag not cleared
        # Finish vector; the pin is still low, so the request remains.
        while c._vector is not None:
            c.tick_cycle()
        self.assertIn(c._active_prio, ([0], [0, 0]))

    def test_ie_write_inhibits_one_boundary(self):
        c = make([0x00] * 8)
        c.set_sfr("TCON", 0x01)
        c.drive_pin(3, 2, 1)
        c.tick_cycle()
        c.drive_pin(3, 2, 0)
        c.tick_cycle()  # IE0 pending, but EA=0
        self.assertIsNone(c._vector)
        c.set_sfr("IE", 0x81)  # write IE: this boundary must not accept
        c.tick_cycle()
        self.assertIsNone(c._vector)
        c.tick_cycle()
        self.assertIsNotNone(c._vector)

    def test_external_interrupt_latency(self):
        c = make([0x00] * 8)
        c.set_sfr("IE", 0x81)
        c.set_sfr("TCON", 0x01)  # IT0 edge
        c.drive_pin(3, 2, 1)
        c.tick_cycle()  # clear the IE-write inhibit
        c.drive_pin(3, 2, 0)
        c.tick_cycle()  # edge sampled, request accepted at this boundary
        self.assertIsNotNone(c._vector)
        assert c._vector is not None
        self.assertEqual(c._vector["target"], 0x0003)
        self.assertEqual((c.get_sfr("TCON") >> 1) & 1, 0)  # edge flag cleared
        c.tick_cycle()  # vector phase 0
        c.tick_cycle()  # vector phase 1
        self.assertEqual(c.pc, 0x0003)
        c.tick_cycle()  # first ISR instruction fetched
        self.assertEqual(c.instruction_pc, 0x0003)

    def test_serial_bits_do_not_request(self):
        c = make([0x00] * 8)
        c.set_sfr("IE", 0x90)  # EA + ES
        c.set_sfr("SCON", 0x03)  # RI + TI
        c.tick_cycle()
        self.assertIsNone(c._vector)


class TimerMode3(unittest.TestCase):
    def test_th0_uses_tr1_and_sets_tf1(self):
        c = make([0x00])
        c.set_sfr("TMOD", 0x03)  # T0 mode 3, T1 mode 0
        c.set_sfr("TH0", 0xFF)
        c.set_sfr("TCON", 0x40)  # TR1
        c.tick_cycle()
        self.assertEqual((c.get_sfr("TCON") >> 7) & 1, 1)
        self.assertEqual(c.get_sfr("TH0"), 0)

    def test_t1_mode2_reload(self):
        c = make([0x00])
        c.set_sfr("TMOD", 0x20)  # T1 mode 2
        c.set_sfr("TH1", 0xFE)
        c.set_sfr("TL1", 0xFF)
        c.set_sfr("TCON", 0x40)  # TR1
        c.tick_cycle()
        self.assertEqual(c.get_sfr("TL1"), 0xFE)
        self.assertEqual((c.get_sfr("TCON") >> 7) & 1, 1)


if __name__ == "__main__":
    unittest.main()
