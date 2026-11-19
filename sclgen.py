#!/usr/bin/env python3
"""
sclgen.py - Offline SCL function block generator (GCCC template style)

Reads a JSON block spec, validates it, and writes a Siemens SCL FUNCTION_BLOCK
built on the standard template:

    header + attributes
    VAR_INPUT / VAR_OUTPUT / VAR_IN_OUT / VAR (+ timer array)
    SECTION 1  block enable and reset            (generated)
    SECTION 2  timer presets                      (generated)
    SECTION 3+ application logic                  (from spec)
    SECTION n  state and status                   (generated)
    Timer execution engine                        (generated)

No internet, no AI and no third-party packages needed: Python 3.8+ standard library only.

Usage:
    python sclgen.py spec.json                 -> writes <BlockName>.scl next to the spec
    python sclgen.py spec.json -o out.scl      -> writes to out.scl
    python sclgen.py spec.json --check         -> validate only, write nothing

Exit code 0 = OK (warnings allowed), 1 = errors found, nothing written.
"""

import argparse
import json
import re
import sys
from pathlib import Path

GENERATOR_VERSION = "1.0"
SPEC_VERSIONS = {"1.0"}

SEP = "// " + "-" * 89
STAR = "//" + "*" * 92

# ---------------------------------------------------------------------------
# Attribute presets (PCS 7 / STEP 7 system attributes)
# ---------------------------------------------------------------------------
ATTR_PRESETS = {
    "none": {},
    "hmi": {"S7_m_c": "true"},
    "param": {"S7_m_c": "true", "S7_param": "true", "S7_read_back": "always", "S7_link": "false"},
    "sampletime": {"S7_visible": "false", "S7_sampletime": "true", "S7_param": "false", "S7_link": "false"},
}

ELEMENTARY = {"BOOL", "BYTE", "WORD", "DWORD", "INT", "DINT", "REAL",
              "TIME", "S5TIME", "DATE", "TIME_OF_DAY", "TOD", "CHAR"}
STRING_RE = re.compile(r"^STRING\[(\d+)\]$")
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TIMER_REF_RE = re.compile(r"\{timer:([A-Za-z_][A-Za-z0-9_]*)\}")

RESERVED = {
    "AND", "ANY", "ARRAY", "BEGIN", "BLOCK_DB", "BLOCK_FB", "BLOCK_FC", "BLOCK_SDB", "BOOL", "BY",
    "BYTE", "CASE", "CHAR", "CONST", "CONTINUE", "COUNTER", "DATA_BLOCK", "DATE", "DINT", "DIV",
    "DO", "DT", "DWORD", "ELSE", "ELSIF", "EN", "ENO", "END_CASE", "END_CONST", "END_DATA_BLOCK",
    "END_FOR", "END_FUNCTION", "END_FUNCTION_BLOCK", "END_IF", "END_LABEL", "END_ORGANIZATION_BLOCK",
    "END_REPEAT", "END_STRUCT", "END_TYPE", "END_VAR", "END_WHILE", "EXIT", "FALSE", "FOR",
    "FUNCTION", "FUNCTION_BLOCK", "GOTO", "IF", "INT", "LABEL", "MOD", "NIL", "NOT", "OF", "OK",
    "OR", "ORGANIZATION_BLOCK", "POINTER", "REAL", "REPEAT", "RETURN", "S5TIME", "STRING",
    "STRUCT", "THEN", "TIME", "TIMER", "TIME_OF_DAY", "TO", "TOD", "TRUE", "TYPE", "UNTIL",
    "VAR", "VAR_IN_OUT", "VAR_INPUT", "VAR_OUTPUT", "VAR_TEMP", "VOID", "WHILE", "WORD", "XOR",
}
KNOWN_FUNCS = {
    "LIMIT", "MIN", "MAX", "ABS", "SQR", "SQRT", "EXP", "EXPD", "LN", "LOG", "SIN", "COS", "TAN",
    "ASIN", "ACOS", "ATAN", "TRUNC", "ROUND", "SEL", "MUX", "SHL", "SHR", "ROL", "ROR", "LEN",
    "CONCAT", "LEFT", "RIGHT", "MID", "INSERT", "DELETE", "REPLACE", "FIND",
}
CONVERSION_RE = re.compile(r"^[A-Z_]+_TO_[A-Z_]+$")

