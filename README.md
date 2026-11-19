# sclgen: offline SCL block generator (GCCC template)

Turns a JSON block spec into a Siemens SCL `FUNCTION_BLOCK` built on the standard GCCC template.
Runs fully offline: Python 3.8+ standard library, no internet, no AI.

```
manufacturer info ──► any AI (online or local) ──► spec.json ──► sclgen.py ──► Block.scl ──► SCL compiler
                      using AI_INSTRUCTIONS.md       (review)     (offline)
```

## Use
```
python sclgen.py examples/pump_run_monitor.json            # writes PumpRunMon.scl next to the spec
python sclgen.py my_spec.json -o C:\Blocks\MyBlock.scl
python sclgen.py my_spec.json --check                        # validate only
```
Errors stop the build and nothing is written. Warnings (unused variables etc.) still build.

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
- `sclgen.py`: the generator
- `schema/block_spec.schema.json`: JSON Schema for the spec (give to the AI; Ollama/llama.cpp can enforce it)
- `AI_INSTRUCTIONS.md`: paste into the AI session with the manufacturer's info
- `examples/`: your template as a spec, plus a pump run-feedback monitor
- `output/`: generated .scl from the examples

## Packaging for site laptops without Python
`pip install pyinstaller` then `pyinstaller --onefile sclgen.py` gives a single `sclgen.exe`.
