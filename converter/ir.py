"""Intermediate representation for PLC ladder logic programs."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union


@dataclass
class Instruction:
    """Single PLC instruction: name + ordered operand list.

    Examples
    --------
    XIC(Start_PB_1)         → Instruction("XIC", ["Start_PB_1"])
    TON(timer,1000,0)       → Instruction("TON", ["timer", "1000", "0"])
    NOP()                   → Instruction("NOP", [])
    """
    name: str
    operands: List[str]


@dataclass
class Branch:
    """Parallel branch — two or more legs run in parallel.

    Corresponds to BST/NXB/BND in RSL and [...] in Studio 5000.
    Each leg is a list of RungElement (Instruction or nested Branch).
    """
    legs: List[List[RungElement]]


RungElement = Union[Instruction, Branch]


@dataclass
class Rung:
    """Single ladder rung."""
    number: int
    elements: List[RungElement] = field(default_factory=list)
    comment: str = ""
    is_empty: bool = False   # ZQH placeholder rung
    is_end: bool = False     # END-of-file marker rung


@dataclass
class Routine:
    """PLC routine (ladder, SFC, FBD, ST)."""
    name: str
    routine_type: str = "RLL"
    rungs: List[Rung] = field(default_factory=list)


@dataclass
class Tag:
    """PLC tag / variable."""
    name: str
    data_type: str = "BOOL"
    value: Optional[str] = None


@dataclass
class Program:
    """PLC program — contains routines and program-scoped tags."""
    name: str
    routines: List[Routine] = field(default_factory=list)
    tags: List[Tag] = field(default_factory=list)


@dataclass
class PLCProject:
    """Top-level project container."""
    name: str
    programs: List[Program] = field(default_factory=list)
    controller_tags: List[Tag] = field(default_factory=list)
