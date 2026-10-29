# simu8051

A cycle-level Intel 8051 simulator written in Python. It loads Intel HEX
files produced by as31 and provides memory/register inspection, breakpoints,
and a plugin API for external stimuli.

## Status

- 255 valid opcodes; `0xA5` is reserved and traps.
- Timer 0/1, interrupts, ports, internal RAM, SFRs, external data memory.

## Requirements

Python 3.11 or newer. The simulator uses only the standard library.

## Usage

Assemble with as31, then run the resulting HEX file:

```bash
as31 -l firmware.asm
python3 -m simu8051 firmware.hex
python3 -m simu8051 firmware.hex --batch --max-cycles 100000
python3 -m simu8051 firmware.hex --plugin stimulus.py
```

`cli.py` is only a reference front end. `Core`, `Session`, `load_hex` and the
plugin API can be used directly, so the CLI can be replaced without touching
the simulator.

### Library

```python
from simu8051 import Core, Session, load_hex

image = load_hex("firmware.hex")
core = Core()
session = Session(core=core, image=image)
result = session.run(max_cycles=100_000)
print(core.pc, core.get_reg("A"), result.reason)
```

`simu8051/__init__.py` lists the supported exports. Names starting with `_`
are internal and may change.

`Core` — CPU and memory:

| Member | Meaning |
|---|---|
| `execute_instruction()` | execute one instruction; returns `pc`, `next_pc`, `cycles`, `halted` |
| `tick_cycle()` | advance one machine cycle; returns events |
| `reset(cold=False)` | reset; `cold=True` clears IRAM and XDATA |
| `read_direct(addr)` / `write_direct(addr, value)` | direct space, `0x00-0xFF` |
| `read_indirect(addr)` / `write_indirect(addr, value)` | `@Ri` space |
| `read_latch(addr)` / `read_bit(bit)` | SFR latches and bit space |
| `read_xdata(addr)` / `write_xdata(addr, value)` | external data memory |
| `read_code(addr)` | program memory |
| `get_reg(name)` / `set_reg(name, value)` | `A B PSW SP DPL DPH DPTR PC R0-R7` |
| `get_sfr(name)` / `set_sfr(name, value)` | SFR by name or address |
| `drive_pin(port, bit, value)`, `drive_port(port, mask, value)`, `release_pin(port, mask)` | external pin levels |

`Session` — stepping, breakpoints and plugins:

| Member | Meaning |
|---|---|
| `step()` / `step_cycle()` | run one instruction / one machine cycle |
| `run(max_cycles=None, max_instructions=None)` | run until a stop |
| `break_at(addr)` / `break_at_cycle(n)` / `clear_breakpoints()` | breakpoints |
| `stop(reason)` | request the run to stop |
| `reset(cold=False)` | reset the session |

`step()` and `run()` return `StepResult` with `pc`, `next_pc`, `cycles`,
`reason`, `instruction`, `halted` and `idle`.

Intel HEX:

| Function | Meaning |
|---|---|
| `load_hex(path)` | parse a HEX file into an `Image` |
| `decode_at(image, pc)` | decode one instruction |
| `disassemble(image, start=0, end=0x10000)` | decode a range |
| `format_instruction(inst)` | text form |

