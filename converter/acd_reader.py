"""Read a Rockwell .ACD file into the IR via acd-tools."""

from __future__ import annotations
import os
import re
import shutil
import sqlite3
import struct
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger as _loguru_logger
_loguru_logger.disable("acd")

from acd.l5x.export_l5x import ExportL5x

from .ir import Branch, Instruction, PLCProject, Program, Routine, Rung, RungElement, Tag


def diagnose(path: str | Path, known: Dict[str, str] = None) -> None:
    """Print a raw diagnostic dump of tag-related data in an ACD's SQLite DB.

    known: optional dict of tag_name → expected_slc_address for targeted
           binary search across all DB blobs.
    """
    path = Path(path)
    tmp = tempfile.mkdtemp()
    try:
        export = ExportL5x(str(path), _temp_dir=tmp)
        cur = export._cur
        _print_diagnose(cur, known=known)
        export._db.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _print_diagnose(cur: sqlite3.Cursor, known: Dict[str, str] = None) -> None:
    from acd.generated.comps.rx_generic import RxGeneric

    # ── 1. Tags referenced in rungs ───────────────────────────────────────
    cur.execute("SELECT object_id, rung FROM rungs")
    raw_rungs = cur.fetchall()
    referenced: Dict[str, str] = {}
    for _, rung_text in raw_rungs:
        _scan_operands(rung_text, referenced)
    rung_tags = sorted(referenced.keys())

    print(f"\n{'='*70}")
    print(f"ACD DIAGNOSTIC DUMP")
    print(f"{'='*70}")

    # ── 2. Full attr[0x01] hex for all rung tags ──────────────────────────
    print(f"\n--- Full extended record data per rung tag ---")
    for tag_name in rung_tags:
        cur.execute("SELECT record FROM comps WHERE comp_name=?", (tag_name,))
        rows = cur.fetchall()
        for (record,) in rows[:1]:
            try:
                record = bytes(record)
                r = RxGeneric.from_bytes(record)
                ext = {a.attribute_id: bytes(a.value) for a in r.extended_records}
                print(f"\n  [{tag_name}]  cip=0x{r.cip_type:02X}")
                for attr_id, val in sorted(ext.items()):
                    # Print full hex, 32 bytes per line
                    hex_str = val.hex()
                    lines = [hex_str[i:i+64] for i in range(0, len(hex_str), 64)]
                    print(f"    attr[0x{attr_id:02X}] ({len(val)}b):")
                    for line in lines:
                        print(f"      {line}")
            except Exception as e:
                print(f"  {tag_name}: [error: {e}]")

    # ── 3. Search all blob columns for known address byte patterns ────────
    if known:
        print(f"\n--- Searching all blobs for known address patterns ---")
        # Build search needles: ASCII and UTF-16LE forms of each address
        needles = {}
        for tag, addr in known.items():
            needles[tag] = [
                addr.encode("ascii"),
                addr.encode("utf-16-le"),
            ]
            # Also try colon-less variants: "I:1/0" → look for slot/bit as u32 pair
            # Parse I:slot/bit or O:slot/bit
            m = re.match(r"[IO]:(\d+)/(\d+)", addr)
            if m:
                slot, bit = int(m.group(1)), int(m.group(2))
                needles[tag].append(struct.pack("<II", slot, bit))
                needles[tag].append(struct.pack("<HH", slot, bit))

        tables = {
            "comps": "SELECT comp_name, record FROM comps",
            "comments": "SELECT object_id, record_string FROM comments",
            "nameless": "SELECT object_id, record FROM nameless",
            "pointers": "SELECT comp_name, record FROM pointers",
        }
        for table, query in tables.items():
            try:
                cur.execute(query)
                for row in cur.fetchall():
                    row_id = row[0]
                    blob = row[1]
                    if blob is None:
                        continue
                    if isinstance(blob, str):
                        blob = blob.encode("utf-8")
                    blob = bytes(blob)
                    for tag, needle_list in needles.items():
                        for needle in needle_list:
                            pos = blob.find(needle)
                            if pos != -1:
                                ctx = blob[max(0, pos-8):pos+len(needle)+16].hex()
                                print(f"  FOUND {tag}={known[tag]!r} in {table} row={row_id!r} "
                                      f"offset={pos}  needle={needle.hex()}  context={ctx}")
            except Exception as e:
                print(f"  [{table}] error: {e}")

    # ── 4. nameless table (first 5 rows) ──────────────────────────────────
    print(f"\n--- nameless table (first 5 rows) ---")
    try:
        cur.execute("SELECT object_id, parent_id, length(record) FROM nameless LIMIT 5")
        for oid, pid, rlen in cur.fetchall():
            print(f"  oid={oid}  parent={pid}  record_len={rlen}")
    except Exception as e:
        print(f"  [error: {e}]")

    # ── 5. pointers table (first 5 rows) ──────────────────────────────────
    print(f"\n--- pointers table (first 5 rows) ---")
    try:
        cur.execute("SELECT comp_name, object_id, parent_id, length(record) FROM pointers LIMIT 5")
        for name, oid, pid, rlen in cur.fetchall():
            print(f"  {name:30s}  oid={oid}  parent={pid}  record_len={rlen}")
    except Exception as e:
        print(f"  [error: {e}]")

    print(f"\n{'='*70}\n")


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

    # ── Alias map: tag name → SLC-500 address (from Studio 5000 alias defs) ──
    alias_map = _read_tag_alias_map(cur)

    # ── Rungs ─────────────────────────────────────────────────────────────
    cur.execute("SELECT object_id, rung FROM rungs")
    raw_rungs = cur.fetchall()

    # Collect all tag names referenced in rungs
    referenced: Dict[str, str] = {}  # name → inferred type
    for _, rung_text in raw_rungs:
        _scan_operands(rung_text, referenced)

    # Build tag list, attaching alias addresses where available
    program_tags = [
        Tag(name=n, data_type=t, alias_for=alias_map.get(n))
        for n, t in sorted(referenced.items())
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


def _read_tag_alias_map(cur: sqlite3.Cursor) -> Dict[str, str]:
    """Build tag_name → SLC-500 address from alias tag definitions in the ACD.

    Studio 5000 stores alias-for paths (e.g. Local:1:I.Data.0) in the
    comments table as tag_reference.  We look them up via the comment_id
    embedded in each tag's binary record blob.
    """
    alias_map: Dict[str, str] = {}
    try:
        cur.execute("SELECT comp_name, record FROM comps WHERE LENGTH(record) > 14")
        rows = cur.fetchall()
    except Exception:
        return alias_map

    for comp_name, record in rows:
        try:
            record = bytes(record)
            if len(record) < 14:
                continue
            cip_type = struct.unpack_from("<H", record, 10)[0]
            if cip_type not in (0x68, 0x6B):  # only tag objects
                continue
            comment_id = struct.unpack_from("<H", record, 12)[0]
            parent_key = (comment_id * 0x10000) + cip_type

            cur.execute(
                "SELECT tag_reference FROM comments WHERE parent=? AND tag_reference != ''",
                (parent_key,),
            )
            for (tag_ref,) in cur.fetchall():
                slc_addr = _io_path_to_slc(tag_ref)
                if slc_addr:
                    alias_map[comp_name] = slc_addr
                    break
        except Exception:
            continue

    return alias_map


def _io_path_to_slc(path: str) -> Optional[str]:
    """Convert a Studio 5000 I/O path to an SLC-500 address string.

    Local:N:I.Data.B  →  I:N/B
    Local:N:O.Data.B  →  O:N/B
    Local:N:I.Data    →  I:N      (word-level)
    Local:N:O.Data    →  O:N
    """
    m = re.match(r"Local:(\d+):(I|O)\.Data\.(\d+)$", path, re.IGNORECASE)
    if m:
        return f"{m.group(2).upper()}:{m.group(1)}/{m.group(3)}"
    m = re.match(r"Local:(\d+):(I|O)\.Data$", path, re.IGNORECASE)
    if m:
        return f"{m.group(2).upper()}:{m.group(1)}"
    return None


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