# Names the generator itself declares
TIMER_ARRAY = "tmTimers"
TIMER_INDEX = "tiIndex"
TIMER_FIELDS = {"IN", "Q", "RESET", "PT", "ET", "RT"}

MAX_TIMER_PRESET_S = 86400.0  # clamp for presets taken from inputs (24 h)


class SpecError(Exception):
    pass


class Report:
    def __init__(self):
        self.errors, self.warnings = [], []

    def err(self, where, msg):
        self.errors.append(f"ERROR   {where}: {msg}")

    def warn(self, where, msg):
        self.warnings.append(f"WARNING {where}: {msg}")


# ---------------------------------------------------------------------------
# Value formatting
# ---------------------------------------------------------------------------
def fmt_real(v):
    f = float(v)
    if f != f or f in (float("inf"), float("-inf")):
        raise ValueError("REAL must be finite")
    s = repr(f)
    if "e" in s or "E" in s:
        mant, exp = s.lower().split("e")
        if "." not in mant:
            mant += ".0"
        return f"{mant}E{exp}"
    return s if "." in s else s + ".0"


def scl_string(s):
    return "'" + str(s).replace("$", "$$").replace("'", "$'") + "'"


def default_value(vtype):
    if vtype == "BOOL":
        return "FALSE"
    if vtype == "REAL":
        return "0.0"
    if vtype in ("INT", "DINT"):
        return "0"
    if vtype in ("BYTE", "WORD", "DWORD"):
        return "0"
    if vtype == "TIME":
        return "T#0MS"
    if STRING_RE.match(vtype):
        return "''"
    return None


def fmt_value(vtype, value):
    """Return SCL literal for value of vtype, or raise ValueError."""
    if vtype == "BOOL":
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, str) and value.upper() in ("TRUE", "FALSE"):
            return value.upper()
        raise ValueError("BOOL needs true/false")
    if vtype == "REAL":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("REAL needs a number")
        return fmt_real(value)
    if vtype in ("INT", "DINT"):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{vtype} needs a whole number")
        lo, hi = (-32768, 32767) if vtype == "INT" else (-2147483648, 2147483647)
        if not lo <= value <= hi:
            raise ValueError(f"{vtype} out of range {lo}..{hi}")
        return str(value)
    m = STRING_RE.match(vtype)
    if m:
        if not isinstance(value, str):
            raise ValueError("STRING needs text")
        if len(value) > int(m.group(1)):
            raise ValueError(f"text longer than {vtype}")
        return scl_string(value)
    if vtype == "TIME":
        if not (isinstance(value, str) and re.match(r"^T#[0-9DHMSdhms_]+$", value)):
            raise ValueError("TIME needs a literal like 'T#5S'")
        return value.upper()
    # BYTE/WORD/DWORD/CHAR/etc: accept plain ints or literal strings
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        return value
    raise ValueError(f"unsupported initial value for {vtype}")


def fmt_attrs(attrs):
    if not attrs:
        return ""
    return " {" + "; ".join(f"{k}:={scl_string(v)}" for k, v in attrs.items()) + "}"


# ---------------------------------------------------------------------------
# SCL text helpers for linting
# ---------------------------------------------------------------------------
def strip_comments_and_strings(code):
    code = re.sub(r"\(\*.*?\*\)", " ", code, flags=re.S)
    code = re.sub(r"//[^\n]*", " ", code)
    code = re.sub(r"'(?:\$.|[^'$])*'", "''", code)
    return code


def find_identifiers(code):
    """Identifiers that should be declared variables (very lightweight lexer)."""
    c = strip_comments_and_strings(code)
    c = re.sub(r"\b[A-Za-z0-9_]+#[A-Za-z0-9_.:\-]+", " ", c)       # T#5S, 16#FF, DINT#5
    c = re.sub(r"([(,]\s*)[A-Za-z_][A-Za-z0-9_]*\s*:=", r"\1", c)    # named params MN:=
    c = re.sub(r"\.\s*[A-Za-z_][A-Za-z0-9_]*", " ", c)               # .FIELD
    c = re.sub(r"\b\d+(\.\d+)?([eE][+-]?\d+)?\b", " ", c)              # numbers
    found = set()
    for tok in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", c):
        u = tok.upper()
        if u in RESERVED or u in KNOWN_FUNCS or CONVERSION_RE.match(u):
            continue
        found.add(tok)
    return found


