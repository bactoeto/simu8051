# simu8051

A cycle-level Intel 8051 simulator written in Python. It loads Intel HEX
files produced by as31 and provides memory/register inspection, breakpoints,
and a plugin API for external stimuli.

## Status

- 255 valid opcodes; `0xA5` is reserved and traps.
- Timer 0/1, interrupts, ports, internal RAM, SFRs, external data memory.

## Requirements

Python 3.11 or newer. The simulator uses only the standard library.

No virtual environment is required. If one is preferred:

```bash
python3 -m venv .venv
.venv/bin/python -m simu8051 firmware.hex
```

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
session.start()
session.run(max_cycles=100_000)
```

### Commands

```text
run [n]                 run until a stop condition or n instructions
step [n]                execute n instructions
cycle [n]               execute n machine cycles
continue                continue after a breakpoint
break ADDR              set a PC breakpoint
break cycle N           set a cycle breakpoint
delete                  clear breakpoints
info break              list breakpoints
regs                    print common registers
sfr [NAME]              print SFRs
x SPACE ADDR [N]        dump memory (code/iram/sfr/direct/xdata/bit)
dis [ADDR] [N]          disassemble
reset [cold]            reset
load FILE               load another HEX file
display ...             add a persistent display item
undisplay N             remove a display item
disable N / enable N    toggle a display item
trace on|off            instruction trace
quit
```

The default persistent display is `regs`, printed after every execution
stop. Use `--no-default-display` to disable it.

## Layout

```text
simu8051/            simulator package
  cli.py             reference CLI (optional front end)
  __main__.py        entry point for `python3 -m simu8051`
  step.py            session, stepping, breakpoints, plugin dispatch
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

A plugin is a Python file registered with `--plugin`; repeat the flag to run
several plugins in command-line order.

The simplest form is a `setup(ctx)` function:

```python
from simu8051.plugin import PluginContext


def at_cycle_100(ctx: PluginContext) -> None:
    ctx.write("iram", 0x20, 0x5A)


def setup(ctx: PluginContext) -> None:
    ctx.schedule_at(100, at_cycle_100)
```

`PluginContext` is created by the simulator and passed to `setup`. The import
is for editor and type-checker support; it does not create the object.

A `Plugin` subclass is also accepted; implement any of the hooks below.

### Hooks

| Hook | Called |
|---|---|
| `setup(ctx)` | once, when the plugin is loaded |
| `on_start(ctx)` | once, when the session starts |
| `on_cycle(ctx)` | after every machine cycle |
| `on_instruction(ctx, text)` | after each completed instruction |
| `on_interrupt(ctx, name)` | when an interrupt is accepted |
| `on_stop(ctx, reason)` | when the run stops |

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
| `ctx.drive_port(port, mask, value)` | drive selected port pins |
| `ctx.release_pin(port, mask)` | release external drive |
| `ctx.schedule_at(cycle, callback)` | call `callback(ctx)` after a cycle |
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
