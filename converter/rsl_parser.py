"""Parse RSLogix 500 .RSL export files into the IR."""

from __future__ import annotations
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .ir import Branch, Instruction, PLCProject, Program, Routine, Rung, RungElement, Tag

# Instructions that take no operand in RSL text
_NO_OPERAND = {"ZQH", "END", "NOP", "MCR", "MCR_END", "MCR_ZONE"}

# Instructions whose first operand is an address/tag reference
# (others are numeric literals and should be preserved verbatim)
_ADDRESS_FIRST = {
    "XIC", "XIO", "OTE", "OTL", "OTU", "ONS", "OSR", "OSF",
    "TON", "TOF", "RTO", "CTU", "CTD", "RES",
    "MOV", "COP", "FLL", "CLR",
    "ADD", "SUB", "MUL", "DIV", "SQR", "NEG", "ABS",
    "EQU", "NEQ", "LES", "LEQ", "GRT", "GEQ",
    "FAL", "FSC", "FFL", "FFU",
    "JSR", "RET", "JMP", "LBL",
}


def parse_file(path: str | Path) -> PLCProject:
    lines = Path(path).read_text(encoding="ascii", errors="replace").splitlines()
    return _parse_lines(lines, name=Path(path).stem)


def _parse_lines(lines: List[str], name: str) -> PLCProject:
    project = PLCProject(name=name)
    current_program: Optional[Program] = None
    current_routine: Optional[Routine] = None
    pending_comment: Optional[str] = None
    rung_index = 0

    for raw in lines:
        line = raw.strip()

        # ── File header ────────────────────────────────────────────────────
        m = re.match(r"^FILE,LAD\s+(\d+)\s*:", line)
        if m:
            prog_name = f"Program_{m.group(1)}"
            current_program = Program(name=prog_name)
            project.programs.append(current_program)
            current_routine = Routine(name="MainRoutine", routine_type="RLL")
            current_program.routines.append(current_routine)
            rung_index = 0
            pending_comment = None
            continue

        m = re.match(r"^FILE,SBR\s+(\d+)\s*:", line)
        if m:
            sbr_name = f"Subroutine_{m.group(1)}"
            current_program = Program(name=sbr_name)
            project.programs.append(current_program)
            current_routine = Routine(name="MainRoutine", routine_type="RLL")
            current_program.routines.append(current_routine)
            rung_index = 0
            pending_comment = None
            continue

        # ── Rung comment (follows the SOR line it belongs to) ─────────────
        if line.startswith("RCM,"):
            pending_comment = line[4:]
            if current_routine and current_routine.rungs:
                current_routine.rungs[-1].comment = pending_comment
            pending_comment = None
            continue

        # ── Rung line ─────────────────────────────────────────────────────
        if line.startswith("SOR,") and current_routine is not None:
            rung = _parse_rung_line(line, rung_index)
            current_routine.rungs.append(rung)
            rung_index += 1
            continue

        # ── Data / symbol tables — ignore ─────────────────────────────────
        # BTBL, NTBL, FTBL, TRENDS, PIDTBL, SYMBOLS, DESCRS and their data rows

    # Collect tags used across all rungs
    for program in project.programs:
        _collect_tags(program)

    return project


def _parse_rung_line(line: str, number: int) -> Rung:
    """Parse one SOR...EOR line into a Rung."""
    # Strip SOR,N and EOR,N
    line = re.sub(r"\bSOR,\d+\s*", "", line)
    line = re.sub(r"\s*EOR,\d+\b", "", line)
    line = line.strip()

    tokens = line.split()

    # Detect trivial rungs
    if not tokens or tokens == ["ZQH,"]:
        return Rung(number=number, is_empty=True)
    if tokens and tokens[0].startswith("ZQH"):
        return Rung(number=number, is_empty=True)
    if tokens and tokens[0].startswith("END"):
        return Rung(number=number, is_end=True)

    elements, _ = _parse_token_stream(tokens, 0)
    return Rung(number=number, elements=elements)


def _parse_token(token: str) -> Tuple[str, List[str]]:
    """Split 'INSTR,op1,op2,...' into (name, [op1, op2, ...])."""
    if not token:
        return "", []
    parts = token.split(",")
    name = parts[0]
    operands = [p for p in parts[1:] if p]
    return name, operands


def _parse_token_stream(
    tokens: List[str], start: int
) -> Tuple[List[RungElement], int]:
    """Recursive-descent parser for a flat RSL token stream.

    Returns (elements, index_after_last_consumed).
    Stops when it encounters BND or runs out of tokens.
    """
    elements: List[RungElement] = []
    i = start

    while i < len(tokens):
        name, operands = _parse_token(tokens[i])

        if name in ("EOR", ""):
            i += 1
            break

        if name == "BST":
            branch_id = operands[0] if operands else ""
            legs: List[List[RungElement]] = []
            i += 1
            # Collect first leg
            leg, i = _parse_token_stream(tokens, i)
            legs.append(leg)
            # Collect additional legs (NXB) until BND
            while i < len(tokens):
                n2, ops2 = _parse_token(tokens[i])
                if n2 == "NXB":
                    i += 1
                    leg, i = _parse_token_stream(tokens, i)
                    legs.append(leg)
                elif n2 == "BND":
                    i += 1
                    break
                else:
                    break
            elements.append(Branch(legs=legs))
            continue

        if name in ("NXB", "BND"):
            # Signal the caller to stop; do NOT consume — caller handles it
            break

        if name in _NO_OPERAND or name in ("ZQH", "END"):
            i += 1
            break

        # Normal instruction
        elements.append(Instruction(name=name, operands=operands))
        i += 1

    return elements, i


def _collect_tags(program: Program) -> None:
    """Walk rungs and register every unique operand as a Tag on the program."""
    seen: Dict[str, str] = {}  # name → data_type

    def _visit(elements: List[RungElement]):
        for el in elements:
            if isinstance(el, Instruction):
                if el.operands:
                    addr = el.operands[0]
                    if addr and addr not in seen:
                        seen[addr] = _infer_type_from_address(addr, el.name)
            elif isinstance(el, Branch):
                for leg in el.legs:
                    _visit(leg)

    for routine in program.routines:
        for rung in routine.rungs:
            _visit(rung.elements)

    program.tags = [Tag(name=k, data_type=v) for k, v in seen.items()]


def _infer_type_from_address(addr: str, instr: str) -> str:
    """Guess the data type from an SLC-500 address."""
    if re.match(r"^T\d+:", addr):
        return "TIMER"
    if re.match(r"^C\d+:", addr):
        return "COUNTER"
    if re.match(r"^(N|F|D|L)\d+:", addr):
        return "DINT"
    return "BOOL"
