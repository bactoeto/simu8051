"""Intel 8051 cycle-level core.

Cycle model: one machine cycle = 12 oscillator periods, no S1-S6 phases.
The external API is intentionally small: tick_cycle(), execute_instruction(),
memory/register access, reset() and pin driving.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .isa_table import OPCODES
from .mem_table import (
    BIT_ADDRESSABLE,
    IE_BITS,
    IP_BITS,
    PORTS,
    SFR,
    SFR_BY_ADDR,
    SFR_RESET,
    VECTORS,
)

CODE_SIZE = 0x10000
PORT_INDEX = {0x80: 0, 0x90: 1, 0xA0: 2, 0xB0: 3}
PORT_ADDRS = tuple(PORT_INDEX)


class Trap(Exception):
    def __init__(self, pc: int, opcode: int, cycle: int):
        self.pc = pc
        self.opcode = opcode
        self.cycle = cycle
        super().__init__(f"reserved opcode 0x{opcode:02X} at 0x{pc:04X}, cycle {cycle}")


@dataclass(frozen=True)
class Event:
    kind: str
    detail: object = None
    cycle: int = 0


class Core:
    def __init__(self, code: Optional[bytes | bytearray] = None):
        self.code = bytearray([0xFF]) * CODE_SIZE
        self.iram = bytearray(128)
        self.sfr = bytearray(128)
        self.xdata = bytearray(CODE_SIZE)
        self.pins = bytearray([0xFF]) * 4
        self.ext_mask = bytearray(4)
        self.ext_value = bytearray(4)
        self.pc = 0
        self.cycle = 0
        self._op = None
        self._opcode = -1
        self._args: tuple[int, ...] = ()
        self._instr_pc = 0
        self._next_pc = 0
        self._remaining = 0
        self._branch_target: Optional[int] = None
        self._vector: Optional[dict] = None
        self._active_prio: list[int] = []
        self._irq_inhibit = False
        self._events: list[Event] = []
        self._int_prev = [1, 1]
        self._timer_prev = [1, 1]
        self._timer_pending = [False, False]
        self._last_event = None
        if code is not None:
            self.code[:] = bytes(code)[:CODE_SIZE]
        self.reset(cold=True)

    # ------------------------------------------------------------------
    # reset / state
    # ------------------------------------------------------------------
    def load_image(self, image) -> None:
        """Copy a hex.Image into code memory."""
        self.code[:] = image.code

    def reset(self, cold: bool = False) -> None:
        """Apply reset values from memory.json. Cold reset clears RAM/XDATA."""
        if cold:
            self.iram[:] = b"\x00" * 128
            self.xdata[:] = b"\x00" * CODE_SIZE
        self.sfr[:] = b"\x00" * 128
        for addr, value in SFR_RESET.items():
            if isinstance(value, str) and value.startswith("0x"):
                self.sfr[addr - 0x80] = int(value, 16)
        self.ext_mask[:] = b"\x00" * 4
        self.ext_value[:] = b"\x00" * 4
        self.pc = 0
        self.cycle = 0
        self._op = None
        self._opcode = -1
        self._args = ()
        self._instr_pc = 0
        self._next_pc = 0
        self._remaining = 0
        self._branch_target = None
        self._vector = None
        self._active_prio.clear()
        self._irq_inhibit = False
        self._events.clear()
        self._timer_prev = [1, 1]
        self._timer_pending = [False, False]
        self._sync_pins()
        self._int_prev = [1, 1]
        self._last_event = None

    @property
    def idle(self) -> bool:
        """True when PCON.IDL is set."""
        return bool(self.read_latch(SFR["PCON"]) & 0x01)

    @property
    def halted(self) -> bool:
        """True when PCON.PD is set."""
        return bool(self.read_latch(SFR["PCON"]) & 0x02)

    @property
    def instruction_pc(self) -> int:
        """PC of the instruction currently being executed."""
        return self._instr_pc

    # ------------------------------------------------------------------
    # memory
    # ------------------------------------------------------------------
    def read_code(self, address: int) -> int:
        """Read a byte from program memory (address is masked to 0xFFFF)."""
        return self.code[address & 0xFFFF]

    def write_code(self, address: int, value: int) -> None:
        """Write a byte to program memory (address is masked to 0xFFFF)."""
        self.code[address & 0xFFFF] = value & 0xFF

    def read_direct(self, address: int) -> int:
        """Read the direct space; port addresses return pin levels."""
        address &= 0xFF
        if address < 0x80:
            return self.iram[address]
        if address in PORT_INDEX:
            return self.pins[PORT_INDEX[address]]
        if address in SFR_BY_ADDR:
            return self.sfr[address - 0x80]
        return 0xFF

    def read_latch(self, address: int) -> int:
        """Read the SFR/RAM latch; port addresses return the latch, not pins."""
        address &= 0xFF
        if address < 0x80:
            return self.iram[address]
        if address in SFR_BY_ADDR:
            return self.sfr[address - 0x80]
        return 0xFF

    def write_direct(self, address: int, value: int) -> None:
        """Write the direct space, applying SFR side effects."""
        address &= 0xFF
        value &= 0xFF
        if address < 0x80:
            self.iram[address] = value
            return
        if address in PORT_INDEX:
            self.sfr[address - 0x80] = value
            return
        if address not in SFR_BY_ADDR:
            self._events.append(Event("unimplemented_sfr_write", (address, value), self.cycle))
            return
        if address in (SFR["IE"], SFR["IP"]):
            self._irq_inhibit = True
        if address == SFR["ACC"]:
            self.sfr[address - 0x80] = value
            self._update_parity()
            return
        if address == SFR["PSW"]:
            # PSW.0 is the hardware-maintained parity flag.
            self.sfr[address - 0x80] = (value & 0xFE) | self._parity(self.read_latch(SFR["ACC"]))
            return
        self.sfr[address - 0x80] = value

    def read_indirect(self, address: int) -> int:
        """Read @Ri space; addresses 0x80-0xFF return 0xFF."""
        address &= 0xFF
        return self.iram[address] if address < 0x80 else 0xFF

    def write_indirect(self, address: int, value: int) -> None:
        """Write @Ri space; addresses 0x80-0xFF are ignored."""
        address &= 0xFF
        if address < 0x80:
            self.iram[address] = value & 0xFF
        else:
            self._events.append(Event("unimplemented_indirect_write", (address, value), self.cycle))

    def _bit_target(self, bit: int) -> tuple[int, int, bool]:
        """Return (byte_address, mask, is_sfr)."""
        bit &= 0xFF
        if bit < 0x80:
            return 0x20 + (bit >> 3), 1 << (bit & 7), False
        return bit & 0xF8, 1 << (bit & 7), True

    def read_bit(self, bit: int, latch: bool = False) -> int:
        """Read a bit; port bits use pin levels unless latch is true."""
        addr, mask, is_sfr = self._bit_target(bit)
        if not is_sfr:
            return 1 if self.iram[addr] & mask else 0
        if addr in PORT_INDEX and not latch:
            value = self.pins[PORT_INDEX[addr]]
        elif addr in SFR_BY_ADDR:
            value = self.sfr[addr - 0x80]
        else:
            value = 0xFF
        return 1 if value & mask else 0

    def write_bit(self, bit: int, value: int) -> None:
        """Write a bit; port bits update the latch only."""
        addr, mask, is_sfr = self._bit_target(bit)
        value = 1 if value else 0
        if not is_sfr:
            if value:
                self.iram[addr] |= mask
            else:
                self.iram[addr] &= ~mask & 0xFF
            return
        if addr in PORT_INDEX:
            latch = self.sfr[addr - 0x80]
            self.sfr[addr - 0x80] = (latch | mask) if value else (latch & ~mask & 0xFF)
            return
        if addr not in SFR_BY_ADDR:
            return
        current = self.sfr[addr - 0x80]
        new = (current | mask) if value else (current & ~mask & 0xFF)
        if addr == SFR["PSW"]:
            new = (new & 0xFE) | self._parity(self.read_latch(SFR["ACC"]))
        self.sfr[addr - 0x80] = new
        if addr == SFR["ACC"]:
            self._update_parity()

    def read_xdata(self, address: int) -> int:
        """Read external data memory (0x0000-0xFFFF)."""
        return self.xdata[address & 0xFFFF]

    def write_xdata(self, address: int, value: int) -> None:
        """Write external data memory (0x0000-0xFFFF)."""
        self.xdata[address & 0xFFFF] = value & 0xFF

    def get_sfr(self, name_or_addr: str | int) -> int:
        """Read an SFR latch by name or address."""
        addr = self._sfr_addr(name_or_addr)
        return self.read_latch(addr)

    def set_sfr(self, name_or_addr: str | int, value: int) -> None:
        """Write an SFR by name or address."""
        self.write_direct(self._sfr_addr(name_or_addr), value)

    def _sfr_addr(self, name_or_addr: str | int) -> int:
        if isinstance(name_or_addr, int):
            return name_or_addr & 0xFF
        if name_or_addr not in SFR:
            raise KeyError(f"unknown SFR {name_or_addr!r}")
        return SFR[name_or_addr]

    # ------------------------------------------------------------------
    # registers
    # ------------------------------------------------------------------
    def _get_bank(self) -> int:
        return (self.read_latch(SFR["PSW"]) >> 3) & 0x03

    def get_reg(self, name: str) -> int:
        """Read a register: A, B, PSW, SP, DPL, DPH, DPTR, PC or R0-R7."""
        name = name.upper()
        if name == "A":
            return self.read_latch(SFR["ACC"])
        if name == "B":
            return self.read_latch(SFR["B"])
        if name == "PSW":
            return self.read_latch(SFR["PSW"])
        if name == "SP":
            return self.read_latch(SFR["SP"])
        if name == "DPL":
            return self.read_latch(SFR["DPL"])
        if name == "DPH":
            return self.read_latch(SFR["DPH"])
        if name == "DPTR":
            return (self.read_latch(SFR["DPH"]) << 8) | self.read_latch(SFR["DPL"])
        if name == "PC":
            return self.pc
        if name.startswith("R") and name[1:].isdigit() and 0 <= int(name[1:]) <= 7:
            return self.iram[self._get_bank() * 8 + int(name[1:])]
        raise KeyError(f"unknown register {name!r}")

    def set_reg(self, name: str, value: int) -> None:
        """Write a register: A, B, PSW, SP, DPL, DPH, DPTR, PC or R0-R7."""
        name = name.upper()
        value &= 0xFFFF if name in {"PC", "DPTR"} else 0xFF
        if name == "A":
            self.write_direct(SFR["ACC"], value)
        elif name == "B":
            self.write_direct(SFR["B"], value)
        elif name == "PSW":
            self.write_direct(SFR["PSW"], value)
        elif name == "SP":
            self.write_direct(SFR["SP"], value)
        elif name == "DPL":
            self.write_direct(SFR["DPL"], value)
        elif name == "DPH":
            self.write_direct(SFR["DPH"], value)
        elif name == "DPTR":
            self.write_direct(SFR["DPH"], (value >> 8) & 0xFF)
            self.write_direct(SFR["DPL"], value & 0xFF)
        elif name == "PC":
            self.pc = value & 0xFFFF
        elif name.startswith("R") and name[1:].isdigit() and 0 <= int(name[1:]) <= 7:
            self.iram[self._get_bank() * 8 + int(name[1:])] = value
        else:
            raise KeyError(f"unknown register {name!r}")

    def _reg(self, n: int) -> int:
        return self.iram[self._get_bank() * 8 + (n & 7)]

    def _set_reg(self, n: int, value: int) -> None:
        self.iram[self._get_bank() * 8 + (n & 7)] = value & 0xFF

    def _get_a(self) -> int:
        return self.read_latch(SFR["ACC"])

    def _set_a(self, value: int) -> None:
        self.write_direct(SFR["ACC"], value)

    def _get_dptr(self) -> int:
        return (self.read_latch(SFR["DPH"]) << 8) | self.read_latch(SFR["DPL"])

    def _set_dptr(self, value: int) -> None:
        self.write_direct(SFR["DPH"], (value >> 8) & 0xFF)
        self.write_direct(SFR["DPL"], value & 0xFF)

    def _get_flag(self, name: str) -> int:
        bit = {"C": 7, "AC": 6, "OV": 2}[name]
        return (self.read_latch(SFR["PSW"]) >> bit) & 1

    def _set_flag(self, name: str, value: int) -> None:
        bit = {"C": 7, "AC": 6, "OV": 2}[name]
        psw = self.read_latch(SFR["PSW"])
        psw = (psw | (1 << bit)) if value else (psw & ~(1 << bit))
        self.write_direct(SFR["PSW"], psw)

    def _parity(self, value: int) -> int:
        return bin(value & 0xFF).count("1") & 1

    def _update_parity(self) -> None:
        psw = self.read_latch(SFR["PSW"])
        self.sfr[SFR["PSW"] - 0x80] = (psw & 0xFE) | self._parity(self._get_a())

    # ------------------------------------------------------------------
    # pins
    # ------------------------------------------------------------------
    def _sync_pins(self) -> None:
        for addr, index in PORT_INDEX.items():
            latch = self.sfr[addr - 0x80]
            mask = self.ext_mask[index]
            self.pins[index] = ((latch & ~mask) | (self.ext_value[index] & mask)) & 0xFF

    def drive_pin(self, port: int, bit: int, value: int) -> None:
        """Drive one external pin; the port latch is unchanged."""
        index = PORT_INDEX.get(port, port) if port in PORT_INDEX else port
        if not 0 <= index <= 3 or not 0 <= bit <= 7:
            raise ValueError("port must be 0..3/0x80..0xB0 and bit 0..7")
        mask = 1 << bit
        self.ext_mask[index] |= mask
        if value:
            self.ext_value[index] |= mask
        else:
            self.ext_value[index] &= ~mask & 0xFF

    def drive_port(self, port: int, mask: int, value: int) -> None:
        """Drive selected pins; bits not set in mask keep their level."""
        index = PORT_INDEX.get(port, port) if port in PORT_INDEX else port
        if not 0 <= index <= 3:
            raise ValueError("port must be 0..3/0x80..0xB0")
        self.ext_mask[index] |= mask & 0xFF
        self.ext_value[index] = ((self.ext_value[index] & ~mask) | (value & mask)) & 0xFF

    def release_pin(self, port: int, mask: int = 0xFF) -> None:
        """Release external drive on the selected pins."""
        index = PORT_INDEX.get(port, port) if port in PORT_INDEX else port
        if not 0 <= index <= 3:
            raise ValueError("port must be 0..3/0x80..0xB0")
        self.ext_mask[index] &= ~mask & 0xFF

    # ------------------------------------------------------------------
    # execution
    # ------------------------------------------------------------------
    def _at_boundary(self) -> bool:
        return self._op is None and self._vector is None

    def _begin_instruction(self) -> None:
        pc = self.pc & 0xFFFF
        opcode = self.code[pc]
        entry = OPCODES[opcode]
        if entry is None:
            raise Trap(pc, opcode, self.cycle)
        size = entry["bytes"]
        self._opcode = opcode
        self._op = entry
        self._instr_pc = pc
        self._next_pc = (pc + size) & 0xFFFF
        self._args = tuple(self.code[(pc + i) & 0xFFFF] for i in range(1, size))
        self._branch_target = None
        self._remaining = entry["cycles"] - 1
        if self._remaining == 0:
            self._execute_current()

    def _execute_current(self) -> None:
        entry = self._op
        if entry is None:
            return
        handler = getattr(self, "_op_" + entry["form"].lower(), None)
        if handler is None:
            raise NotImplementedError(f"handler missing for {entry['form']}")
        handler()
        self.pc = self._branch_target if self._branch_target is not None else self._next_pc
        self.pc &= 0xFFFF
        self._op = None
        self._branch_target = None
        self._remaining = 0

    def execute_instruction(self) -> dict:
        """Execute one complete instruction and return a summary."""
        if self.halted:
            return {"pc": self.pc, "cycles": 0, "halted": True}
        start_pc = self.pc
        start_cycle = self.cycle
        while True:
            self.tick_cycle()
            if self._at_boundary() and not self.idle:
                break
            if self.idle and self._vector is None:
                break
            if self.halted:
                break
        return {
            "pc": start_pc,
            "next_pc": self.pc,
            "cycles": self.cycle - start_cycle,
            "halted": self.halted,
        }

    def tick_cycle(self) -> tuple[Event, ...]:
        """Advance exactly one machine cycle."""
        self._events.clear()
        if self.halted:
            self.cycle += 1
            return ()
        self.cycle += 1

        # Apply external drive changes made after the previous cycle.
        self._sync_pins()

        # Timer/interrupt inputs use the state at the start of the cycle.
        snap_tmod = self.read_latch(SFR["TMOD"])
        snap_tcon = self.read_latch(SFR["TCON"])
        snap_pins = bytes(self.pins)

        if self._vector is not None:
            self._vector_step()
        elif self._op is not None:
            self._remaining -= 1
            if self._remaining == 0:
                self._execute_current()
        elif self.idle:
            pass
        else:
            self._begin_instruction()

        self._tick_timers(snap_tmod, snap_tcon, snap_pins)
        self._sync_pins()
        self._sample_external()
        if self._at_boundary():
            self._poll_interrupts()
        return tuple(self._events)

    def _vector_step(self) -> None:
        vector = self._vector
        if vector is None:
            return
        if vector["phase"] == 0:
            pc = vector["saved_pc"]
            self._push(pc & 0xFF)
            self._push((pc >> 8) & 0xFF)
            vector["phase"] = 1
        else:
            self.pc = vector["target"] & 0xFFFF
            self._vector = None
            # Do not accept another interrupt before the first ISR instruction.
            self._irq_inhibit = True

    # ------------------------------------------------------------------
    # stack
    # ------------------------------------------------------------------
    def _push(self, value: int) -> None:
        sp = (self.read_latch(SFR["SP"]) + 1) & 0xFF
        self.write_direct(SFR["SP"], sp)
        self.write_indirect(sp, value)

    def _pop(self) -> int:
        sp = self.read_latch(SFR["SP"])
        value = self.read_indirect(sp)
        self.write_direct(SFR["SP"], (sp - 1) & 0xFF)
        return value

    def _push16(self, value: int) -> None:
        self._push(value & 0xFF)
        self._push((value >> 8) & 0xFF)

    def _pop16(self) -> int:
        high = self._pop()
        low = self._pop()
        return ((high << 8) | low) & 0xFFFF

    # ------------------------------------------------------------------
    # timers
    # ------------------------------------------------------------------
    def _timer_enabled(self, t: int, tmod: int, tcon: int, pins: bytes) -> bool:
        tr_bit = 4 if t == 0 else 6
        gate_bit = 3 if t == 0 else 7
        int_bit = 2 if t == 0 else 3
        if not ((tcon >> tr_bit) & 1):
            return False
        if (tmod >> gate_bit) & 1:
            return bool((pins[3] >> int_bit) & 1)
        return True

    def _tick_timers(self, tmod: int, tcon: int, pins: bytes) -> None:
        t0_mode = tmod & 0x03
        t1_mode = (tmod >> 4) & 0x03
        if t0_mode == 3:
            self._tick_tl0(tmod, tcon, pins)
            self._tick_th0(tcon)
            return
        self._tick_timer(0, t0_mode, tmod, tcon, pins)
        if t1_mode != 3:
            self._tick_timer(1, t1_mode, tmod, tcon, pins)

    def _tick_timer(self, t: int, mode: int, tmod: int, tcon: int, pins: bytes) -> None:
        if not self._timer_enabled(t, tmod, tcon, pins):
            self._timer_prev[t] = (pins[3] >> (4 if t == 0 else 5)) & 1
            return
        ct_bit = 2 if t == 0 else 6
        if (tmod >> ct_bit) & 1:
            pin = (pins[3] >> (4 if t == 0 else 5)) & 1
            if self._timer_pending[t]:
                self._timer_pending[t] = False
                self._increment_timer(t, mode)
            if self._timer_prev[t] and not pin:
                self._timer_pending[t] = True
            self._timer_prev[t] = pin
        else:
            self._increment_timer(t, mode)

    def _increment_timer(self, t: int, mode: int) -> None:
        tl_addr = SFR["TL0"] if t == 0 else SFR["TL1"]
        th_addr = SFR["TH0"] if t == 0 else SFR["TH1"]
        tf_bit = 5 if t == 0 else 7
        tl = self.read_latch(tl_addr)
        th = self.read_latch(th_addr)
        if mode == 0:
            value = ((th << 5) | (tl & 0x1F)) + 1
            if value & 0x2000:
                self._set_tf(t, 1)
                value &= 0x1FFF
            self.write_direct(th_addr, (value >> 5) & 0xFF)
            self.write_direct(tl_addr, (tl & 0xE0) | (value & 0x1F))
        elif mode == 1:
            value = ((th << 8) | tl) + 1
            if value & 0x10000:
                self._set_tf(t, 1)
                value &= 0xFFFF
            self.write_direct(th_addr, (value >> 8) & 0xFF)
            self.write_direct(tl_addr, value & 0xFF)
        elif mode == 2:
            value = tl + 1
            if value & 0x100:
                self._set_tf(t, 1)
                value = th
            self.write_direct(tl_addr, value & 0xFF)
        else:
            value = (tl + 1) & 0xFF
            if value == 0:
                self._set_tf(t, 1)
            self.write_direct(tl_addr, value)

    def _tick_tl0(self, tmod: int, tcon: int, pins: bytes) -> None:
        if not self._timer_enabled(0, tmod, tcon, pins):
            self._timer_prev[0] = (pins[3] >> 4) & 1
            return
        if (tmod >> 2) & 1:
            pin = (pins[3] >> 4) & 1
            if self._timer_pending[0]:
                self._timer_pending[0] = False
                self._increment_timer(0, 3)
            if self._timer_prev[0] and not pin:
                self._timer_pending[0] = True
            self._timer_prev[0] = pin
        else:
            self._increment_timer(0, 3)

    def _tick_th0(self, tcon: int) -> None:
        if not ((tcon >> 6) & 1):  # TR1 controls TH0 in mode 3
            return
        th = (self.read_latch(SFR["TH0"]) + 1) & 0xFF
        if th == 0:
            self._set_tf(1, 1)
        self.write_direct(SFR["TH0"], th)

    def _set_tf(self, t: int, value: int) -> None:
        bit = 5 if t == 0 else 7
        tcon = self.read_latch(SFR["TCON"])
        tcon = (tcon | (1 << bit)) if value else (tcon & ~(1 << bit))
        self.write_direct(SFR["TCON"], tcon)

    # ------------------------------------------------------------------
    # interrupts
    # ------------------------------------------------------------------
    def _sample_external(self) -> None:
        tcon = self.read_latch(SFR["TCON"])
        p3 = self.pins[3]
        for index, (int_bit, it_bit, flag_bit) in enumerate(
            ((2, 0, 1), (3, 2, 3))
        ):
            now = (p3 >> int_bit) & 1
            if (tcon >> it_bit) & 1:
                if self._int_prev[index] and not now:
                    tcon |= 1 << flag_bit
            else:
                tcon = (tcon | (1 << flag_bit)) if not now else (tcon & ~(1 << flag_bit))
            self._int_prev[index] = now
        self.write_direct(SFR["TCON"], tcon)

    def _poll_interrupts(self) -> None:
        if self._irq_inhibit:
            self._irq_inhibit = False
            return
        ie = self.read_latch(SFR["IE"])
        if not (ie & 0x80):
            return
        tcon = self.read_latch(SFR["TCON"])
        ip = self.read_latch(SFR["IP"])
        # (name, flag_bit, enable_bit, priority_bit, vector, clear_flag)
        sources = (
            ("IE0", 1, IE_BITS["EX0"], IP_BITS["PX0"], VECTORS["IE0"], bool((tcon >> 0) & 1)),
            ("TF0", 5, IE_BITS["ET0"], IP_BITS["PT0"], VECTORS["TF0"], True),
            ("IE1", 3, IE_BITS["EX1"], IP_BITS["PX1"], VECTORS["IE1"], bool((tcon >> 2) & 1)),
            ("TF1", 7, IE_BITS["ET1"], IP_BITS["PT1"], VECTORS["TF1"], True),
        )
        candidates = []
        for order, (name, flag_bit, enable_bit, prio_bit, vector, clear_flag) in enumerate(sources):
            if not (ie & (1 << enable_bit)):
                continue
            if not ((tcon >> flag_bit) & 1):
                continue
            level = (ip >> prio_bit) & 1
            if self._active_prio and self._active_prio[-1] >= level:
                continue
            candidates.append((level, -order, name, flag_bit, vector, clear_flag))
        if not candidates:
            return
        level, _, name, flag_bit, vector, clear_flag = max(candidates)
        if clear_flag:
            tcon &= ~(1 << flag_bit)
            self.write_direct(SFR["TCON"], tcon)
        self._active_prio.append(level)
        if self.idle:
            self.write_direct(SFR["PCON"], self.read_latch(SFR["PCON"]) & 0xFE)
        self._vector = {"saved_pc": self.pc, "target": int(vector, 16), "phase": 0}
        self._events.append(Event("interrupt", name, self.cycle))

    # ------------------------------------------------------------------
    # helpers for instruction handlers
    # ------------------------------------------------------------------
    def _rel_target(self, index: int = 0) -> int:
        value = self._args[index]
        offset = value - 256 if value & 0x80 else value
        return (self._next_pc + offset) & 0xFFFF

    def _addr11(self) -> int:
        return ((self._next_pc & 0xF800) | ((self._opcode & 0xE0) << 3) | self._args[0]) & 0xFFFF

    def _addr16(self) -> int:
        return ((self._args[0] << 8) | self._args[1]) & 0xFFFF

    def _branch(self, target: int) -> None:
        self._branch_target = target & 0xFFFF

    @staticmethod
    def _signed8(value: int) -> int:
        return value - 256 if value & 0x80 else value

    # arithmetic helpers
    @staticmethod
    def _add8(a: int, b: int, carry: int = 0) -> tuple[int, int, int, int]:
        total = a + b + carry
        result = total & 0xFF
        c = 1 if total > 0xFF else 0
        ac = 1 if ((a & 0x0F) + (b & 0x0F) + carry) > 0x0F else 0
        ov = 1 if (((a ^ result) & (b ^ result)) & 0x80) else 0
        return result, c, ac, ov

    @staticmethod
    def _sub8(a: int, b: int, borrow: int = 0) -> tuple[int, int, int, int]:
        total = a - b - borrow
        result = total & 0xFF
        c = 1 if total < 0 else 0
        ac = 1 if (a & 0x0F) - (b & 0x0F) - borrow < 0 else 0
        ov = 1 if (((a ^ b) & (a ^ result)) & 0x80) else 0
        return result, c, ac, ov

    # ------------------------------------------------------------------
    # 8051 instruction handlers
    # ------------------------------------------------------------------
    def _op_nop(self) -> None:
        pass

    # ADD / ADDC / SUBB -------------------------------------------------
    def _op_add_a_rn(self) -> None:
        result, c, ac, ov = self._add8(self._get_a(), self._reg(self._opcode & 7))
        self._set_a(result)
        self._set_flag("C", c)
        self._set_flag("AC", ac)
        self._set_flag("OV", ov)

    def _op_add_a_direct(self) -> None:
        result, c, ac, ov = self._add8(self._get_a(), self.read_direct(self._args[0]))
        self._set_a(result)
        self._set_flag("C", c)
        self._set_flag("AC", ac)
        self._set_flag("OV", ov)

    def _op_add_a_at_ri(self) -> None:
        result, c, ac, ov = self._add8(self._get_a(), self.read_indirect(self._reg(self._opcode & 1)))
        self._set_a(result)
        self._set_flag("C", c)
        self._set_flag("AC", ac)
        self._set_flag("OV", ov)

    def _op_add_a_imm_data(self) -> None:
        result, c, ac, ov = self._add8(self._get_a(), self._args[0])
        self._set_a(result)
        self._set_flag("C", c)
        self._set_flag("AC", ac)
        self._set_flag("OV", ov)

    def _op_addc_a_rn(self) -> None:
        result, c, ac, ov = self._add8(self._get_a(), self._reg(self._opcode & 7), self._get_flag("C"))
        self._set_a(result)
        self._set_flag("C", c)
        self._set_flag("AC", ac)
        self._set_flag("OV", ov)

    def _op_addc_a_direct(self) -> None:
        result, c, ac, ov = self._add8(self._get_a(), self.read_direct(self._args[0]), self._get_flag("C"))
        self._set_a(result)
        self._set_flag("C", c)
        self._set_flag("AC", ac)
        self._set_flag("OV", ov)

    def _op_addc_a_at_ri(self) -> None:
        result, c, ac, ov = self._add8(self._get_a(), self.read_indirect(self._reg(self._opcode & 1)), self._get_flag("C"))
        self._set_a(result)
        self._set_flag("C", c)
        self._set_flag("AC", ac)
        self._set_flag("OV", ov)

    def _op_addc_a_imm_data(self) -> None:
        result, c, ac, ov = self._add8(self._get_a(), self._args[0], self._get_flag("C"))
        self._set_a(result)
        self._set_flag("C", c)
        self._set_flag("AC", ac)
        self._set_flag("OV", ov)

    def _op_subb_a_rn(self) -> None:
        result, c, ac, ov = self._sub8(self._get_a(), self._reg(self._opcode & 7), self._get_flag("C"))
        self._set_a(result)
        self._set_flag("C", c)
        self._set_flag("AC", ac)
        self._set_flag("OV", ov)

    def _op_subb_a_direct(self) -> None:
        result, c, ac, ov = self._sub8(self._get_a(), self.read_direct(self._args[0]), self._get_flag("C"))
        self._set_a(result)
        self._set_flag("C", c)
        self._set_flag("AC", ac)
        self._set_flag("OV", ov)

    def _op_subb_a_at_ri(self) -> None:
        result, c, ac, ov = self._sub8(self._get_a(), self.read_indirect(self._reg(self._opcode & 1)), self._get_flag("C"))
        self._set_a(result)
        self._set_flag("C", c)
        self._set_flag("AC", ac)
        self._set_flag("OV", ov)

    def _op_subb_a_imm_data(self) -> None:
        result, c, ac, ov = self._sub8(self._get_a(), self._args[0], self._get_flag("C"))
        self._set_a(result)
        self._set_flag("C", c)
        self._set_flag("AC", ac)
        self._set_flag("OV", ov)

    # ANL / ORL / XRL ---------------------------------------------------
    def _op_anl_a_rn(self) -> None:
        self._set_a(self._get_a() & self._reg(self._opcode & 7))

    def _op_anl_a_direct(self) -> None:
        self._set_a(self._get_a() & self.read_direct(self._args[0]))

    def _op_anl_a_at_ri(self) -> None:
        self._set_a(self._get_a() & self.read_indirect(self._reg(self._opcode & 1)))

    def _op_anl_a_imm_data(self) -> None:
        self._set_a(self._get_a() & self._args[0])

    def _op_anl_direct_a(self) -> None:
        addr = self._args[0]
        self.write_direct(addr, self.read_latch(addr) & self._get_a())

    def _op_anl_direct_imm_data(self) -> None:
        addr = self._args[0]
        self.write_direct(addr, self.read_latch(addr) & self._args[1])

    def _op_anl_c_bit(self) -> None:
        self._set_flag("C", self._get_flag("C") & self.read_bit(self._args[0]))

    def _op_anl_c_not_bit(self) -> None:
        self._set_flag("C", self._get_flag("C") & (1 - self.read_bit(self._args[0])))

    def _op_orl_a_rn(self) -> None:
        self._set_a(self._get_a() | self._reg(self._opcode & 7))

    def _op_orl_a_direct(self) -> None:
        self._set_a(self._get_a() | self.read_direct(self._args[0]))

    def _op_orl_a_at_ri(self) -> None:
        self._set_a(self._get_a() | self.read_indirect(self._reg(self._opcode & 1)))

    def _op_orl_a_imm_data(self) -> None:
        self._set_a(self._get_a() | self._args[0])

    def _op_orl_direct_a(self) -> None:
        addr = self._args[0]
        self.write_direct(addr, self.read_latch(addr) | self._get_a())

    def _op_orl_direct_imm_data(self) -> None:
        addr = self._args[0]
        self.write_direct(addr, self.read_latch(addr) | self._args[1])

    def _op_orl_c_bit(self) -> None:
        self._set_flag("C", self._get_flag("C") | self.read_bit(self._args[0]))

    def _op_orl_c_not_bit(self) -> None:
        self._set_flag("C", self._get_flag("C") | (1 - self.read_bit(self._args[0])))

    def _op_xrl_a_rn(self) -> None:
        self._set_a(self._get_a() ^ self._reg(self._opcode & 7))

    def _op_xrl_a_direct(self) -> None:
        self._set_a(self._get_a() ^ self.read_direct(self._args[0]))

    def _op_xrl_a_at_ri(self) -> None:
        self._set_a(self._get_a() ^ self.read_indirect(self._reg(self._opcode & 1)))

    def _op_xrl_a_imm_data(self) -> None:
        self._set_a(self._get_a() ^ self._args[0])

    def _op_xrl_direct_a(self) -> None:
        addr = self._args[0]
        self.write_direct(addr, self.read_latch(addr) ^ self._get_a())

    def _op_xrl_direct_imm_data(self) -> None:
        addr = self._args[0]
        self.write_direct(addr, self.read_latch(addr) ^ self._args[1])

    # CJNE --------------------------------------------------------------
    def _op_cjne_a_direct_rel(self) -> None:
        first = self._get_a()
        second = self.read_direct(self._args[0])
        self._set_flag("C", 1 if first < second else 0)
        if first != second:
            self._branch(self._rel_target(-1))

    def _op_cjne_a_imm_data_rel(self) -> None:
        first = self._get_a()
        second = self._args[0]
        self._set_flag("C", 1 if first < second else 0)
        if first != second:
            self._branch(self._rel_target(-1))

    def _op_cjne_rn_imm_data_rel(self) -> None:
        first = self._reg(self._opcode & 7)
        second = self._args[0]
        self._set_flag("C", 1 if first < second else 0)
        if first != second:
            self._branch(self._rel_target(-1))

    def _op_cjne_at_ri_imm_data_rel(self) -> None:
        first = self.read_indirect(self._reg(self._opcode & 1))
        second = self._args[0]
        self._set_flag("C", 1 if first < second else 0)
        if first != second:
            self._branch(self._rel_target(-1))

    # jumps / calls -----------------------------------------------------
    def _op_ajmp_addr11(self) -> None:
        self._branch(self._addr11())

    def _op_acall_addr11(self) -> None:
        self._push16(self._next_pc)
        self._branch(self._addr11())

    def _op_ljmp_addr16(self) -> None:
        self._branch(self._addr16())

    def _op_lcall_addr16(self) -> None:
        self._push16(self._next_pc)
        self._branch(self._addr16())

    def _op_sjmp_rel(self) -> None:
        self._branch(self._rel_target(0))

    def _op_jc_rel(self) -> None:
        if self._get_flag("C"):
            self._branch(self._rel_target(0))

    def _op_jnc_rel(self) -> None:
        if not self._get_flag("C"):
            self._branch(self._rel_target(0))

    def _op_jz_rel(self) -> None:
        if self._get_a() == 0:
            self._branch(self._rel_target(0))

    def _op_jnz_rel(self) -> None:
        if self._get_a() != 0:
            self._branch(self._rel_target(0))

    def _op_jb_bit_rel(self) -> None:
        if self.read_bit(self._args[0]):
            self._branch(self._rel_target(-1))

    def _op_jnb_bit_rel(self) -> None:
        if not self.read_bit(self._args[0]):
            self._branch(self._rel_target(-1))

    def _op_jbc_bit_rel(self) -> None:
        bit = self._args[0]
        if self.read_bit(bit, latch=True):
            self.write_bit(bit, 0)
            self._branch(self._rel_target(-1))

    def _op_jmp_at_a_plus_dptr(self) -> None:
        self._branch((self._get_a() + self._get_dptr()) & 0xFFFF)

    def _op_ret(self) -> None:
        self._branch(self._pop16())

    def _op_reti(self) -> None:
        self._branch(self._pop16())
        if self._active_prio:
            self._active_prio.pop()
        self._irq_inhibit = True

    # bit instructions --------------------------------------------------
    def _op_clr_a(self) -> None:
        self._set_a(0)

    def _op_clr_c(self) -> None:
        self._set_flag("C", 0)

    def _op_clr_bit(self) -> None:
        self.write_bit(self._args[0], 0)

    def _op_cpl_a(self) -> None:
        self._set_a((~self._get_a()) & 0xFF)

    def _op_cpl_c(self) -> None:
        self._set_flag("C", 1 - self._get_flag("C"))

    def _op_cpl_bit(self) -> None:
        bit = self._args[0]
        self.write_bit(bit, 1 - self.read_bit(bit, latch=True))

    def _op_setb_c(self) -> None:
        self._set_flag("C", 1)

    def _op_setb_bit(self) -> None:
        self.write_bit(self._args[0], 1)

    # arithmetic / logic misc -------------------------------------------
    def _op_da_a(self) -> None:
        a = self._get_a()
        c = self._get_flag("C")
        ac = self._get_flag("AC")
        if (a & 0x0F) > 9 or ac:
            a += 6
            if a > 0xFF:
                c = 1
            a &= 0xFF
        if ((a >> 4) & 0x0F) > 9 or c:
            a += 0x60
            if a > 0xFF:
                c = 1
            a &= 0xFF
        self._set_a(a)
        self._set_flag("C", c)

    def _op_mul_ab(self) -> None:
        product = self._get_a() * self.read_latch(SFR["B"])
        self._set_a(product & 0xFF)
        self.write_direct(SFR["B"], (product >> 8) & 0xFF)
        self._set_flag("C", 0)
        self._set_flag("OV", 1 if product > 0xFF else 0)

    def _op_div_ab(self) -> None:
        a = self._get_a()
        b = self.read_latch(SFR["B"])
        self._set_flag("C", 0)
        if b == 0:
            self._set_flag("OV", 1)
            return
        self._set_a(a // b)
        self.write_direct(SFR["B"], a % b)
        self._set_flag("OV", 0)

    def _op_inc_a(self) -> None:
        self._set_a((self._get_a() + 1) & 0xFF)

    def _op_inc_rn(self) -> None:
        n = self._opcode & 7
        self._set_reg(n, (self._reg(n) + 1) & 0xFF)

    def _op_inc_direct(self) -> None:
        addr = self._args[0]
        self.write_direct(addr, (self.read_latch(addr) + 1) & 0xFF)

    def _op_inc_at_ri(self) -> None:
        addr = self._reg(self._opcode & 1)
        self.write_indirect(addr, (self.read_indirect(addr) + 1) & 0xFF)

    def _op_inc_dptr(self) -> None:
        self._set_dptr((self._get_dptr() + 1) & 0xFFFF)

    def _op_dec_a(self) -> None:
        self._set_a((self._get_a() - 1) & 0xFF)

    def _op_dec_rn(self) -> None:
        n = self._opcode & 7
        self._set_reg(n, (self._reg(n) - 1) & 0xFF)

    def _op_dec_direct(self) -> None:
        addr = self._args[0]
        self.write_direct(addr, (self.read_latch(addr) - 1) & 0xFF)

    def _op_dec_at_ri(self) -> None:
        addr = self._reg(self._opcode & 1)
        self.write_indirect(addr, (self.read_indirect(addr) - 1) & 0xFF)

    def _op_djnz_rn_rel(self) -> None:
        n = self._opcode & 7
        value = (self._reg(n) - 1) & 0xFF
        self._set_reg(n, value)
        if value:
            self._branch(self._rel_target(-1))

    def _op_djnz_direct_rel(self) -> None:
        addr = self._args[0]
        value = (self.read_latch(addr) - 1) & 0xFF
        self.write_direct(addr, value)
        if value:
            self._branch(self._rel_target(-1))

    def _op_rl_a(self) -> None:
        a = self._get_a()
        self._set_a(((a << 1) | (a >> 7)) & 0xFF)

    def _op_rr_a(self) -> None:
        a = self._get_a()
        self._set_a(((a >> 1) | ((a & 1) << 7)) & 0xFF)

    def _op_rlc_a(self) -> None:
        a = self._get_a()
        c = self._get_flag("C")
        self._set_flag("C", (a >> 7) & 1)
        self._set_a(((a << 1) | c) & 0xFF)

    def _op_rrc_a(self) -> None:
        a = self._get_a()
        c = self._get_flag("C")
        self._set_flag("C", a & 1)
        self._set_a(((a >> 1) | (c << 7)) & 0xFF)

    def _op_swap_a(self) -> None:
        a = self._get_a()
        self._set_a(((a << 4) | (a >> 4)) & 0xFF)

    # data transfer -----------------------------------------------------
    def _op_mov_a_rn(self) -> None:
        self._set_a(self._reg(self._opcode & 7))

    def _op_mov_a_direct(self) -> None:
        self._set_a(self.read_direct(self._args[0]))

    def _op_mov_a_at_ri(self) -> None:
        self._set_a(self.read_indirect(self._reg(self._opcode & 1)))

    def _op_mov_a_imm_data(self) -> None:
        self._set_a(self._args[0])

    def _op_mov_rn_a(self) -> None:
        self._set_reg(self._opcode & 7, self._get_a())

    def _op_mov_rn_direct(self) -> None:
        self._set_reg(self._opcode & 7, self.read_direct(self._args[0]))

    def _op_mov_rn_imm_data(self) -> None:
        self._set_reg(self._opcode & 7, self._args[0])

    def _op_mov_direct_a(self) -> None:
        self.write_direct(self._args[0], self._get_a())

    def _op_mov_direct_rn(self) -> None:
        self.write_direct(self._args[0], self._reg(self._opcode & 7))

    def _op_mov_direct_direct(self) -> None:
        # Guide note: source byte first, destination byte second.
        self.write_direct(self._args[1], self.read_direct(self._args[0]))

    def _op_mov_direct_at_ri(self) -> None:
        self.write_direct(self._args[0], self.read_indirect(self._reg(self._opcode & 1)))

    def _op_mov_direct_imm_data(self) -> None:
        self.write_direct(self._args[0], self._args[1])

    def _op_mov_at_ri_a(self) -> None:
        self.write_indirect(self._reg(self._opcode & 1), self._get_a())

    def _op_mov_at_ri_direct(self) -> None:
        self.write_indirect(self._reg(self._opcode & 1), self.read_direct(self._args[0]))

    def _op_mov_at_ri_imm_data(self) -> None:
        self.write_indirect(self._reg(self._opcode & 1), self._args[0])

    def _op_mov_c_bit(self) -> None:
        self._set_flag("C", self.read_bit(self._args[0]))

    def _op_mov_bit_c(self) -> None:
        self.write_bit(self._args[0], self._get_flag("C"))

    def _op_mov_dptr_imm_data16(self) -> None:
        self._set_dptr(self._addr16())

    def _op_movc_a_at_a_plus_dptr(self) -> None:
        self._set_a(self.code[(self._get_a() + self._get_dptr()) & 0xFFFF])

    def _op_movc_a_at_a_plus_pc(self) -> None:
        self._set_a(self.code[(self._get_a() + self._next_pc) & 0xFFFF])

    def _op_movx_a_at_ri(self) -> None:
        address = ((self.read_latch(SFR["P2"]) << 8) | self._reg(self._opcode & 1)) & 0xFFFF
        self._set_a(self.xdata[address])

    def _op_movx_a_at_dptr(self) -> None:
        self._set_a(self.xdata[self._get_dptr()])

    def _op_movx_at_ri_a(self) -> None:
        address = ((self.read_latch(SFR["P2"]) << 8) | self._reg(self._opcode & 1)) & 0xFFFF
        self.xdata[address] = self._get_a()

    def _op_movx_at_dptr_a(self) -> None:
        self.xdata[self._get_dptr()] = self._get_a()

    def _op_push_direct(self) -> None:
        self._push(self.read_direct(self._args[0]))

    def _op_pop_direct(self) -> None:
        self.write_direct(self._args[0], self._pop())

    def _op_xch_a_rn(self) -> None:
        n = self._opcode & 7
        value = self._reg(n)
        self._set_reg(n, self._get_a())
        self._set_a(value)

    def _op_xch_a_direct(self) -> None:
        addr = self._args[0]
        value = self.read_direct(addr)
        self.write_direct(addr, self._get_a())
        self._set_a(value)

    def _op_xch_a_at_ri(self) -> None:
        addr = self._reg(self._opcode & 1)
        value = self.read_indirect(addr)
        self.write_indirect(addr, self._get_a())
        self._set_a(value)

    def _op_xchd_a_at_ri(self) -> None:
        addr = self._reg(self._opcode & 1)
        mem = self.read_indirect(addr)
        a = self._get_a()
        self.write_indirect(addr, (mem & 0xF0) | (a & 0x0F))
        self._set_a((a & 0xF0) | (mem & 0x0F))
