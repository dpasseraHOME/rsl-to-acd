"""Address ↔ tag-name mapping and rung-element translation."""

from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from .ir import Branch, Instruction, PLCProject, Program, RungElement, Tag


# ── Map file I/O ──────────────────────────────────────────────────────────

def load_map(path: str | Path) -> Dict[str, str]:
    """Load a JSON address-to-tag mapping file."""
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, dict) and "addresses" in data:
        return data["addresses"]
    return data  # flat dict also accepted


def save_map(mapping: Dict[str, str], path: str | Path) -> None:
    with open(path, "w") as f:
        json.dump({"addresses": mapping}, f, indent=2)


# ── Auto-generate mappings ────────────────────────────────────────────────

def auto_map_from_rsl(project: PLCProject) -> Dict[str, str]:
    """Generate address → suggested_tag_name for an RSL-sourced project."""
    addresses: Dict[str, str] = {}
    for program in project.programs:
        output_addrs = _find_output_addresses(program)
        for tag in program.tags:
            addr = tag.name
            if addr in addresses:
                continue
            addresses[addr] = _suggest_tag_name(addr, addr in output_addrs)
    return addresses


def auto_map_from_acd(project: PLCProject) -> Dict[str, str]:
    """Generate tag_name → SLC_address for an ACD-sourced project.

    Uses I/O alias definitions from the ACD when available (tag.alias_for).
    Falls back to sequential heuristic assignment for tags without aliases.
    """
    mapping: Dict[str, str] = {}
    input_counter: Dict[int, int] = {}   # slot → next bit
    output_counter: Dict[int, int] = {}
    timer_counter = 0
    counter_counter = 0
    dint_counter = 0

    output_tags = _find_output_tags(project)

    for program in project.programs:
        for tag in program.tags:
            name = tag.name
            if name in mapping:
                continue
            # Use actual alias address from ACD if present
            if tag.alias_for:
                mapping[name] = tag.alias_for
                continue
            dt = tag.data_type.upper()
            if dt == "TIMER":
                mapping[name] = f"T4:{timer_counter}"
                timer_counter += 1
            elif dt == "COUNTER":
                mapping[name] = f"C5:{counter_counter}"
                counter_counter += 1
            elif dt in ("DINT", "INT", "SINT"):
                mapping[name] = f"N7:{dint_counter}"
                dint_counter += 1
            else:
                # BOOL: determine input vs output from rung usage
                if name in output_tags:
                    slot = 2
                    bit = output_counter.get(slot, 0)
                    output_counter[slot] = bit + 1
                    mapping[name] = f"O:{slot}/{bit}"
                else:
                    slot = 1
                    bit = input_counter.get(slot, 0)
                    input_counter[slot] = bit + 1
                    mapping[name] = f"I:{slot}/{bit}"
    return mapping


# ── Apply mapping to a project ────────────────────────────────────────────

def apply_map(project: PLCProject, mapping: Dict[str, str]) -> PLCProject:
    """Return a new PLCProject with all operands translated via mapping.

    Works for both directions:
      RSL project  + addr→tag map  → tag-named project (ready for L5X/ACD)
      ACD project  + tag→addr map  → address-named project (ready for RSL)
    """
    from copy import deepcopy
    p = deepcopy(project)
    for program in p.programs:
        for routine in program.routines:
            for rung in routine.rungs:
                rung.elements = _translate_elements(rung.elements, mapping)
        # Update tag names
        program.tags = _translate_tags(program.tags, mapping)
    return p


def _translate_elements(
    elements: List[RungElement], mapping: Dict[str, str]
) -> List[RungElement]:
    result = []
    for el in elements:
        if isinstance(el, Instruction):
            new_ops = [_translate_operand(op, mapping) for op in el.operands]
            result.append(Instruction(name=el.name, operands=new_ops))
        elif isinstance(el, Branch):
            new_legs = [
                _translate_elements(leg, mapping) for leg in el.legs
            ]
            result.append(Branch(legs=new_legs))
    return result


# SLC-500 bit offsets for structured tag members
_COUNTER_BITS = {"CU": 15, "CD": 14, "DN": 13, "OV": 12, "UN": 11, "UA": 10}
_TIMER_BITS   = {"EN": 15, "TT": 14, "DN": 13}

def _translate_operand(op: str, mapping: Dict[str, str]) -> str:
    """Translate a single operand, handling Base.Member dot-notation."""
    if op == "?":
        return "0"  # Studio 5000 placeholder for counter/timer PRE and ACC
    if "." not in op:
        return mapping.get(op, op)
    base, member = op.split(".", 1)
    slc = mapping.get(base)
    if slc is None:
        return op  # unknown base tag — leave as-is
    member_up = member.upper()
    # Counter bit members: C5:N/bit
    if slc.startswith("C") and member_up in _COUNTER_BITS:
        return f"{slc}/{_COUNTER_BITS[member_up]}"
    # Timer bit members: T4:N/bit
    if slc.startswith("T") and member_up in _TIMER_BITS:
        return f"{slc}/{_TIMER_BITS[member_up]}"
    # Word members (.ACC, .PRE): C5:N.ACC  /  T4:N.ACC etc.
    if member_up in ("ACC", "PRE"):
        return f"{slc}.{member_up}"
    return op


def _translate_tags(tags: List[Tag], mapping: Dict[str, str]) -> List[Tag]:
    seen = {}
    for t in tags:
        new_name = mapping.get(t.name, t.name)
        if new_name not in seen:
            seen[new_name] = Tag(name=new_name, data_type=t.data_type)
    return list(seen.values())


# ── Helpers ───────────────────────────────────────────────────────────────

def _find_output_addresses(program) -> set:
    """Return set of addresses used as OTE/OTL/OTU targets."""
    outputs = set()
    def _walk(els):
        for el in els:
            if isinstance(el, Instruction) and el.name in ("OTE", "OTL", "OTU"):
                if el.operands:
                    outputs.add(el.operands[0])
            elif isinstance(el, Branch):
                for leg in el.legs:
                    _walk(leg)
    for routine in program.routines:
        for rung in routine.rungs:
            _walk(rung.elements)
    return outputs


def _find_output_tags(project: PLCProject) -> set:
    """Return set of tag names used as OTE/OTL/OTU targets across the project."""
    outputs = set()
    def _walk(els):
        for el in els:
            if isinstance(el, Instruction) and el.name in ("OTE", "OTL", "OTU"):
                if el.operands:
                    outputs.add(el.operands[0])
            elif isinstance(el, Branch):
                for leg in el.legs:
                    _walk(leg)
    for program in project.programs:
        for routine in program.routines:
            for rung in routine.rungs:
                _walk(rung.elements)
    return outputs


def _suggest_tag_name(addr: str, is_output: bool) -> str:
    """Turn an SLC-500 address into a readable tag name."""
    addr = addr.replace(":", "_").replace("/", "_").replace(".", "_")
    prefix = "Output" if is_output else "Input"
    if addr.startswith("I_") or addr.startswith("O_"):
        return f"{prefix}_{addr[2:]}"
    if addr.startswith("T"):
        return addr.replace("T4_", "timer_")
    if addr.startswith("C"):
        return addr.replace("C5_", "counter_")
    return addr
