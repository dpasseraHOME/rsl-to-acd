"""Read a Rockwell .ACD file into the IR via acd-tools."""

from __future__ import annotations
import os
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger as _loguru_logger
_loguru_logger.disable("acd")

from acd.l5x.export_l5x import ExportL5x

from .ir import Branch, Instruction, PLCProject, Program, Routine, Rung, RungElement, Tag


def read_file(path: str | Path) -> PLCProject:
    """Extract an ACD file and return a PLCProject."""
    path = Path(path)
    tmp = tempfile.mkdtemp()
    try:
        export = ExportL5x(str(path), _temp_dir=tmp)
        cur = export._cur
        project = _build_project(cur, name=path.stem)
        export._db.close()
    finally:
        # ignore_errors=True because acd-tools may hold open handles to the
        # extracted binary files (Comments.Dat, Comps.Dat, etc.) on Windows;
        # the OS will clean the temp dir on reboot if this silent-fails here.
        shutil.rmtree(tmp, ignore_errors=True)
    return project


def _build_project(cur: sqlite3.Cursor, name: str) -> PLCProject:
    project = PLCProject(name=name)

    # ── Tags (program-scoped) ──────────────────────────────────────────────
    # comps table holds tag names; filter by known tag types
    cur.execute(
        "SELECT comp_name, object_id FROM comps "
        "WHERE comp_name NOT LIKE 'Rx%' "
        "  AND comp_name NOT LIKE '$%' "
        "  AND comp_name NOT IN ('MainProgram','MainRoutine','RxProgramCollection',"
        "                        'RxRoutineCollection','RxTagCollection',"
        "                        'RxDataCollection','RxAlarmDigitalCollection',"
        "                        'RxAlarmAnalogCollection','RxHMIBCCollection',"
        "                        'RxBEOCollection','RxEEOCollection',"
        "                        'RxMsgCollection','RxChartCollection',"
        "                        'RxLabelCollection') "
        "  AND LENGTH(comp_name) > 0"
    )
    all_comps = {row[1]: row[0] for row in cur.fetchall()}

    # Grab typed tag info from comps name list that appears in TagInfo context
    # We rely on the rung operand names to know which are real user tags
    cur.execute("SELECT object_id, rung FROM rungs")
    raw_rungs = cur.fetchall()

    # Collect all tag names referenced in rungs
    referenced: Dict[str, str] = {}  # name → inferred type
    for _, rung_text in raw_rungs:
        _scan_operands(rung_text, referenced)

    # Build tag list
    program_tags = [
        Tag(name=n, data_type=t) for n, t in sorted(referenced.items())
    ]

    # ── Comments keyed by object_id ───────────────────────────────────────
    cur.execute(
        "SELECT object_id, record_string FROM comments "
        "WHERE record_string != '' AND LENGTH(record_string) < 500"
    )
    # Use first non-metadata comment per object — heuristic
    comments: Dict[int, str] = {}
    for oid, text in cur.fetchall():
        if oid not in comments and not _is_metadata_comment(text):
            comments[oid] = text

    # ── Rungs ─────────────────────────────────────────────────────────────
    rungs: List[Rung] = []
    for idx, (oid, rung_text) in enumerate(raw_rungs):
        elements = _parse_s5k_rung(rung_text)
        comment = comments.get(oid, "")
        rungs.append(Rung(number=idx, elements=elements, comment=comment))

    routine = Routine(name="MainRoutine", routine_type="RLL", rungs=rungs)
    program = Program(
        name="MainProgram",
        routines=[routine],
        tags=program_tags,
    )
    project.programs.append(program)
    return project


def _is_metadata_comment(text: str) -> bool:
    metadata = {
        "LocalName", "EnglishName", "SupportsLS", "SupportsPT", "SupportsPTC",
        "SupportsPTFP", "SupportsCTI", "SupportsMVFB", "InternalGroup",
        "InternalSubGroup", "Definition Local Name", "Definition English Name",
        "0", "1", "2",
    }
    return text.strip() in metadata


