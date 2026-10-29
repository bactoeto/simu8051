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

`cli.py` is only a reference front end. Every front end should use
`simu8051.session`; see [API.md](API.md). `core.py`, `hex.py`, `plugin.py`
and the generated tables are internal.

### Library

```python
from simu8051.session import Session

session = Session.open("firmware.hex")
session.break_at(0x0030)
result = session.run(max_cycles=100_000)
print(session.pc, session.get_reg("A"), result.reason)
```

[API.md](API.md) is the reference. `step()` returns `StepResult`; `run()`
returns `RunResult`; use `StopReason.kind` in code.

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
x SPACE ADDR [N]        dump; 8 per row, default 2 rows: i (internal RAM), x (external RAM), c (ROM)
w SPACE ADDR VALUE...   write memory: i, x, c (c patches the loaded image)
disassemble, d [ADDR] [N]  disassemble
reset, res [cold]       reset
load, l FILE            load another HEX file
display, dp ...         add a persistent display item
undisplay, und N        remove a display item
enable, on N            enable a display item
disable, off N          disable a display item
help, h, ?              show commands
quit, q, exit
```

The command list is printed when the interactive prompt starts. Type `help`
or `?` to show it again.

### Options

| Option | Meaning |
|---|---|
| `--plugin FILE` | load a plugin; repeatable |
| `--break ADDR` | set a PC breakpoint; repeatable |
| `--break cycle:N` | set a cycle breakpoint; repeatable |
| `--max-cycles N` | cycle limit for `--run` / `--batch` |
| `--run`, `--batch` | run without the REPL |
| `--display SPEC` | add a persistent display; repeatable |
| `--no-default-display` | do not print `regs` after stops |

`--display` accepts the same specifications as the `display` command:
`regs`, `reg NAME`, `sfr NAME`, `mem SPACE ADDR [N]` and
`bit ADDR [N]`. A dump always starts on an eight-byte row boundary; without
`N` it shows two rows.

Exit codes: `0` for a normal stop, `1` when execution hits a reserved
opcode, `2` for a startup error.

Memory is addressed as `i` (internal RAM, `0x00-0x7F`), `x` (external RAM,
`0x0000-0xFFFF`) and `c` (program memory, `0x0000-0xFFFF`). `w c` patches
program memory for debugging; real ROM cannot be written.

Unwritten code reads as erased `0xFF`, matching hardware. `x c` shows those
bytes, while `d` prints `NULL` for instructions whose bytes were not present
in the HEX file. `session.is_loaded(address)` exposes the same distinction.

Interactive mode keeps history in `~/.local/state/simu8051/history` (or
`$XDG_STATE_HOME/simu8051/history`), completes commands with Tab, and supports
Ctrl-L to clear the screen.

After every stop the CLI prints the current instruction at PC, then the
persistent displays. The default display is `regs`; use
`--no-default-display` to disable it.

## Layout

Only `simu8051/` is needed at runtime; the other directories are development
material.

```text
API.md               public API reference and contract
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

tools/               data extraction, build, validation, table generation
tests/               unit, CLI and public-API contract tests
tests/fixtures/      as31 HEX fixtures
examples/            plugin examples
docs/                source documents (local, not tracked)
build/               generated intermediates and reports (not tracked)
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

File plugins use module-level functions: do not write a class or use `self`.
From Python, load a plugin with `session.load_plugin("my_plugin.py")`.
Programmatic plugins may subclass `Plugin` and be registered with
`session.add_plugin()`. For editor support, annotate `ctx` as
`from simu8051.session import PluginContext`.

`ctx` exposes the cycle, PC, memory, registers, SFRs, pins and `stop()`.
See [API.md](API.md#plugins) for the exact members. Plugin writes take effect
on the next machine cycle.

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
