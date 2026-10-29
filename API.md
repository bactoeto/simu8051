# simu8051 API

`simu8051.session` is the public interface. A front end — the bundled REPL,
a test harness, a GUI, an automated runner — should import only this module.
`core`, `hex`, `plugin`, `isa_table` and `mem_table` are implementation
details.

Everything documented here is covered by `tests/test_session_api.py`.

## Quick start

```python
from simu8051.session import Session

session = Session.open("firmware.hex")
session.break_at(0x0030)
result = session.run(max_cycles=100_000)

print(session.pc, session.cycle, session.get_reg("A"))
print(result.reason.kind, result.instructions, result.cycles)
```

## Construction

| Member | Meaning |
|---|---|
| `Session.open(path, cold=True)` | parse a HEX file and create a session |
| `Session.from_bytes(code, entry=0, cold=True)` | use raw program bytes |
| `session.load(path, cold=True)` | replace the program and reset |
| `session.reset(cold=False)` | reset the CPU and return to the entry address |

`open()` and `load()` use the HEX start record as the entry address; without
one the entry is 0. `from_bytes()` loads the bytes at address 0 and takes
`entry` explicitly. `reset()` returns the PC to that entry address.

A cold reset clears IRAM and XDATA. A warm reset preserves them.
`load()` parses the new file first: if parsing fails, the session is
unchanged.

`open()` and `load()` raise `HexError` for a malformed or unreadable file.
The `cold` argument of `open()` controls the initial reset; a fresh session
has no previous RAM to preserve.

## State

| Member | Meaning |
|---|---|
| `session.pc` | current program counter |
| `session.cycle` | machine cycles elapsed since reset |
| `session.halted` | power-down flag |
| `session.idle` | idle flag |
| `session.last_stop` | current stop reason, or `None` after resuming |
| `session.register_names` | names accepted by `get_reg()` / `set_reg()` |
| `session.sfr_names` | names accepted by `get_sfr()` / `set_sfr()` |

`register_names` and `sfr_names` are class attributes as well, so a front end
can build menus or completion without an instance:

```python
Session.sfr_names
```

## Execution

| Member | Meaning |
|---|---|
| `session.step()` | execute one instruction |
| `session.step_cycle()` | advance one machine cycle |
| `session.run(max_cycles=None, max_instructions=None)` | run until a stop |

One machine cycle is 12 oscillator cycles.

`step()` executes one complete instruction. If an interrupt is accepted at
the end of that instruction, the vectoring cycles are included in the same
call. `step_cycle()` advances exactly one cycle and can leave an instruction
partially executed.

`step()` and `run()` resume automatically. There is no separate `resume()`;
a stop only describes the result of the call that produced it.

In power-down, `step()` and `step_cycle()` report `halt` without advancing the
cycle counter.

`run()` repeatedly executes instructions until one of these happens:

- a PC or cycle breakpoint is hit;
- `max_cycles` or `max_instructions` is reached;
- the CPU enters power-down;
- a reserved opcode is executed;
- a plugin calls `ctx.stop()`.

### Limits

- `max_cycles` is a hard limit. It can stop in the middle of an instruction;
  the next call continues that instruction.
- `max_instructions` is checked at instruction boundaries and counts only
  completed instructions.
- Both limits restart at every `run()` call.
- `max_cycles=0` and `max_instructions=0` return immediately.
- Idle mode consumes cycles but retires no instructions; use `max_cycles`
  with idle programs.

### Results

`step()` and `step_cycle()` return `StepResult`:

| Field | Meaning |
|---|---|
| `pc` | PC at the start of the call |
| `next_pc` | PC after the call |
| `cycles` | machine cycles consumed |
| `reason` | `StopReason` or `None` |
| `instruction` | decoded `Instruction` if one retired |
| `completed` | an instruction retired during this call |
| `halted` | power-down flag after the call |
| `idle` | idle flag after the call |