# ── Studio 5000 rung text parser ──────────────────────────────────────────

class _S5KParser:
    """Recursive-descent parser for Studio 5000 rung text."""

    def __init__(self, text: str):
        self.text = text.rstrip(";").strip()
        self.pos = 0

    def peek(self) -> Optional[str]:
        return self.text[self.pos] if self.pos < len(self.text) else None

    def consume(self) -> str:
        c = self.text[self.pos]
        self.pos += 1
        return c

    def skip_ws(self):
        while self.pos < len(self.text) and self.text[self.pos] in " \t\r\n":
            self.pos += 1

    def parse_elements(self, stop: str = "") -> List[RungElement]:
        elements: List[RungElement] = []
        while self.pos < len(self.text):
            self.skip_ws()
            c = self.peek()
            if c is None or c in stop:
                break
            if c == "[":
                self.consume()
                elements.append(self._parse_branch())
            elif c.isupper():
                elements.append(self._parse_instruction())
            else:
                self.consume()  # skip unexpected char
        return elements

    def _parse_branch(self) -> Branch:
        """Parse [...]. Opening '[' already consumed."""
        legs: List[List[RungElement]] = []
        leg = self.parse_elements(stop="],")
        legs.append(leg)
        while self.pos < len(self.text):
            c = self.peek()
            if c == "]":
                self.consume()
                break
            # Separator may arrive as ',' (space already consumed by skip_ws
            # inside parse_elements) or as ' ,' (space not yet consumed).
            if c == ",":
                self.consume()
                self.skip_ws()
                legs.append(self.parse_elements(stop="],"))
            elif c == " " and self.pos + 1 < len(self.text) and self.text[self.pos + 1] == ",":
                self.pos += 2
                self.skip_ws()
                legs.append(self.parse_elements(stop="],"))
            else:
                self.consume()
        return Branch(legs=legs)

    def _parse_instruction(self) -> Instruction:
        name = ""
        while self.pos < len(self.text) and self.text[self.pos].isalnum():
            name += self.consume()
        operands: List[str] = []
        if self.peek() == "(":
            self.consume()  # skip '('
            op = ""
            depth = 1
            while self.pos < len(self.text) and depth > 0:
                c = self.consume()
                if c == "(":
                    depth += 1
                    op += c
                elif c == ")":
                    depth -= 1
                    if depth > 0:
                        op += c
                elif c == "," and depth == 1:
                    operands.append(op.strip())
                    op = ""
                else:
                    op += c
            if op.strip():
                operands.append(op.strip())
        return Instruction(name=name, operands=operands)


def _parse_s5k_rung(text: str) -> List[RungElement]:
    return _S5KParser(text).parse_elements()


def _scan_operands(rung_text: str, registry: Dict[str, str]) -> None:
    """Walk a Studio 5000 rung text string and register tag operands."""
    # Match INSTR(op[,op...])
    for m in re.finditer(r"([A-Z][A-Z0-9]*)\(([^)]*)\)", rung_text):
        instr = m.group(1)
        for op in m.group(2).split(","):
            op = op.strip()
            if op and not op.lstrip("-").isdigit() and "." not in op:
                if op not in registry:
                    registry[op] = _infer_type_from_tag(op, instr)


def _infer_type_from_tag(tag: str, instr: str) -> str:
    """Heuristic data-type inference from tag name and instruction context."""
    lower = tag.lower()
    if instr in ("TON", "TOF", "RTO"):
        return "TIMER"
    if instr in ("CTU", "CTD"):
        return "COUNTER"
    if "timer" in lower or lower.startswith("t_"):
        return "TIMER"
    if "counter" in lower or lower.startswith("cnt"):
        return "COUNTER"
    if lower.endswith("[16]") or "seq" in lower:
        return "DINT"
    return "BOOL"
