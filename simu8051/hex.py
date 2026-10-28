"""Intel HEX loader and 8051 instruction decoder.

This module only parses and decodes. It never executes instructions or
changes CPU state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .isa_table import OPCODES

CODE_SIZE = 0x10000


class HexError(Exception):
    """Raised for malformed or unsupported Intel HEX input."""


@dataclass
class HexRecord:
    line: int
    rectype: int
    address: int
    data: bytes


@dataclass
class Image:
    code: bytearray = field(default_factory=lambda: bytearray([0xFF]) * CODE_SIZE)
    start: Optional[int] = None
    records: list[HexRecord] = field(default_factory=list)
    min_addr: Optional[int] = None
    max_addr: Optional[int] = None

    def write(self, address: int, data: bytes) -> None:
        end = address + len(data)
        if address < 0 or end > CODE_SIZE:
            raise HexError(f"data range 0x{address:04X}-0x{end - 1:04X} outside 64K")
        self.code[address:end] = data
        if data:
            if self.min_addr is None or address < self.min_addr:
                self.min_addr = address
            last = end - 1
            if self.max_addr is None or last > self.max_addr:
                self.max_addr = last


def _hex_byte(text: str, lineno: int) -> int:
    try:
        return int(text, 16)
    except ValueError as exc:
        raise HexError(f"line {lineno}: invalid hex byte {text!r}") from exc


def load_hex(path: str | Path) -> Image:
    """Parse an Intel HEX file produced by as31 or a compatible assembler."""
    image = Image()
    seen_eof = False
    base = 0
    path = Path(path)
    try:
        lines = path.read_text(encoding="ascii", errors="strict").splitlines()
    except OSError as exc:
        raise HexError(f"cannot read {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise HexError(f"{path}: not an ASCII Intel HEX file") from exc

    for lineno, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line:
            continue
        if seen_eof:
            raise HexError(f"line {lineno}: data after EOF record")
        if not line.startswith(":"):
            raise HexError(f"line {lineno}: record does not start with ':'")
        payload = line[1:]
        if len(payload) < 10 or len(payload) % 2:
            raise HexError(f"line {lineno}: invalid record length")
        raw_bytes = bytes(_hex_byte(payload[i : i + 2], lineno) for i in range(0, len(payload), 2))
        count = raw_bytes[0]
        if len(raw_bytes) != count + 5:
            raise HexError(
                f"line {lineno}: length field {count} does not match {len(raw_bytes) - 5} data bytes"
            )
        address = (raw_bytes[1] << 8) | raw_bytes[2]
        rectype = raw_bytes[3]
        data = raw_bytes[4 : 4 + count]
        checksum = raw_bytes[-1]
        if (sum(raw_bytes[:-1]) + checksum) & 0xFF:
            raise HexError(f"line {lineno}: checksum mismatch")

        record = HexRecord(line=lineno, rectype=rectype, address=address, data=data)
        image.records.append(record)

        if rectype == 0x00:  # data
            absolute = base + address
            image.write(absolute, data)
        elif rectype == 0x01:  # EOF
            if count != 0:
                raise HexError(f"line {lineno}: EOF record must have zero data")
            seen_eof = True
        elif rectype == 0x02:  # extended segment address
            if count != 2:
                raise HexError(f"line {lineno}: type 02 must carry 2 bytes")
            base = ((data[0] << 8) | data[1]) << 4
        elif rectype == 0x03:  # start segment address (CS:IP)
            if count != 4:
                raise HexError(f"line {lineno}: type 03 must carry 4 bytes")
            cs = (data[0] << 8) | data[1]
            ip = (data[2] << 8) | data[3]
            image.start = ((cs << 4) + ip) & 0xFFFF
        elif rectype == 0x04:  # extended linear address
            if count != 2:
                raise HexError(f"line {lineno}: type 04 must carry 2 bytes")
            base = ((data[0] << 8) | data[1]) << 16
        elif rectype == 0x05:  # start linear address
            if count != 4:
                raise HexError(f"line {lineno}: type 05 must carry 4 bytes")
            image.start = int.from_bytes(data, "big")
        else:
            raise HexError(f"line {lineno}: unsupported record type 0x{rectype:02X}")

    if not seen_eof:
        raise HexError("missing EOF record")
    return image


@dataclass(frozen=True)
class Instruction:
    address: int
    opcode: int
    form: str
    syntax: str
    raw: bytes
    size: int
    cycles: int
    args: tuple[int, ...]
    reserved: bool = False


def read_code(image: Image, address: int) -> int:
    return image.code[address & 0xFFFF]


def decode_at(image: Image, pc: int) -> Instruction:
    """Decode one instruction from the image at pc (16-bit wraparound)."""
    pc &= 0xFFFF
    opcode = read_code(image, pc)
    entry = OPCODES[opcode]
    if entry is None:
        return Instruction(
            address=pc,
            opcode=opcode,
            form="RESERVED",
            syntax="reserved",
            raw=bytes([opcode]),
            size=1,
            cycles=1,
            args=(),
            reserved=True,
        )
    size = entry["bytes"]
    raw = bytes(read_code(image, pc + i) for i in range(size))
    return Instruction(
        address=pc,
        opcode=opcode,
        form=entry["form"],
        syntax=entry["syntax"],
        raw=raw,
        size=size,
        cycles=entry["cycles"],
        args=raw[1:],
    )


def disassemble(image: Image, start: int = 0, end: int = CODE_SIZE) -> list[Instruction]:
    """Linear disassembly. Stop when the next instruction would pass end."""
    result: list[Instruction] = []
    pc = start & 0xFFFF
    end &= 0xFFFF
    while True:
        inst = decode_at(image, pc)
        if pc + inst.size > end:
            break
        result.append(inst)
        pc = (pc + inst.size) & 0xFFFF
        if pc == (end & 0xFFFF):
            break
        if pc == 0 and start != 0:
            break
    return result


def _signed8(value: int) -> int:
    return value - 256 if value & 0x80 else value


def format_instruction(inst: Instruction) -> str:
    """Format an instruction using actual operand values where possible."""
    if inst.reserved:
        return f"0x{inst.opcode:02X}  reserved"
    syntax = inst.syntax
    args = inst.args
    form = inst.form

    if form == "NOP":
        return "NOP"
    if form.startswith("AJMP_"):
        target = ((inst.address + 2) & 0xF800) | ((inst.opcode & 0xE0) << 3) | args[0]
        return f"AJMP    0x{target:04X}"
    if form.startswith("ACALL_"):
        target = ((inst.address + 2) & 0xF800) | ((inst.opcode & 0xE0) << 3) | args[0]
        return f"ACALL   0x{target:04X}"
    if form.startswith("LJMP_"):
        return f"LJMP    0x{(args[0] << 8) | args[1]:04X}"
    if form.startswith("LCALL_"):
        return f"LCALL   0x{(args[0] << 8) | args[1]:04X}"
    if form in {"SJMP_rel", "JC_rel", "JNC_rel", "JZ_rel", "JNZ_rel"}:
        target = (inst.address + inst.size + _signed8(args[0])) & 0xFFFF
        return f"{syntax:<7} 0x{target:04X}"
    if form in {"JB_bit_rel", "JNB_bit_rel", "JBC_bit_rel"}:
        target = (inst.address + inst.size + _signed8(args[1])) & 0xFFFF
        return f"{syntax:<7} 0x{args[0]:02X},0x{target:04X}"
    if form in {"CJNE_A_IMM_data_rel", "CJNE_Rn_IMM_data_rel", "CJNE_AT_Ri_IMM_data_rel"}:
        target = (inst.address + inst.size + _signed8(args[1])) & 0xFFFF
        return f"{syntax:<7} #0x{args[0]:02X},0x{target:04X}"
    if form == "CJNE_A_direct_rel":
        target = (inst.address + inst.size + _signed8(args[1])) & 0xFFFF
        return f"{syntax:<7} 0x{args[0]:02X},0x{target:04X}"
    if form == "DJNZ_Rn_rel":
        target = (inst.address + inst.size + _signed8(args[0])) & 0xFFFF
        return f"{syntax:<7} 0x{target:04X}"
    if form == "DJNZ_direct_rel":
        target = (inst.address + inst.size + _signed8(args[1])) & 0xFFFF
        return f"{syntax:<7} 0x{args[0]:02X},0x{target:04X}"
    if form == "JMP_AT_A_PLUS_DPTR":
        return "JMP     @A+DPTR"
    if form.startswith("MOVX_"):
        return syntax
    if form.startswith("MOVC_"):
        return syntax
    if form == "MOV_DPTR_IMM_data16":
        return f"MOV     DPTR,#0x{(args[0] << 8) | args[1]:04X}"
    if form == "MOV_direct_direct":
        return f"MOV     0x{args[1]:02X},0x{args[0]:02X}"
    if form == "MOV_direct_IMM_data":
        return f"MOV     0x{args[0]:02X},#0x{args[1]:02X}"
    if form == "MOV_direct_AT_Ri":
        return f"MOV     0x{args[0]:02X},@R{inst.opcode & 1}"
    if form == "MOV_AT_Ri_direct":
        return f"MOV     @R{inst.opcode & 1},0x{args[0]:02X}"
    if form == "MOV_AT_Ri_IMM_data":
        return f"MOV     @R{inst.opcode & 1},#0x{args[0]:02X}"
    if form == "MOV_C_bit":
        return f"MOV     C,0x{args[0]:02X}"
    if form == "MOV_bit_C":
        return f"MOV     0x{args[0]:02X},C"

    # Generic syntax with operand substitution.
    text = syntax
    text = text.replace("Rn", f"R{inst.opcode & 7}")
    text = text.replace("@Ri", f"@R{inst.opcode & 1}")
    if form.endswith("_direct") or "_direct" in form:
        if args:
            text = text.replace("direct", f"0x{args[0]:02X}", 1)
    if "IMM_data16" in form:
        text = text.replace("#data16", f"#0x{(args[0] << 8) | args[1]:04X}")
    elif "IMM_data" in form:
        text = text.replace("#data", f"#0x{args[0]:02X}")
    if "bit" in form and args:
        text = text.replace("bit", f"0x{args[0]:02X}", 1)
    return text
