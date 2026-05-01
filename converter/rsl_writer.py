"""Write a PLCProject to RSLogix 500 .RSL export format."""

from __future__ import annotations
from pathlib import Path
from typing import List

from .ir import Branch, Instruction, PLCProject, Program, Routine, Rung, RungElement

# ── RSL table dimensions (SLC-500 standard) ───────────────────────────────
_BTBL_SIZE = 128
_NTBL_SIZE = 128
_FTBL_SIZE = 128
_TRENDS_SIZE = 14
_PIDTBL_SIZE = 26
_SBR_FILES = range(3, 10)   # SBR 3 through SBR 9


def write_file(project: PLCProject, path: str | Path) -> None:
    Path(path).write_text(render(project), encoding="ascii")


def render(project: PLCProject) -> str:
    lines: List[str] = []

    # Main LAD file (first program only; others become SBR files)
    main_programs = [p for p in project.programs if not p.name.startswith("Subroutine_")]
    sbr_programs  = [p for p in project.programs if p.name.startswith("Subroutine_")]

    if main_programs:
        prog = main_programs[0]
        lines.append("FILE,LAD 2:")
        routine = prog.routines[0] if prog.routines else Routine("MainRoutine")
        _render_routine(routine, lines)
    else:
        lines.append("FILE,LAD 2:")
        lines.append("SOR,0 ZQH, EOR,0")
        lines.append("RCM,")
        lines.append("SOR,1 END,")
        lines.append("RCM,")

    # SBR files ─ use extracted SBR programs if available, else emit empty stubs
    sbr_map = {p.name: p for p in sbr_programs}
    for n in _SBR_FILES:
        key = f"Subroutine_{n}"
        lines.append(f"FILE,SBR {n}:")
        if key in sbr_map and sbr_map[key].routines:
            _render_routine(sbr_map[key].routines[0], lines)
        else:
            lines.append("SOR,0 ZQH, EOR,0")
            lines.append("RCM,")
            lines.append("SOR,1 END,")
            lines.append("RCM,")

    # Data tables
    lines.append("BTBL")
    lines.extend(["0"] * _BTBL_SIZE)
    lines.append("NTBL")
    lines.extend(["0"] * _NTBL_SIZE)
    lines.append("FTBL")
    lines.extend(["0"] * _FTBL_SIZE)
    lines.append("TRENDS")
    lines.extend(["0"] * _TRENDS_SIZE)
    for _ in range(2):
        lines.append("PIDTBL")
        lines.extend([" 0 "] * _PIDTBL_SIZE)
    lines.append("SYMBOLS")
    lines.append("DESCRS")

    return "\n".join(lines) + "\n"


def _render_routine(routine: Routine, lines: List[str]) -> None:
    rung_num = 0
    for rung in routine.rungs:
        if rung.is_end:
            lines.append(f"SOR,{rung_num} END,")
            lines.append(f"RCM,{rung.comment}")
            rung_num += 1
            continue
        if rung.is_empty:
            lines.append(f"SOR,{rung_num} ZQH, EOR,{rung_num}")
            lines.append(f"RCM,{rung.comment}")
            rung_num += 1
            continue
        tokens = _elements_to_tokens(rung.elements, _BranchCounter())
        rung_text = " ".join(tokens)
        lines.append(f"SOR,{rung_num} {rung_text} EOR,{rung_num}")
        lines.append(f"RCM,{rung.comment}")
        rung_num += 1

    # Always end with an empty rung and END rung
    if not routine.rungs or not routine.rungs[-1].is_end:
        lines.append(f"SOR,{rung_num} ZQH, EOR,{rung_num}")
        lines.append("RCM,")
        rung_num += 1
        lines.append(f"SOR,{rung_num} END,")
        lines.append("RCM,")


class _BranchCounter:
    def __init__(self):
        self.n = 0

    def next(self) -> int:
        self.n += 1
        return self.n


def _elements_to_tokens(
    elements: List[RungElement], counter: _BranchCounter
) -> List[str]:
    tokens: List[str] = []
    for el in elements:
        if isinstance(el, Instruction):
            if el.operands:
                tokens.append(f"{el.name},{','.join(el.operands)}")
            else:
                tokens.append(f"{el.name},")
        elif isinstance(el, Branch):
            n = counter.next()
            tokens.append(f"BST,{n}")
            for i, leg in enumerate(el.legs):
                if i > 0:
                    tokens.append(f"NXB,{n}")
                tokens.extend(_elements_to_tokens(leg, counter))
            tokens.append(f"BND,{n}")
    return tokens