def check_balance(code, where, rep):
    c = strip_comments_and_strings(code).upper()
    pairs = [("IF", "END_IF"), ("CASE", "END_CASE"), ("FOR", "END_FOR"),
             ("WHILE", "END_WHILE"), ("REPEAT", "END_REPEAT")]
    for open_kw, close_kw in pairs:
        opens = len(re.findall(rf"(?<![A-Z_]){open_kw}(?![A-Z_])", c))
        closes = len(re.findall(rf"(?<![A-Z_]){close_kw}(?![A-Z_])", c))
        if opens != closes:
            rep.err(where, f"{open_kw} opened {opens} time(s) but {close_kw} appears {closes} time(s)")


def lint_snippet(code, where, declared_lower, rep, is_condition=False):
    c = strip_comments_and_strings(code)
    if not is_condition:
        check_balance(code, where, rep)
        # statements should end with ';' - catch the commonest slip: assignment line without ';'
        for n, line in enumerate(c.splitlines(), 1):
            s = line.strip()
            if ":=" in s and not s.endswith((";", "THEN", "DO", "ELSE", "OF", ",", "(", ":=")) \
                    and not re.search(r"\b(THEN|DO|ELSE)\b", s, re.I):
                rep.warn(where, f"line {n} looks like an assignment without ';': {s[:60]}")
    else:
        if ";" in c:
            rep.err(where, "a condition must be a single expression (no ';')")
    # assignment inside a comparison, e.g.  "IF x > REAL := y THEN"
    for m in re.finditer(r"\bIF\b(.*?)\bTHEN\b", c, flags=re.I | re.S):
        cond = re.sub(r"\([^()]*\)", "()", m.group(1))  # ignore function-call params
        if ":=" in cond:
            rep.err(where, f"':=' (assignment) inside IF condition: IF{m.group(1).strip()[:60]}... use '=' or fix the expression")
    if is_condition and ":=" in re.sub(r"\([^()]*\)", "()", c):
        rep.err(where, "':=' (assignment) inside a condition; use '=' for comparison")
    if re.search(r"\.\s*PT\s*:=", c, flags=re.I):
        rep.warn(where, "logic sets a timer .PT directly; put presets in the spec's 'timers' instead")
    for ident in sorted(find_identifiers(code)):
        if ident.lower() not in declared_lower:
            rep.err(where, f"'{ident}' is used but not declared")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate(spec, rep):
    if not isinstance(spec, dict):
        raise SpecError("spec must be a JSON object")
    sv = str(spec.get("spec_version", ""))
    if sv not in SPEC_VERSIONS:
        rep.err("spec_version", f"must be one of {sorted(SPEC_VERSIONS)}, got '{sv}'")

    blk = spec.get("block")
    if not isinstance(blk, dict):
        rep.err("block", "missing block section")
        return
    for key in ("name", "title", "author", "family", "short_name", "version"):
        if not isinstance(blk.get(key), str) or not blk.get(key):
            rep.err(f"block.{key}", "required text")
    if isinstance(blk.get("name"), str) and not IDENT_RE.match(blk["name"]):
        rep.err("block.name", "must be letters, digits, underscore and not start with a digit")
    for key, limit in (("short_name", 8), ("author", 8), ("family", 8)):
        if isinstance(blk.get(key), str) and len(blk[key]) > limit:
            rep.err(f"block.{key}", f"max {limit} characters in STEP 7 block headers")
    if isinstance(blk.get("version"), str) and not re.match(r"^\d{1,2}\.\d{1,2}$", blk["version"]):
        rep.err("block.version", "must look like '1.0' (0.0 to 15.15)")
    if blk.get("type", "FUNCTION_BLOCK") != "FUNCTION_BLOCK":
        rep.err("block.type", "only FUNCTION_BLOCK is supported by this template")

    # ---- variables ----
    seen = {}
    sections = (("inputs", "input"), ("outputs", "output"), ("in_outs", "in_out"), ("statics", "static"))
    for sec, kind in sections:
        items = spec.get(sec, [])
        if not isinstance(items, list):
            rep.err(sec, "must be a list")
            continue
        for i, v in enumerate(items):
            where = f"{sec}[{i}]"
            if not isinstance(v, dict):
                rep.err(where, "must be an object")
                continue
            name = v.get("name")
            if not isinstance(name, str) or not IDENT_RE.match(name):
                rep.err(where, f"invalid name '{name}'")
                continue
            where = f"{sec}.{name}"
            if name.upper() in RESERVED or name.upper() in KNOWN_FUNCS:
                rep.err(where, "name is an SCL reserved word or standard function")
            if len(name) > 24:
                rep.warn(where, "name longer than 24 characters (STEP 7 V5 symbol limit)")
            if name.lower() in seen:
                rep.err(where, f"duplicate name (SCL is not case-sensitive; clashes with {seen[name.lower()]})")
            seen[name.lower()] = where
            if name.lower() in (TIMER_ARRAY.lower(), TIMER_INDEX.lower()):
                rep.err(where, f"'{name}' is reserved for the timer engine")
            vtype = v.get("type")
            m = STRING_RE.match(vtype) if isinstance(vtype, str) else None
            if not isinstance(vtype, str) or (vtype not in ELEMENTARY and not m):
                rep.err(where, f"unsupported type '{vtype}'")
                continue
            if m and not 1 <= int(m.group(1)) <= 254:
                rep.err(where, "STRING length must be 1..254")
            if not v.get("comment"):
                rep.warn(where, "no comment; every variable should say what it is for")
            for key in ("initial", "reset_value"):
                if key in v:
                    try:
                        fmt_value(vtype, v[key])
                    except ValueError as e:
                        rep.err(f"{where}.{key}", str(e))
            preset = v.get("attr_preset", "none")
            if preset not in ATTR_PRESETS:
                rep.err(where, f"attr_preset must be one of {list(ATTR_PRESETS)}")
            if kind == "static" and (preset != "none" or v.get("attributes")):
                rep.warn(where, "attributes on static variables are ignored")
            if "attributes" in v and not isinstance(v["attributes"], dict):
                rep.err(where, "attributes must be an object of name: value")

    declared = {k: v for k, v in seen.items()}

    def var(name):
        for sec, _ in sections:
            for v in spec.get(sec, []) or []:
                if isinstance(v, dict) and str(v.get("name", "")).lower() == str(name).lower():
                    return sec, v
        return None, None

    # ---- enable input ----
    en = spec.get("enable_input")
    sec, v = var(en)
    if sec != "inputs" or v.get("type") != "BOOL":
        rep.err("enable_input", f"'{en}' must be a BOOL in inputs")

    # ---- timers ----
    timers = spec.get("timers", [])
    tids = set()
    if not isinstance(timers, list):
        rep.err("timers", "must be a list")
        timers = []
    for i, t in enumerate(timers):
        tid = t.get("id") if isinstance(t, dict) else None
        where = f"timers[{i}]"
        if not isinstance(tid, str) or not IDENT_RE.match(tid):
            rep.err(where, "id must be a simple identifier, e.g. on_delay")
            continue
        where = f"timers.{tid}"
        if tid.lower() in tids:
            rep.err(where, "duplicate timer id")
        tids.add(tid.lower())
        p = t.get("preset")
        if not isinstance(p, dict) or len(p) != 1 or not ({"seconds", "from"} & set(p)):
            rep.err(where, "preset must be {\"seconds\": 5.0} or {\"from\": \"InputName\"}")
            continue
        if "seconds" in p:
            s = p["seconds"]
            if isinstance(s, bool) or not isinstance(s, (int, float)) or s <= 0:
                rep.err(where, "preset seconds must be a number > 0")
        else:
            psec, pv = var(p["from"])
            if not pv:
                rep.err(where, f"preset source '{p['from']}' is not declared")
            elif pv.get("type") != "REAL":
                rep.err(where, f"preset source '{p['from']}' must be REAL seconds")
    if timers:
        st = spec.get("sample_time_input", "SampleTime")
        ssec, sv_ = var(st)
        if not sv_:
            rep.err("sample_time_input", f"timers need a REAL input '{st}' (PLC cycle time in seconds)")
        elif ssec != "inputs" or sv_.get("type") != "REAL":
            rep.err("sample_time_input", f"'{st}' must be a REAL input")

    # ---- status / state ----
    sm = spec.get("state_machine")
    if not isinstance(sm, dict):
        rep.err("state_machine", "missing")
    else:
        so, sov = var(sm.get("status_output"))
        if so != "outputs" or not STRING_RE.match(str(sov.get("type"))):
            rep.err("state_machine.status_output", "must name a STRING output")
        stv_sec, stv = var(sm.get("state_output"))
        if stv_sec != "outputs" or stv.get("type") != "INT":
            rep.err("state_machine.state_output", "must name an INT output")
        strlen = int(STRING_RE.match(sov["type"]).group(1)) if sov and STRING_RE.match(str(sov.get("type"))) else 254
        states = sm.get("states", [])
        if not isinstance(states, list) or not states:
            rep.err("state_machine.states", "need at least one state")
            states = []
        vals = set()
        for i, s in enumerate(states):
            where = f"state_machine.states[{i}]"
            if not isinstance(s, dict):
                rep.err(where, "must be an object")
                continue
            val = s.get("value")
            if isinstance(val, bool) or not isinstance(val, int) or not 0 <= val <= 32767:
                rep.err(where, "value must be an INT 0..32767")
            elif val in vals:
                rep.err(where, f"duplicate state value {val}")
            vals.add(val)
            for key in ("name", "condition", "status"):
                if not isinstance(s.get(key), str) or not s.get(key).strip():
                    rep.err(where, f"{key} is required")
            if isinstance(s.get("condition"), str) and s["condition"].strip().upper() == "ELSE" \
                    and (i != len(states) - 1 or i == 0):
                rep.err(where, "condition 'ELSE' is only allowed on the last state (and not the only one)")
            if isinstance(s.get("status"), str) and len(s["status"]) > strlen:
                rep.err(where, f"status text longer than status output (max {strlen})")
        sv0 = sm.get("safe_value", 0)
        if vals and sv0 not in vals:
            rep.err("state_machine.safe_value", f"{sv0} is not one of the state values")
        for key, default in (("disabled_status", "Disabled"), ("unknown_status", "Error/Unknown")):
            txt = sm.get(key, default)
            if not isinstance(txt, str) or len(txt) > strlen:
                rep.err(f"state_machine.{key}", f"must be text up to {strlen} chars")

    # ---- logic sections ----
    ls = spec.get("logic_sections", [])
    if not isinstance(ls, list) or not ls:
        rep.err("logic_sections", "need at least one logic section")
        ls = []
    for i, s in enumerate(ls):
        where = f"logic_sections[{i}]"
        if not isinstance(s, dict) or not isinstance(s.get("scl"), str):
            rep.err(where, "needs 'title' and 'scl' text")
            continue
        if not s.get("title"):
            rep.err(where, "title required")
        if re.search(r"\bRETURN\b", strip_comments_and_strings(s["scl"]), re.I):
            rep.warn(where, "RETURN in logic skips the state/status and timer sections that follow")

    if rep.errors:
        return  # don't lint code against a broken declaration set

    declared_lower = set(declared) | {TIMER_ARRAY.lower(), TIMER_INDEX.lower()}

    def check_timer_refs(text, where):
        for ref in TIMER_REF_RE.findall(text):
            if ref.lower() not in tids:
                rep.err(where, f"{{timer:{ref}}} does not match any timer id")

    for i, s in enumerate(ls):
        where = f"logic_sections[{i}] '{s.get('title')}'"
        check_timer_refs(s["scl"], where)
        lint_snippet(resolve_timers(s["scl"], timers), where, declared_lower, rep)
    for i, s in enumerate(sm.get("states", [])):
        if s["condition"].strip().upper() == "ELSE":
            continue
        where = f"state_machine.states[{i}] '{s.get('name')}'"
        check_timer_refs(s["condition"], where)
        lint_snippet(resolve_timers(s["condition"], timers), where, declared_lower, rep, is_condition=True)

    # unused declarations
    all_code = " ".join(resolve_timers(s["scl"], timers) for s in ls) + " " + \
               " ".join(resolve_timers(s["condition"], timers) for s in sm.get("states", [])
                         if s["condition"].strip().upper() != "ELSE")
    used = {x.lower() for x in find_identifiers(all_code)}
    used |= {str(spec.get("enable_input", "")).lower(),
             str(sm.get("status_output", "")).lower(), str(sm.get("state_output", "")).lower(),
             str(spec.get("sample_time_input", "SampleTime")).lower()}
    used |= {str(t["preset"].get("from", "")).lower() for t in timers}
    for sec in ("inputs", "in_outs", "statics"):
        for v in spec.get(sec, []):
            if v["name"].lower() not in used:
                rep.warn(f"{sec}.{v['name']}", "declared but never used in logic")


