#!/usr/bin/env python3
"""Minimal Dolfin → Python dataclass compiler.

Reads a .dolfin file, emits a Python module with:
- typing.Literal[...] for enum concepts (concept X: one of: ...)
- @dataclass for regular concepts (concept X: has ...)

Cardinality mapping:
    one <T>              → T                             (required)
    optional <T>         → Optional[T] = None            (optional)
    at least 1 <T>       → list[T]                       (required non-empty)
    at least N <T>       → list[T]                       (docstring: at least N)
    at most N <T>        → list[T]                       (docstring: at most N)
    exactly N <T>        → list[T]                       (docstring: exactly N)
    between N M <T>      → list[T]                       (docstring: between N and M)
    (no cardinality) <T> → list[T] = field(default_factory=list)  (any)

Type mapping:
    string  → str
    int     → int
    float   → float
    boolean → bool
    other   → forward reference to another concept

Usage:
    python3 dolfin2model.py path/to/model.dolfin           # prints to stdout
    python3 dolfin2model.py path/to/model.dolfin out.py    # writes to file
"""
from __future__ import annotations
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

@dataclass
class Attr:
    name: str
    cardinality: str  # "one" | "optional" | "many" | "at_least_N" | ...
    raw_cardinality: str
    type_name: str  # canonical type name (str, int, float, bool, or Concept)
    is_primitive: bool


@dataclass
class Concept:
    name: str
    kind: str  # "enum" or "record"
    enum_values: list[str] = field(default_factory=list)
    attrs: list[Attr] = field(default_factory=list)


PRIMITIVES = {
    "string": "str",
    "int": "int",
    "float": "float",
    "boolean": "bool",
}


_HAS_LINE = re.compile(
    r"has\s+(?P<name>\w+)\s*:\s*(?P<card>.+?)\s+(?P<type>\w+)\s*$"
)
_CONCEPT_LINE = re.compile(r"concept\s+(?P<name>\w+)\s*:\s*$")


def _classify_cardinality(raw: str) -> str:
    raw = raw.strip()
    if raw == "one":
        return "one"
    if raw == "optional":
        return "optional"
    if raw == "at least 1":
        return "at_least_1"
    if raw.startswith("at least "):
        return "at_least_n"
    if raw.startswith("at most "):
        return "at_most_n"
    if raw.startswith("exactly "):
        return "exactly_n"
    if raw.startswith("between "):
        return "between_n_m"
    # bare type, no cardinality keyword → treat as "many"
    return "many"


def parse(text: str) -> list[Concept]:
    concepts: list[Concept] = []
    current: Optional[Concept] = None
    in_one_of = False

    for raw_line in text.splitlines():
        line = raw_line.rstrip()

        # Skip empty lines
        if not line.strip():
            continue

        # Skip the package block header entirely (its lines all start indented
        # after the top-level `package ...:` declaration and use tokens like
        # dolfin_version, version, author, description).
        if line.startswith("package "):
            current = None
            in_one_of = False
            continue

        # A new top-level `concept X:` opens a fresh concept.
        m = _CONCEPT_LINE.match(line.strip())
        if m:
            current = Concept(name=m.group("name"), kind="record")
            concepts.append(current)
            in_one_of = False
            continue

        # Inside the "one of:" block of an enum concept.
        if current is not None and line.strip() == "one of:":
            current.kind = "enum"
            in_one_of = True
            continue

        if in_one_of:
            token = line.strip()
            if token and _HAS_LINE.match(token) is None and _CONCEPT_LINE.match(token) is None:
                current.enum_values.append(token)
                continue

        # A `has X: <cardinality> <Type>` line inside a record concept.
        m = _HAS_LINE.match(line.strip())
        if m and current is not None and current.kind != "enum":
            raw_card = m.group("card").strip()
            type_name_dolfin = m.group("type").strip()
            card = _classify_cardinality(raw_card)
            is_prim = type_name_dolfin in PRIMITIVES
            type_name = PRIMITIVES.get(type_name_dolfin, type_name_dolfin)
            current.attrs.append(Attr(
                name=m.group("name"),
                cardinality=card,
                raw_cardinality=raw_card,
                type_name=type_name,
                is_primitive=is_prim,
            ))
            continue

        # Anything else inside the package block (version, description, ...)
        # is ignored.

    return concepts


# ---------------------------------------------------------------------------
# Emitter
# ---------------------------------------------------------------------------

def _quoted(s: str) -> str:
    """Emit a Python string literal, escaping any embedded double-quotes."""
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _field_type(attr: Attr) -> str:
    """Return the Python type annotation for an attribute."""
    base = attr.type_name
    if attr.cardinality == "one":
        return base
    if attr.cardinality == "optional":
        return f"Optional[{base}]"
    # any many-shaped cardinality
    return f"list[{base}]"


def _field_default(attr: Attr) -> str:
    """Return the default-value expression (right of `=`), or empty string."""
    if attr.cardinality == "one":
        return ""
    if attr.cardinality == "optional":
        return "None"
    if attr.cardinality == "at_least_1":
        # required non-empty list, no sensible default
        return ""
    # many, at_least_n, at_most_n, exactly_n, between_n_m → default empty list
    return "field(default_factory=list)"


def _card_comment(attr: Attr) -> str:
    """A short comment describing the cardinality when it's not obvious."""
    if attr.cardinality in ("one", "optional", "many"):
        return ""
    return f"  # cardinality: {attr.raw_cardinality}"


def emit(concepts: list[Concept]) -> str:
    lines: list[str] = []
    lines.append('"""Generated by dolfin2model.py, do not edit by hand."""')
    lines.append("from __future__ import annotations")
    lines.append("from dataclasses import dataclass, field")
    lines.append("from typing import Literal, Optional")
    lines.append("")
    lines.append("")

    # Enums first, so that record definitions can refer to them.
    enums = [c for c in concepts if c.kind == "enum"]
    records = [c for c in concepts if c.kind == "record"]

    for c in enums:
        if not c.enum_values:
            continue
        values = ", ".join(_quoted(v) for v in c.enum_values)
        lines.append(f"{c.name} = Literal[{values}]")
        lines.append("")

    if enums:
        lines.append("")

    # Records.
    for c in records:
        # Frozen dataclasses for identity-value entities without mutable defaults.
        # We default to non-frozen mutable @dataclass to keep field(default_factory=list) valid.
        lines.append("@dataclass")
        lines.append(f"class {c.name}:")
        if not c.attrs:
            lines.append("    pass")
            lines.append("")
            lines.append("")
            continue

        # Order attrs: required fields first (no default), then those with defaults.
        required = [a for a in c.attrs if _field_default(a) == ""]
        with_default = [a for a in c.attrs if _field_default(a) != ""]
        for a in required + with_default:
            annot = _field_type(a)
            default = _field_default(a)
            comment = _card_comment(a)
            if default:
                lines.append(f"    {a.name}: {annot} = {default}{comment}")
            else:
                lines.append(f"    {a.name}: {annot}{comment}")
        lines.append("")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0

    src_path = Path(argv[1])
    if not src_path.exists():
        print(f"error: file not found: {src_path}", file=sys.stderr)
        return 2

    text = src_path.read_text(encoding="utf-8")
    concepts = parse(text)
    output = emit(concepts)

    if len(argv) >= 3:
        out_path = Path(argv[2])
        out_path.write_text(output, encoding="utf-8")
        print(f"→ wrote {out_path} ({len(concepts)} concepts: "
              f"{sum(1 for c in concepts if c.kind == 'enum')} enums, "
              f"{sum(1 for c in concepts if c.kind == 'record')} records)")
    else:
        sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
