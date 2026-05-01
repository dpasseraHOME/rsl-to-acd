"""Write a PLCProject to Rockwell L5X (Logix 5000 XML) format."""

from __future__ import annotations
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List
from xml.dom import minidom

from .ir import Branch, Instruction, PLCProject, Program, Routine, Rung, RungElement, Tag

# Default controller type for CompactLogix (same family as Lab12 ACD)
_PROCESSOR_TYPE = "1769-L18ERM/A"
_MAJOR_REV = "35"
_MINOR_REV = "11"


def write_file(project: PLCProject, path: str | Path) -> None:
    Path(path).write_text(render(project), encoding="utf-8")


def render(project: PLCProject) -> str:
    root = ET.Element("RSLogix5000Content", {
        "SchemaRevision": "1.0",
        "SoftwareRevision": f"{_MAJOR_REV}.{_MINOR_REV}",
        "TargetName": project.name,
        "TargetType": "Controller",
        "ContainsContext": "false",
    })

    ctrl = ET.SubElement(root, "Controller", {
        "Name": project.name,
        "ProcessorType": _PROCESSOR_TYPE,
        "MajorRev": _MAJOR_REV,
        "MinorRev": _MINOR_REV,
        "TimeSlice": "20",
        "ShareUnusedTimeSlice": "1",
        "SFCExecutionControl": "CurrentActive",
        "SFCRestartPosition": "MostRecent",
        "SFCLastScan": "DontScan",
    })

    ET.SubElement(ctrl, "DataTypes")
    ET.SubElement(ctrl, "Modules")
    ET.SubElement(ctrl, "AddOnInstructionDefinitions")
    ET.SubElement(ctrl, "Tags")  # controller-scoped tags (none for these labs)

    programs_el = ET.SubElement(ctrl, "Programs")
    main_programs = [p for p in project.programs if not p.name.startswith("Subroutine_")]
    for prog in main_programs:
        _build_program(programs_el, prog)

    tasks_el = ET.SubElement(ctrl, "Tasks")
    task = ET.SubElement(tasks_el, "Task", {
        "Name": "MainTask",
        "Type": "CONTINUOUS",
        "Rate": "10",
        "Priority": "10",
        "Watchdog": "500",
        "DisableUpdateOutputs": "false",
        "InhibitTask": "false",
    })
    sched = ET.SubElement(task, "ScheduledPrograms")
    for prog in main_programs:
        ET.SubElement(sched, "ScheduledProgram", {"Name": prog.name})

    return _pretty(root)


def _build_program(parent: ET.Element, program: Program) -> None:
    main_routine = program.routines[0].name if program.routines else "MainRoutine"
    prog_el = ET.SubElement(parent, "Program", {
        "Name": program.name,
        "TestEdits": "false",
        "MainRoutineName": main_routine,
        "Disabled": "false",
        "UseAsFolder": "false",
    })

    tags_el = ET.SubElement(prog_el, "Tags")
    for tag in program.tags:
        _build_tag(tags_el, tag)

    routines_el = ET.SubElement(prog_el, "Routines")
    for routine in program.routines:
        _build_routine(routines_el, routine)


def _build_tag(parent: ET.Element, tag: Tag) -> None:
    dt = tag.data_type.upper()
    radix = "NullType" if dt not in ("BOOL", "SINT", "INT", "DINT", "LINT", "REAL") else "Decimal"
    if dt == "REAL":
        radix = "Float"
    tag_el = ET.SubElement(parent, "Tag", {
        "Name": tag.name,
        "TagType": "Base",
        "DataType": tag.data_type,
        "Radix": radix,
        "Constant": "false",
        "ExternalAccess": "Read/Write",
    })
    # Default initial value
    default_val = "0" if dt in ("BOOL", "SINT", "INT", "DINT", "LINT") else "0.0"
    data_el = ET.SubElement(tag_el, "DefaultData", {"Format": "L5K"})
    data_el.text = default_val


def _build_routine(parent: ET.Element, routine: Routine) -> None:
    r_el = ET.SubElement(parent, "Routine", {
        "Name": routine.name,
        "Type": routine.routine_type,
    })
    if routine.routine_type == "RLL":
        rll = ET.SubElement(r_el, "RLLContent")
        for rung in routine.rungs:
            if rung.is_end:
                continue  # END marker has no equivalent in L5X
            _build_rung(rll, rung)


def _build_rung(parent: ET.Element, rung: Rung) -> None:
    rung_el = ET.SubElement(parent, "Rung", {
        "Number": str(rung.number),
        "Type": "N",
    })
    text_el = ET.SubElement(rung_el, "Text")
    if rung.is_empty:
        text_el.text = "NOP();"
    else:
        text_el.text = elements_to_s5k(rung.elements) + ";"
    if rung.comment:
        comment_el = ET.SubElement(rung_el, "Comment")
        comment_el.text = rung.comment


def elements_to_s5k(elements: List[RungElement]) -> str:
    """Render a list of IR elements to Studio 5000 rung text."""
    parts: List[str] = []
    for el in elements:
        if isinstance(el, Instruction):
            if el.operands:
                parts.append(f"{el.name}({','.join(el.operands)})")
            else:
                parts.append(f"{el.name}()")
        elif isinstance(el, Branch):
            legs = [elements_to_s5k(leg) for leg in el.legs]
            parts.append("[" + " ,".join(legs) + " ]")
    return "".join(parts)


def _pretty(root: ET.Element) -> str:
    raw = ET.tostring(root, encoding="unicode", xml_declaration=False)
    dom = minidom.parseString(raw)
    pretty = dom.toprettyxml(indent="  ", encoding=None)
    # minidom adds its own xml declaration — replace with the standard one
    lines = pretty.splitlines()
    if lines and lines[0].startswith("<?xml"):
        lines[0] = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    return "\n".join(lines) + "\n"
