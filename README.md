# sclgen: offline SCL block generator (GCCC template)

Turns a JSON block spec into a Siemens SCL `FUNCTION_BLOCK` built on the standard GCCC template.
It is one HTML page, `sclgen.html`: double-click it and it opens in any browser. No install,
no Python, no internet, no AI. Nothing you type leaves the page.

```
manufacturer info ──► any AI (online or local) ──► spec.json ──► sclgen.html ──► Block.scl ──► SCL compiler
                      using "Copy AI instructions"   (review)     (offline)
```

## Use
1. Open `sclgen.html`. Copy it to a site laptop or USB stick as it is; it is self-contained.
2. **Copy AI instructions** puts `AI_INSTRUCTIONS.md` and the schema on the clipboard. Paste them into
   any AI chat with the manufacturer's info. The AI writes only the JSON spec.
3. Paste the spec into the left pane, or **Open spec…**, or drop a `.json` file on the page.
   **Load example…** has the two specs in `examples/`.
4. The page checks the spec as you type. Errors stop the build. Warnings (unused variables etc.)
   still build. Click a problem to jump to it in the spec.
5. **Download** saves `<BlockName>.scl` with CRLF line endings for SIMATIC Manager or TIA import.
   **Save spec** saves the JSON. The last spec is also kept in the browser between visits.

## What the generator does for you (the rules)
| Part | Source |
|---|---|
| Header, attributes, AUTHOR/FAMILY/NAME/VERSION, KNOW_HOW_PROTECT | `block` |
| VAR_INPUT / VAR_OUTPUT / VAR_IN_OUT / VAR with S7 attribute presets | variable lists |
| Section 1: enable/reset (outputs and statics back to initial values, timers cleared, `RETURN`) | generated |
| Section 2: timer presets every scan, input presets clamped 0..86400 s | `timers` |
| Sections 3+: application logic | `logic_sections` (with `{timer:id}` resolved to `tmTimers[n]`) |
| State IF/ELSIF chain + status CASE with fail-safe ELSE | `state_machine` |
| Timer array engine (FOR loop, SampleTime based) | generated |

## Checks before writing
Duplicate or reserved names (case-insensitive), types and initial values, header field lengths,
enable input is a BOOL input, SampleTime present when timers exist, timer presets valid,
`{timer:id}` references exist, IF/CASE/FOR/WHILE balanced, `:=` used inside an IF condition,
undeclared identifiers, `.PT` set in logic, state values unique, status text fits the STRING,
unused declarations.

It does **not** compile SCL. Always compile the output in STEP 7 / PCS 7 / TIA and test it.

## Fixes compared with the emailed template
1. `IF InternalReal1 > REAL := INT_TO_REAL(IntInput1) THEN` would not compile, and compared the
   process value against the *scan count*. Replaced with a separate `RealSetpoint1` input.
2. The RESET clearing block sat **after** `END_FOR`, so it only ran once with whatever `tiIndex` was
   left at after the loop, not for each timer. The on delay's RESET (set when the output turns on)
   was never cleared, so the output could turn on once and never again until a disable/enable.
   RESET is now handled inside the timer loop.
3. The `IntOutput1` comment said 0..2 but the logic used 0..3.
4. On/off delay presets were hard-coded (5.0 / 3.0) in the logic; now inputs `OnDelayTime` / `OffDelayTime`.
5. The scan counter could overflow at 32767; now capped.
6. `InternalBool1` was declared but never used; removed.

## Files
- `sclgen.html`: the generator (open it in a browser)
- `schema/block_spec.schema.json`: JSON Schema for the spec (give to the AI; Ollama/llama.cpp can enforce it)
- `AI_INSTRUCTIONS.md`: paste into the AI session with the manufacturer's info
- `examples/`: your template as a spec, plus a pump run-feedback monitor
- `output/`: generated .scl from the examples

- `tests/`: checks for the generator (needs Node.js, only for whoever changes `sclgen.html`)

## Changing the generator
The generator code sits between `/* ENGINE START */` and `/* ENGINE END */` in `sclgen.html`.
The page also embeds copies of `AI_INSTRUCTIONS.md`, the schema and the examples. After editing
any of them run:
```
node tests/test.js --fix   # refresh the embedded copies
node tests/test.js         # examples must still regenerate byte for byte to output/
```
`tests/cases/` holds broken specs with the exact messages expected for each. They were recorded
from the original Python `sclgen.py` (in git history, commit f4157fb), which the HTML version
replaced on 19 Nov 2026. It was checked against the Python on 6,000 deliberately broken specs, checking
and generating each, with identical messages and output. The one deliberate difference: a name, type or version with a trailing line
break (`"Enable\n"`) is now rejected; Python's pattern matching let it through.

For batch use without the browser: `node tests/cli.js spec.json [-o out.scl] [--check]`.