`run()` returns `RunResult`:

| Field | Meaning |
|---|---|
| `reason` | why the run stopped |
| `instructions` | instructions completed during this run |
| `cycles` | machine cycles consumed during this run |
| `start_pc` | PC when the run started |
| `end_pc` | PC when the run stopped |

### StopReason

```python
StopReason(kind, detail="", pc=None, cycle=None)
```

| `kind` | Trigger |
|---|---|
| `pc_breakpoint` | PC breakpoint reached |
| `cycle_breakpoint` | cycle breakpoint reached |
| `max_cycles` | run cycle limit |
| `max_instructions` | run instruction limit |
| `halt` | power-down |
| `trap` | reserved opcode |
| `plugin` | `ctx.stop()` |

Use `reason.kind` in programs. `str(reason)` is a human-readable line.
A trap never escapes as an exception; it is reported as
`StopReason(kind="trap")`, with the original message in `detail`.

## Breakpoints

| Member | Meaning |
|---|---|
| `session.break_at(address)` | stop before the instruction at `address` |
| `session.break_at_cycle(cycle)` | stop when the cycle counter reaches `cycle` |
| `session.clear_breakpoints()` | remove both kinds |
| `session.breakpoints_pc` | read-only set of PC breakpoints |
| `session.breakpoints_cycle` | read-only set of cycle breakpoints |

A PC breakpoint stops before the instruction executes, so the instruction
can be inspected at `session.pc`. The next `step()` or `run()` executes it
once instead of stopping at the same address again.

A cycle breakpoint is checked every machine cycle and can stop in the middle
of an instruction. `step_cycle()` does not check PC breakpoints; it always
advances one cycle.

## Memory

```python
value = session.read("iram", 0x20)
session.write("iram", 0x20, 0x5A)
```

| Space | Range | Meaning |
|---|---|---|
| `code` | `0x0000-0xFFFF` | program memory; unwritten bytes read `0xFF` |
| `iram` | `0x00-0x7F` | internal RAM (`@Ri` view) |
| `sfr` | `0x80-0xFF` | SFR latch, no side effects on read |
| `direct` | `0x00-0xFF` | direct address; ports return pin level |
| `xdata` | `0x0000-0xFFFF` | external data memory |
| `bit` | `0x00-0xFF` | bit address |

Unknown space names raise `ValueError`. Addresses are masked to the space
width.

- Reading `iram` above `0x7F` returns `0xFF`; writes there are ignored.
- `read("sfr", 0x90)` returns the P1 latch.
- `read("direct", 0x90)` returns the P1 pin level.
- Writes to `sfr` and `direct` apply the normal SFR side effects (parity,
  interrupt inhibit, port latch).

## Registers and pins

| Member | Meaning |
|---|---|
| `session.get_reg(name)` / `set_reg(name, value)` | `A B PSW SP DPL DPH DPTR PC R0-R7` |
| `session.get_sfr(name_or_addr)` / `set_sfr(name_or_addr, value)` | SFR by name or address |
| `session.drive_pin(port, bit, value)` | drive one external pin |
| `session.drive_port(port, mask, value)` | drive selected pins |
| `session.release_pin(port, mask=0xFF)` | release external drive |

Unknown register or SFR names raise `KeyError`. Port numbers are `0..3` or
`0x80/0x90/0xA0/0xB0`. Port latches and pin levels are separate:
`drive_pin()` changes the external drive only, leaving the latch unchanged.
Pin changes become visible on the next machine cycle.

## Program and disassembly

| Member | Meaning |
|---|---|
| `session.decode(pc=None)` | decode one instruction |
| `session.disassemble(start=None, count=8)` | decode `count` instructions |
| `session.format(instruction)` | render an instruction, or `NULL` |
| `session.is_loaded(address)` | address came from the HEX file or a code write |

