import unittest

from simu8051.core import Core


def make(code):
    c = Core()
    c.code[: len(code)] = bytes(code)
    return c


class InterruptPriority(unittest.TestCase):
    def test_natural_order_same_level(self):
        c = make([0x00] * 8)
        c.set_sfr("IE", 0x83)  # EA + EX0 + ET0
        c.set_sfr("TCON", 0x11)  # IT0 edge, TR0
        c.set_sfr("TH0", 0xFF)
        c.set_sfr("TL0", 0xFF)
        c.drive_pin(3, 2, 1)
        c.tick_cycle()
        c.drive_pin(3, 2, 0)
        c.tick_cycle()
        # IE0 and TF0 are both pending, same priority: IE0 wins naturally.
        self.assertEqual(c._vector["target"], 0x0003)
        self.assertEqual(c._active_prio, [0])

    def test_higher_priority_preempts(self):
        c = make([0x00] * 16)
        c.set_sfr("IE", 0x83)  # EA + EX0 + ET0
        c.set_sfr("IP", 0x00)  # both low
        c.set_sfr("TCON", 0x11)  # IT0 edge, TR0
        c.drive_pin(3, 2, 1)
        c.tick_cycle()
        c.drive_pin(3, 2, 0)
        c.tick_cycle()
        self.assertEqual(c._active_prio, [0])
        # Finish the vector and execute the first ISR instruction.
        while c._vector is not None:
            c.tick_cycle()
        c.tick_cycle()
        # Raise Timer 0 to high priority and request it.
        c.set_sfr("IP", 0x02)  # PT0 high
        c.tick_cycle()  # writing IP inhibits this boundary
        c.set_sfr("TH0", 0xFF)
        c.set_sfr("TL0", 0xFF)
        c.tick_cycle()
        self.assertEqual(c._vector["target"], 0x000B)
        self.assertEqual(c._active_prio, [0, 1])

    def test_reti_restores_priority(self):
        c = make([0x32])  # RETI at 0
        c.set_sfr("SP", 0x07)
        c._active_prio = [0]
        c.write_direct(8, 0x00)
        c.write_direct(9, 0x00)
        c.set_sfr("SP", 0x09)
        c.execute_instruction()
        self.assertEqual(c._active_prio, [])
        # The inhibit was consumed at the boundary immediately after RETI.
        self.assertFalse(c._irq_inhibit)


class IdleGate(unittest.TestCase):
    def test_idle_wakes_on_interrupt(self):
        c = make([0x00] * 8)
        c.set_sfr("IE", 0x81)
        c.set_sfr("TCON", 0x01)  # IT0 edge
        c.set_sfr("PCON", 0x01)  # IDL
        c.drive_pin(3, 2, 1)
        c.tick_cycle()
        c.drive_pin(3, 2, 0)
        c.tick_cycle()
        self.assertIn(c._vector["target"], (0x0003,))
        self.assertEqual(c.get_sfr("PCON") & 1, 0)

    def test_gate_blocks_timer(self):
        c = make([0x00])
        c.set_sfr("TMOD", 0x08)  # GATE0
        c.set_sfr("TCON", 0x10)  # TR0
        c.set_sfr("TL0", 0x00)
        c.drive_pin(3, 2, 0)     # INT0 low
        c.tick_cycle()
        self.assertEqual(c.get_sfr("TL0"), 0)

    def test_gate_allows_timer_with_int_high(self):
        c = make([0x00])
        c.set_sfr("TMOD", 0x08)
        c.set_sfr("TCON", 0x10)
        c.set_sfr("TL0", 0x00)
        c.drive_pin(3, 2, 1)
        c.tick_cycle()
        self.assertEqual(c.get_sfr("TL0"), 1)


if __name__ == "__main__":
    unittest.main()
