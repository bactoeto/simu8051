# simu8051 API

`simu8051.session` is the only public module. `core`, `hex`, `plugin`,
`isa_table` and `mem_table` are internal. `tests/test_session_api.py` is the
contract.

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
| `Session.open(path, cold=True)` | parse a HEX file |
| `Session.from_bytes(code, entry=0, cold=True)` | load bytes at address 0 |
| `session.load(path, cold=True)` | replace the program and reset |
| `session.reset(cold=False)` | reset and return to the entry address |

The entry address is the HEX start record, or 0. `from_bytes()` takes it
explicitly. Cold reset clears IRAM/XDATA; warm reset preserves them.
`load()` parses before changing state, so a bad file leaves the session
intact. `open()` and `load()` raise `HexError` on bad input.

## State

| Member | Meaning |
|---|---|
| `pc`, `cycle` | program counter and machine cycles since reset |
| `halted`, `idle` | power-down and idle flags |
| `last_stop` | current `StopReason`, or `None` after resuming |
| `register_names`, `sfr_names` | names for register/SFR access |

`register_names` and `sfr_names` are class attributes too:
`Session.sfr_names`.

## Execution

| Member | Meaning |
|---|---|
| `step()` | execute one instruction |
| `step_cycle()` | advance one machine cycle |
| `run(max_cycles=None, max_instructions=None)` | run until a stop |

`step()` includes any interrupt vectoring caused by that instruction.
`step_cycle()` can leave an instruction partially executed. Both resume
automatically; there is no `resume()`. In power-down they report `halt`
without advancing the cycle counter.

`run()` stops on a breakpoint, a limit, power-down, a reserved opcode, or
`ctx.stop()`.

### Limits

- `max_cycles` is hard: it can stop mid-instruction, and the next call
  continues that instruction.
- `max_instructions` is checked at instruction boundaries and counts only
  completed instructions.
- Limits restart on every `run()` call; `0` returns immediately.
- Idle mode consumes cycles without retiring instructions; use `max_cycles`.

### Results

`StepResult` from `step()` / `step_cycle()`:

| Field | Meaning |
|---|---|
| `pc`, `next_pc` | PC before and after the call |
| `cycles` | machine cycles consumed |
| `reason` | `StopReason` or `None` |
| `instruction` | decoded `Instruction` if one retired |
| `completed` | an instruction retired during this call |
| `halted`, `idle` | flags after the call |

`RunResult` from `run()`:

| Field | Meaning |
|---|---|
| `reason` | why the run stopped |
| `instructions` | completed instructions in this run |
| `cycles` | machine cycles in this run |
| `start_pc`, `end_pc` | PC at start and stop |

### StopReason

```python
StopReason(kind, detail="", pc=None, cycle=None)
```

| `kind` | Trigger |
|---|---|
| `pc_breakpoint`, `cycle_breakpoint` | breakpoint |
| `max_cycles`, `max_instructions` | run limit |
| `halt` | power-down |
| `trap` | reserved opcode |
| `plugin` | `ctx.stop()` |

Use `reason.kind` in code; `str(reason)` is for humans. Traps never escape:
they become `StopReason(kind="trap")`, with the message in `detail`.

## Breakpoints

| Member | Meaning |
|---|---|
| `break_at(address)` | stop before the instruction |
| `break_at_cycle(cycle)` | stop at a machine cycle |
| `clear_breakpoints()` | remove both kinds |
| `breakpoints_pc`, `breakpoints_cycle` | read-only sets |

A PC breakpoint stops before execution; the next `step()`/`run()` executes
that instruction once. A cycle breakpoint may stop mid-instruction.
`step_cycle()` ignores PC breakpoints.

## Memory

```python
session.read("iram", 0x20)
session.write("iram", 0x20, 0x5A)
```

| Space | Range | Meaning |
|---|---|---|
| `code` | `0x0000-0xFFFF` | program memory |
| `iram` | `0x00-0x7F` | internal RAM (`@Ri` view) |
| `sfr` | `0x80-0xFF` | SFR latch |
| `direct` | `0x00-0xFF` | direct address; ports read pins |
| `xdata` | `0x0000-0xFFFF` | external data memory |
| `bit` | `0x00-0xFF` | bit address |

Unknown spaces raise `ValueError`. `iram` above `0x7F` reads `0xFF`; writes
there are ignored. `read("sfr", 0x90)` is the P1 latch;
`read("direct", 0x90)` is the P1 pin. `sfr` and `direct` writes apply SFR
side effects.

## Registers and pins

| Member | Meaning |
|---|---|
| `get_reg` / `set_reg` | `A B PSW SP DPL DPH DPTR PC R0-R7` |
| `get_sfr` / `set_sfr` | SFR by name or address |
| `drive_pin(port, bit, value)` | drive one pin |
| `drive_port(port, mask, value)` | drive selected pins |
| `release_pin(port, mask=0xFF)` | release external drive |

Unknown names raise `KeyError`. Ports are `0..3` or
`0x80/0x90/0xA0/0xB0`. Driving a pin changes the external level, not the
latch; it becomes visible on the next machine cycle.

## Program and disassembly

| Member | Meaning |
|---|---|
| `decode(pc=None)` | decode one instruction |
| `disassemble(start=None, count=8)` | decode a count of instructions |
| `format(instruction)` | render, or `NULL` |
| `is_loaded(address)` | address supplied by the HEX file or a code write |

Execution and disassembly share the CPU's `code` space; a `code` write
changes both.

`is_loaded()` is file coverage, not hardware state. Unwritten bytes and
bytes explicitly loaded as `0xFF` both read `0xFF`; only `is_loaded()`
separates them. `format()` returns `NULL` if any byte of the instruction was
never loaded. The CPU still executes that cell as `0xFF` (`MOV R7, A`).

`decode()` and `disassemble()` always work; an empty program is filled with
`0xFF`.

## Plugins

```python
session.load_plugin("stimulus.py")
session.add_plugin(MyPlugin())
```

| Hook | Called |
|---|---|
| `setup(ctx)` | once, when registered |
| `on_cycle(ctx)` | after every machine cycle |
| `on_instruction(ctx, text)` | after each completed instruction |
| `on_interrupt(ctx, name)` | when an interrupt is accepted |
| `on_stop(ctx, reason)` | when a run or step stops |

A file plugin uses module-level functions; a programmatic plugin subclasses
`Plugin`. `setup` runs once. `load_plugin()` returns the plugin.

| Context | Meaning |
|---|---|
| `ctx.cycle`, `ctx.pc` | current cycle and PC |
| `ctx.read` / `ctx.write` | same as Session |
| `ctx.get_reg` / `ctx.set_reg` | registers |
| `ctx.get_sfr` / `ctx.set_sfr` | SFRs |
| `ctx.drive_pin` / `ctx.release_pin` | external pins |
| `ctx.stop(reason)` | stop with `kind="plugin"` |

Plugin writes take effect on the next machine cycle.

## Errors

| Exception | Raised by |
|---|---|
| `HexError` | malformed or unreadable HEX input |
| `KeyError` | unknown register or SFR |
| `ValueError` | unknown memory space |

## Guarantees and limits

- Deterministic, machine-cycle granularity; no S1-S6 phases.
- Single-threaded; a Session is not thread-safe.
- No serial port, ALE/PSEN/RD/WR timing, block memory, or snapshots.

## Stability

Public names: `Session`, `StepResult`, `RunResult`, `StopReason`, `Plugin`,
`PluginContext`, `Instruction`, `HexError`. Names starting with `_` are
private; importing from the other modules is unsupported.