Program memory is the CPU's `code` space; Session does not keep a second
copy. A write to `code` therefore changes both execution and disassembly.

`is_loaded()` is loader coverage, not hardware state. On real hardware an
erased cell and a cell programmed to `0xFF` are indistinguishable. Session
tracks which addresses the HEX file supplied (and which were patched through
`write("code", ...)`):

- an unwritten address reads as `0xFF`;
- a byte explicitly loaded as `0xFF` also reads as `0xFF`;
- `is_loaded(address)` is `False` only in the first case.

`format()` returns `NULL` when any byte of the instruction was never loaded.
This is a debugger annotation: the CPU still executes the cell as `0xFF`
(`MOV R7,A`).

`decode()` and `disassemble()` always work. A session always has program
memory; an empty program is filled with `0xFF`.

## Plugins

```python
session.load_plugin("stimulus.py")
session.add_plugin(MyPlugin())
```

Hooks receive a `PluginContext`:

| Function | Called |
|---|---|
| `setup(ctx)` | once, when registered |
| `on_cycle(ctx)` | after every machine cycle |
| `on_instruction(ctx, text)` | after each completed instruction |
| `on_interrupt(ctx, name)` | when an interrupt is accepted |
| `on_stop(ctx, reason)` | when a run or step stops |

A plugin loaded from a file is a module with module-level functions. A
programmatic plugin subclasses `Plugin`. In both cases `setup` runs exactly
once. `load_plugin()` returns the registered plugin; `add_plugin()` returns
`None`.

### PluginContext

| Member | Meaning |
|---|---|
| `ctx.cycle` | current machine cycle |
| `ctx.pc` | current program counter |
| `ctx.read(space, addr)` | same as `session.read` |
| `ctx.write(space, addr, value)` | same as `session.write` |
| `ctx.get_reg` / `ctx.set_reg` | register access |
| `ctx.get_sfr` / `ctx.set_sfr` | SFR access |
| `ctx.drive_pin(port, bit, value)` | drive one external pin |
| `ctx.release_pin(port, mask=0xFF)` | release external drive |
| `ctx.stop(reason)` | stop the current run |

Plugin writes take effect on the next machine cycle. `ctx.stop()` reports
`StopReason(kind="plugin")` with `detail` set to the reason.

## Tracing

```python
session.set_trace(print)
session.set_trace(None)
```

When enabled, Session prints one line per instruction before executing it:

```text
    1234 0030 MOV A,#0x00
```

Tracing is a general observation hook, not a CLI feature.

## Errors

| Exception | Raised by |
|---|---|
| `HexError` | malformed or unreadable HEX input |
| `KeyError` | unknown register or SFR name |
| `ValueError` | unknown memory space |

`Trap` is caught inside `step()`/`run()` and reported through `StopReason`.

## Example: a minimal front end

```python
from simu8051.session import Session

session = Session.open("firmware.hex")
session.break_at(0x0030)
result = session.run()

if result.reason.kind == "pc_breakpoint":
    instruction = session.decode()
    print(f"0x{session.pc:04X}  {session.format(instruction)}")
    print({name: session.get_reg(name) for name in ("A", "B", "PC")})
    session.step()
```

## Guarantees

- Deterministic: the same program, stimulus and plugins produce the same state.
- Machine-cycle granularity; there are no S1-S6 phases.
- No serial port, baud rate or serial interrupts on the base 8051 model.
- Single-threaded; a `Session` is not thread-safe.
- No ALE/PSEN/RD/WR bus timing or electrical characteristics.
- No block memory operations, snapshots or save/restore.

## Stability

Names exported by `simu8051.session` are public:

```python
Session, StepResult, RunResult, StopReason,
Plugin, PluginContext, Instruction, HexError
```

Anything starting with `_` is private. Importing from `simu8051.core`,
`simu8051.hex`, `simu8051.plugin`, `simu8051.isa_table` or
`simu8051.mem_table` is unsupported.