def resolve_timers(text, timers):
    idx = {t["id"].lower(): i for i, t in enumerate(timers)}
    return TIMER_REF_RE.sub(lambda m: f"{TIMER_ARRAY}[{idx.get(m.group(1).lower(), '?')}]", text)


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------
def indent(text, n=1):
    pad = "    " * n
    return "\n".join((pad + ln if ln.strip() else "") for ln in text.rstrip("\n").splitlines())


def section_header(num, title, description):
    out = [SEP, f"// SECTION {num}: {title.upper()}"]
    for line in description or []:
        out.append(f"// {line}" if line else "//")
    out.append(SEP)
    return "\n".join(out)


def decl_line(v, with_attrs=True):
    attrs = {}
    if with_attrs:
        attrs = dict(ATTR_PRESETS[v.get("attr_preset", "none")])
        attrs.update(v.get("attributes", {}) or {})
    init = ""
    if "initial" in v:
        init = f" := {fmt_value(v['type'], v['initial'])}"
    comment = f" // {v['comment']}" if v.get("comment") else ""
    return f"    {v['name']}{fmt_attrs(attrs)} : {v['type']}{init};{comment}"


def generate(spec):
    blk = spec["block"]
    timers = spec.get("timers", [])
    sm = spec["state_machine"]
    st_name = spec.get("sample_time_input", "SampleTime")
    n_t = len(timers)
    L = []

    # ---- header ----
    L.append(f'FUNCTION_BLOCK "{blk["name"]}"')
    L.append(f"TITLE = {scl_string(blk['title'])}")
    battrs = blk.get("attributes", {"S7_m_c": "true", "S7_tasklist": "OB100"})
    if battrs:
        L.append("{")
        L.append(";\n".join(f"{k}:={scl_string(v)}" for k, v in battrs.items()))
        L.append("}")
    L.append(f"AUTHOR : {scl_string(blk['author'])}")
    L.append(f"FAMILY : {scl_string(blk['family'])}")
    L.append(f"NAME : {scl_string(blk['short_name'])}")
    L.append(f"VERSION : {scl_string(blk['version'])}")
    if blk.get("know_how_protect", True):
        L.append("KNOW_HOW_PROTECT")
    L.append("")
    L.append(f"// Purpose : {blk.get('purpose', blk['title'])}")
    if blk.get("how_it_works"):
        L.append("//")
        L.append("// How it works:")
        for line in blk["how_it_works"]:
            L.append(f"// {line}")
    L.append("//")
    L.append(f"// Generated by sclgen {GENERATOR_VERSION} from a JSON block spec. Edit the spec and regenerate")
    L.append("// rather than editing this file by hand.")
    L.append("")

    # ---- declarations ----
    for sec, kw in (("inputs", "VAR_INPUT"), ("outputs", "VAR_OUTPUT"), ("in_outs", "VAR_IN_OUT")):
        items = spec.get(sec, [])
        if items:
            L.append(kw)
            L.extend(decl_line(v) for v in items)
            L.append("END_VAR")
            L.append("")

    L.append("VAR")
    L.append("    // Internal working variables")
    L.extend(decl_line(v, with_attrs=False) for v in spec.get("statics", []))
    if n_t:
        idx_doc = ", ".join(f"index {i} = {t['id']}" for i, t in enumerate(timers))
        L.append("")
        L.append(f"    {TIMER_ARRAY} : ARRAY[0..{n_t - 1}] OF STRUCT // Timer array, {idx_doc}")
        L.append("        IN : BOOL; // Timer input, TRUE starts the timer accumulating")
        L.append("        Q : BOOL; // Timer output, TRUE when ET reaches PT, stays TRUE while IN is held")
        L.append("        RESET : BOOL := FALSE; // Set TRUE to clear ET/RT and hold Q FALSE for one scan, engine clears it")
        L.append("        PT : REAL; // Preset time in seconds, written every scan in the timer presets section")
        L.append("        ET : REAL; // Elapsed time in seconds, incremented each scan by " + st_name)
        L.append("        RT : REAL; // Remaining time in seconds, PT minus ET, clamped to zero")
        L.append("    END_STRUCT;")
        L.append(f"    {TIMER_INDEX} : INT; // Loop index for the timer execution FOR loop")
    L.append("END_VAR")
    L.append("")
    L.append("BEGIN")
    L.append("")

    sec_no = 1

    # ---- SECTION 1: enable / reset ----
    en = spec["enable_input"]
    L.append(section_header(sec_no, "Block enable and reset", [
        f"If {en} is FALSE, everything is reset to a clean idle state and the block exits.",
        "This ensures the block always starts fresh when re-enabled after being disabled.",
    ]))
    sec_no += 1
    body = []
    for sec in ("outputs", "statics", "in_outs"):
        default_reset = sec != "in_outs"
        for v in spec.get(sec, []):
            if v["name"] in (sm["status_output"], sm["state_output"]):
                continue
            if not v.get("reset_on_disable", default_reset):
                continue
            if "reset_value" in v:
                val = fmt_value(v["type"], v["reset_value"])
            elif "initial" in v:
                val = fmt_value(v["type"], v["initial"])
            else:
                val = default_value(v["type"])
            if val is None:
                continue
            body.append(f"{v['name']} := {val}; // Back to initial value")
    body.append(f"{sm['state_output']} := {sm.get('safe_value', 0)}; // Return to safe state")
    body.append(f"{sm['status_output']} := {scl_string(sm.get('disabled_status', 'Disabled'))};")
    if n_t:
        body.append(f"FOR {TIMER_INDEX} := 0 TO {n_t - 1} DO // Stop and clear every timer")
        for f, val in (("IN", "FALSE"), ("Q", "FALSE"), ("RESET", "FALSE"), ("ET", "0.0"), ("RT", "0.0")):
            body.append(f"    {TIMER_ARRAY}[{TIMER_INDEX}].{f} := {val};")
        body.append("END_FOR;")
    body.append("RETURN; // Exit the block early, skip all logic below")
    L.append(f"IF NOT {en} THEN")
    L.append(indent("\n".join(body)))
    L.append("END_IF;")
    L.append("")

    # ---- SECTION 2: timer presets ----
    if n_t:
        L.append(section_header(sec_no, "Timer presets", [
            "Presets are written every scan so changes from the HMI or engineering take effect immediately.",
            "Presets taken from inputs are clamped to 0..86400 s so a bad entry cannot break the timer.",
        ]))
        sec_no += 1
        for i, t in enumerate(timers):
            p = t["preset"]
            note = t.get("comment", t["id"])
            if "seconds" in p:
                L.append(f"{TIMER_ARRAY}[{i}].PT := {fmt_real(p['seconds'])}; // {t['id']}: {note}")
            else:
                L.append(f"{TIMER_ARRAY}[{i}].PT := LIMIT(MN:=0.0, IN:={p['from']}, MX:={fmt_real(MAX_TIMER_PRESET_S)}); // {t['id']}: {note}")
        L.append("")

    # ---- application logic ----
    for s in spec["logic_sections"]:
        L.append(section_header(sec_no, s["title"], s.get("description")))
        sec_no += 1
        L.append(resolve_timers(s["scl"].rstrip(), timers))
        L.append("")

    # ---- state and status ----
    so, stv = sm["status_output"], sm["state_output"]
    L.append(section_header(sec_no, "State and status", [
        f"{stv} gives a simple integer state that can be trended or alarmed on the HMI.",
        f"{so} gives a plain English description for diagnostics on the operator screen.",
        "States are tested in priority order; the first matching condition wins.",
    ]))
    states = sm["states"]
    for i, s in enumerate(states):
        if s["condition"].strip().upper() == "ELSE":
            L.append("ELSE")
        else:
            kw = "IF" if i == 0 else "ELSIF"
            L.append(f"{kw} {resolve_timers(s['condition'].strip(), timers)} THEN")
        L.append(f"    {stv} := {s['value']}; // {s['name']}")
    L.append("END_IF;")
    L.append("")
    L.append(f"CASE {stv} OF")
    for s in sorted(states, key=lambda x: x["value"]):
        L.append(f"    {s['value']}: // {s['name']}")
        L.append(f"        {so} := {scl_string(s['status'])};")
    L.append("    ELSE // Unexpected state, fail safe")
    L.append(f"        {so} := {scl_string(sm.get('unknown_status', 'Error/Unknown'))};")
    L.append(f"        {stv} := {sm.get('safe_value', 0)}; // Force back to safe state so block recovers automatically")
    L.append("END_CASE;")
    L.append("")

    # ---- timer engine ----
    if n_t:
        L.append(STAR)
        L.append("// Timers execution")
        L.append("// Runs every timer in the array with a single FOR loop; each element works identically.")
        L.append(f"// {st_name} is added to ET each scan the timer IN is active. When ET reaches PT, RT reaches")
        L.append("// zero and Q goes TRUE. Q stays TRUE while IN is held. RESET clears the timer for one scan")
        L.append("// and is then cleared by the engine itself, so the timer is ready for its next cycle.")
        L.append(STAR)
        t = f"{TIMER_ARRAY}[{TIMER_INDEX}]"
        L.append(f"FOR {TIMER_INDEX} := 0 TO {n_t - 1} DO")
        L.append(f"    IF {t}.RESET THEN")
        L.append(f"        {t}.ET := 0.0; // Clear elapsed time")
        L.append(f"        {t}.RT := 0.0; // Clear remaining time")
        L.append(f"        {t}.Q := FALSE; // Hold output off while resetting")
        L.append(f"        {t}.RESET := FALSE; // Reset handled, clear the flag so the timer can run again")
        L.append(f"    ELSIF {t}.IN THEN")
        L.append(f"        IF NOT {t}.Q THEN")
        L.append(f"            {t}.ET := {t}.ET + {st_name}; // Accumulate elapsed time")
        L.append("        END_IF;")
        L.append(f"        {t}.RT := LIMIT(MN:=0.0, IN:={t}.PT - {t}.ET, MX:={t}.PT); // Remaining time, never below 0")
        L.append(f"        {t}.Q := {t}.RT <= 0.0; // Done when no time remains")
        L.append("    ELSE")
        L.append(f"        {t}.ET := 0.0; // Not running, clear elapsed time")
        L.append(f"        {t}.RT := 0.0; // Not running, clear remaining time")
        L.append(f"        {t}.Q := FALSE; // Not running, output off")
        L.append("    END_IF;")
        L.append("END_FOR;")
        L.append("")

    L.append("END_FUNCTION_BLOCK")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Offline SCL function block generator")
    ap.add_argument("spec", help="JSON block spec file")
    ap.add_argument("-o", "--output", help="output .scl file (default: <BlockName>.scl next to spec)")
    ap.add_argument("--check", action="store_true", help="validate only")
    a = ap.parse_args(argv)

    path = Path(a.spec)
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR   {path.name}: not valid JSON (line {e.lineno}, col {e.colno}): {e.msg}")
        return 1
    except OSError as e:
        print(f"ERROR   {e}")
        return 1

    rep = Report()
    try:
        validate(spec, rep)
    except SpecError as e:
        rep.err("spec", str(e))
    for line in rep.errors + rep.warnings:
        print(line)
    if rep.errors:
        print(f"\n{len(rep.errors)} error(s), {len(rep.warnings)} warning(s). Nothing written.")
        return 1

    code = generate(spec)
    # final structural sanity check on the generated block
    rep2 = Report()
    check_balance(code, "generated block", rep2)
    if rep2.errors:
        for line in rep2.errors:
            print(line)
        print("Internal error: generated block is unbalanced. Nothing written.")
        return 1

    if a.check:
        print(f"OK: spec valid, {len(rep.warnings)} warning(s).")
        return 0
    out = Path(a.output) if a.output else path.with_name(f"{spec['block']['name']}.scl")
    with open(out, "w", encoding="utf-8", newline="\r\n") as fh:  # CRLF for SIMATIC Manager / TIA import
        fh.write(code)
    print(f"OK: wrote {out} ({len(code.splitlines())} lines, {len(rep.warnings)} warning(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