See [Plugins](#plugins) for the plugin API.

### Commands

```text
run, r [N]              run until a stop condition or N instructions
step, s [N]             execute N instructions
cycle, cy [N]           execute N machine cycles
continue, c             continue after a breakpoint
break, b ADDR           set a PC breakpoint
break cycle N           set a cycle breakpoint
delete, del             clear breakpoints
info, i break|display   list breakpoints or displays
regs, reg               print common registers
sfr, sf [NAME]          print SFRs
x SPACE ADDR [N]        dump, 8 per line: i (internal RAM), x (external RAM), c (ROM)
w SPACE ADDR VALUE...   write memory: i, x, c (c patches the loaded image)
disassemble, d [ADDR] [N]  disassemble
reset, res [cold]       reset
load, l FILE            load another HEX file
display, dp ...         add a persistent display item
undisplay, und N        remove a display item
enable, on N            enable a display item
disable, off N          disable a display item
trace, t on|off         instruction trace
help, h, ?              show commands
quit, q, exit
```

The command list is printed when the interactive prompt starts. Type `help`
or `?` to show it again.

Memory is addressed as `i` (internal RAM, `0x00-0x7F`), `x` (external RAM,
`0x0000-0xFFFF`) and `c` (program memory, `0x0000-0xFFFF`). `w c` patches the
loaded image for debugging; real ROM cannot be written.

Interactive mode keeps history in `~/.local/state/simu8051/history` (or
`$XDG_STATE_HOME/simu8051/history`), completes commands with Tab, and supports
Ctrl-L to clear the screen.

The default persistent display is `regs`, printed after every execution
stop. Use `--no-default-display` to disable it.

## Layout

Only `simu8051/` is needed at runtime; the other directories are development
material.

```text
simu8051/            simulator package
  cli.py             reference CLI (optional front end)
  __main__.py        entry point for `python3 -m simu8051`
  session.py         Session: the public API for stepping and inspection
  core.py            CPU, memory, ports, timers, interrupts
  hex.py             Intel HEX parser, decoder, disassembler
  plugin.py          plugin API and loader
  isa_table.py       generated instruction table
  mem_table.py       generated memory/SFR constants

data/
  isa.json           instruction data
  memory.json        memory, SFR and reset data

tools/               extraction, data build, validation, table generation
tests/               unit and CLI tests
tests/fixtures/      as31 HEX fixtures
examples/            plugin examples
reports/             data review and validation reports
docs/                source documents
build/               generated intermediate files
```

## Data

`data/isa.json` and `data/memory.json` are the source of truth for the
instruction set and memory model. `simu8051/isa_table.py` and
`simu8051/mem_table.py` are generated from them.

```bash
python3 tools/build_data.py
python3 tools/build_memory.py
python3 tools/check_data.py
python3 tools/gen_tables.py
```

Do not edit the generated tables by hand.

## Plugins

A plugin is one `.py` file with module-level functions. Run it with
`--plugin`:

```bash
python3 -m simu8051 firmware.hex --plugin my_plugin.py
```

Define at least one of these functions; all take `ctx` as the first argument:

| Function | Called |
|---|---|
| `setup(ctx)` | once, when the plugin is loaded |
| `on_cycle(ctx)` | after every machine cycle |
| `on_instruction(ctx, text)` | after each completed instruction |
| `on_interrupt(ctx, name)` | when an interrupt is accepted |
| `on_stop(ctx, reason)` | when the run stops |

`on_cycle` runs every cycle. For a one-off action, compare `ctx.cycle`:

```python
def on_cycle(ctx):
    if ctx.cycle == 100:
        ctx.write("iram", 0x20, 0x5A)
```

Do not write a class, do not inherit from `Plugin`, do not use `self`.

### Context API

| Member | Meaning |
|---|---|
| `ctx.cycle` | current machine cycle |
| `ctx.pc` | current program counter |
| `ctx.read(space, addr)` | read one byte |
| `ctx.write(space, addr, value)` | write one byte |
| `ctx.get_reg(name)` / `ctx.set_reg(name, value)` | register access |
| `ctx.get_sfr(name_or_addr)` / `ctx.set_sfr(name_or_addr, value)` | SFR access |
| `ctx.drive_pin(port, bit, value)` | drive one external pin |
| `ctx.release_pin(port, mask)` | release external drive |
| `ctx.stop(reason)` | stop the session |

Memory spaces:

| Space | Meaning |
|---|---|
| `iram` | internal RAM, `0x00-0x7F` |
| `sfr` | SFR latch, `0x80-0xFF` |
| `direct` | direct address; port reads return pin level |
| `xdata` | external data memory, `0x0000-0xFFFF` |
| `code` | program memory, `0x0000-0xFFFF` |
| `bit` | bit address, `0x00-0xFF` |

Plugin writes take effect on the next machine cycle. Port latches and pin
levels are separate; `drive_pin` changes the external drive only and leaves
the latch unchanged.

## Tests

```bash
python3 -m unittest discover -s tests
python3 tools/check_data.py
python3 tools/check_form_coverage.py
```

## Scope and limitations

- 8051 base device; no 8052 Timer 2.
- No serial port simulation, baud rate, or serial interrupt requests.
- No S1-S6 phases; events occur at machine-cycle boundaries.
- Unimplemented SFR reads return `0xFF`; writes are ignored.
- Indirect access to `0x80-0xFF` is not internal RAM on the 8051.
- Cold reset clears RAM/XDATA for determinism; warm reset preserves them.
- No ALE/PSEN/RD/WR bus timing or electrical characteristics.

## License

MIT. See [LICENSE](LICENSE).
