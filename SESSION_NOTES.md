# Session Notes — PLC Ladder Logic Converter
**Date:** 2026-05-01  
**Repo:** https://github.com/dpasseraHOME/rsl-to-acd

---

## What was built

A bidirectional converter between two Rockwell Automation PLC file formats, with an interactive wizard designed for non-technical Windows users.

**Primary workflow:** Studio 5000 (ACD) → RSLogix 500 (RSL)  
**Also supported:** ACD → L5X, RSL → L5X, file inspection

---

## Starting point

Two files were provided:
- `Lab10_6_35.RSL` — RSLogix 500 export (SLC-500 platform)
- `Lab12_6_35.ACD` — Studio 5000 project (CompactLogix platform)

Both contained the same 3-motor sequential interlock logic, just expressed in different platform languages. The naming convention (`Lab##_6_35`) identifies these as lab assignments, likely from an industrial automation course.

---

## File format research

### RSL
Plain ASCII text. Human-readable immediately. Each rung is one line:
```
SOR,0 XIC,I:1/1 BST,1 XIC,I:1/0 NXB,1 XIC,O:2/0 BND,1 OTE,O:2/0 EOR,0
RCM,Motor 1 seal-in.
```
Instruction tokens are `NAME,OPERAND` separated by spaces. Branch structure uses `BST`/`NXB`/`BND` markers with matching numeric IDs. File also contains data tables (BTBL, NTBL, FTBL), subroutine files (SBR), and symbol/description sections — all of which were empty zeros in this lab file.

### ACD
Proprietary binary archive. Starts with a plain-text header/log (readable in a hex editor), then contains compressed binary database files: `Comps.Dat`, `SbRegion.Dat`, `Comments.Dat`, `Nameless.Dat`, and others.

Key discovery from the hex dump: the file header identified it as Studio 5000 **V35.00.00**, created **2026-04-23**.

### How ACD stores rung text
`SbRegion.Dat` holds the actual ladder logic. Records with `language_type == "Rung NT"` contain the rung text encoded as **UTF-16 LE**, with tag references stored as `@hex_id@` placeholders that resolve to tag names via the `Comps.Dat` component registry.

### L5X
Rockwell's XML interchange format. Human-readable, Studio 5000 importable. Sits between ACD and everything else — used as the write target for RSL→ACD conversion (since direct ACD binary writing was out of scope).

---

## Tool selection

**`acd-tools`** (PyPI: `pip install acd-tools`, GitHub: `hutcheb/acd`) was identified as the right library. It:
- Supports ACD files up to Studio 5000 v35 (our file was exactly v35)
- Extracts the ACD into an intermediate SQLite database
- Parses `SbRegion.Dat` rung text and resolves `@hex_id@` references to tag names

Extraction confirmed 3 rungs in the SQLite `rungs` table:
```
XIC(Stop_PB_1)[XIC(Start_PB_1) ,XIC(Motor_1) ]OTE(Motor_1);
XIC(Stop_PB_2)XIC(Motor_1)[XIC(Start_PB_2) ,XIC(Motor_2) ]OTE(Motor_2);
XIC(Stop_PB_3)XIC(Motor_2)[XIC(Start_PB_3) ,XIC(Motor_3) ]OTE(Motor_3);
```

The `acd-tools` L5X export produced a valid XML skeleton but did not populate the rung content — a known limitation of v0.2a8. The rung data was read directly from SQLite instead.

---

## Architecture decisions

### Intermediate representation (IR)
Rather than translating directly between RSL text and Studio 5000 text, all formats are parsed into a shared Python data structure first:

```
Instruction(name, operands)
Branch(legs)           ← parallel branch, any number of legs
Rung(number, elements, comment)
Routine(name, rungs)
Program(name, routines, tags)
PLCProject(name, programs)
```

This gives clean separation: parsers only read, writers only write, and translation (address↔tag mapping) is applied to the IR in between.

### Address mapping
The fundamental difference between the two platforms: RSLogix 500 uses **physical I/O addresses** (`I:1/0`, `O:2/0`) while Studio 5000 uses **named tags** (`Start_PB_1`, `Motor_1`). A mapping file bridges them:

```json
{
  "addresses": {
    "Motor_1": "O:2/0",
    "Start_PB_1": "I:1/0"
  }
}
```

The tool auto-generates a mapping (with heuristics: tags used in OTE/OTL/OTU become outputs, others become inputs) but the user is expected to review and edit it to match their actual hardware wiring before downloading to a PLC.

### Branch syntax translation
RSL uses sequential markers: `BST,1 ... NXB,1 ... BND,1`  
Studio 5000 uses bracket notation: `[leg1 ,leg2 ]`

The IR `Branch` node captures the structure abstractly. A stack-based parser handles RSL; a recursive-descent character-level parser handles Studio 5000 text. Both emit and consume the same `Branch` IR node.

**Bug found and fixed during development:** The Studio 5000 parser initially produced only one branch leg. Root cause: `skip_ws()` consumed the space before the `,` leg separator, so the separator detection (which looked for ` ,`) failed silently. Fix: detect bare `,` as well as ` ,` in the branch parser loop.

### ACD write direction
Direct ACD binary output was evaluated but deferred. It would require:
1. Reversing the `@hex_id@` tag reference format in `SbRegion.Dat`
2. Re-serializing the Kaitai Struct binary record format
3. Updating `Comps.Dat` with new tag entries

Instead, the tool generates L5X (importable via Studio 5000 File → Import), which covers the practical use case without the binary complexity.

---

## Components built

### `converter/ir.py`
Data classes for the intermediate representation. Uses `from __future__ import annotations` for forward references in the recursive `Branch` type.

### `converter/rsl_parser.py`
- Reads RSL line by line
- Detects `FILE,LAD N:` and `FILE,SBR N:` headers to create Program objects
- Parses `SOR...EOR` rung lines using a token stream with a recursive branch handler
- Extracts rung comments from `RCM,` lines
- Infers tag data types from SLC-500 address prefixes (T→TIMER, C→COUNTER, etc.)

### `converter/acd_reader.py`
- Uses `acd-tools` `ExportL5x` to extract ACD into a temp SQLite database
- Queries the `rungs` table for rung text
- Parses Studio 5000 rung text using `_S5KParser` (recursive-descent character parser)
- Infers tag data types from instruction context (TON→TIMER, CTU→COUNTER, etc.)

### `converter/translate.py`
- `auto_map_from_acd`: generates tag→address mapping by scanning rung usage
- `auto_map_from_rsl`: generates address→tag mapping with readable default names
- `apply_map`: applies any mapping dict to a PLCProject (works in both directions)
- `load_map` / `save_map`: JSON file I/O

### `converter/rsl_writer.py`
- Renders IR to RSLogix 500 RSL format
- Assigns sequential `BST/NXB/BND` branch numbers using a counter per rung
- Appends standard empty data tables (BTBL/NTBL/FTBL), subroutine stubs (SBR 3-9), and SYMBOLS/DESCRS sections to match expected RSL structure

### `converter/l5x_writer.py`
- Renders IR to Logix 5000 XML (Studio 5000 importable)
- Targets `ProcessorType="1769-L18ERM/A"` (CompactLogix), `MajorRev="35"`
- Includes Tags, Programs, Routines with RLLContent, and Task scheduling
- Uses `minidom` for pretty-printed XML output

### `converter/main.py`
CLI entry point with subcommands: `show`, `genmap`, `acd2rsl`, `acd2l5x`, `rsl2l5x`, `rsl2rsl`

### `wizard.py`
Interactive step-by-step wizard for non-technical users:
- Uses `rich` for terminal formatting (panels, tables, rules)
- Uses `tkinter.filedialog` for native Windows file picker
- Walks through: file selection → summary → address mapping → conversion → output location → next steps
- Offers to save mapping JSON for reuse
- Loops back to main menu

### `run.bat`
Windows bootstrap launcher:
1. Checks `python` exists in PATH; if not, opens python.org with install instructions
2. Checks Python ≥ 3.8; if too old, same guidance
3. Auto-installs `acd-tools` and `rich` via pip (quiet, fast no-op when current)
4. Launches `wizard.py`

### `wizard.py` self-bootstrap (`_bootstrap()`)
Runs before any third-party import. If packages are missing (e.g., user ran `python wizard.py` directly), installs them and relaunches the script. Covers the case where `run.bat` was bypassed.

---

## Key decisions log

| Decision | Rationale |
|---|---|
| L5X as pivot format rather than direct ACD binary write | ACD binary format requires reverse-engineering `SbRegion.Dat` Kaitai Struct records and recreating `@hex_id@` tag references — significant complexity for a use case covered by Studio 5000's own import |
| Normalized IR in Studio 5000 bracket syntax | ACD rung text is already in this form; RSL can be cleanly mapped to/from it; it's the more expressive of the two notations |
| Auto-map with user review rather than forced manual entry | Users shouldn't need to understand SLC-500 addressing to do a first test run; the hardware-wiring alignment step is flagged clearly as "verify before downloading" |
| `run.bat` + `wizard.py` dual bootstrap | `run.bat` handles the Python-not-installed case (can't be handled in Python); `wizard.py` handles missing pip packages for users who bypass the bat file |
| `tkinter` file dialog over typed paths | Non-technical users should never have to type a file path |
| Platform clarification (macOS → Windows) | Initial implementation assumed macOS; corrected after user pointed out that RSLogix 500 and Studio 5000 are Windows-only applications. Fixed: `root.attributes("-topmost", True)` replacing macOS-specific Tcl/Tk call; all README instructions rewritten for Command Prompt; added `run.bat` double-click launcher |

---

## Known limitations

- **Direct ACD output not supported** — produces L5X for Studio 5000 import instead
- **Studio 5000-only instructions not converted** — motion (`MAM`, `MAS`), safety, `MSG`, AOIs pass through verbatim; RSLogix 500 will not recognize them
- **One program per RSL file** — RSLogix 500's LAD 2 + SBR 3-9 structure; multi-program ACD projects must be split manually
- **Hardware configuration not transferred** — I/O module rack/slot setup must be redone in RSLogix 500; only logic transfers
- **Address mapping is heuristic** — auto-assigned addresses may not match real wiring; user must review before downloading to hardware

---

## Files delivered

```
run.bat                   Windows launcher (double-click to start)
wizard.py                 Interactive wizard
README.md                 User documentation
requirements.txt          pip dependency reference
.gitignore                Excludes ACD/RSL/L5X files and build artifacts
converter/
  __init__.py
  ir.py                   Intermediate representation data classes
  acd_reader.py           ACD → IR
  rsl_parser.py           RSL → IR
  translate.py            Address ↔ tag mapping
  rsl_writer.py           IR → RSL
  l5x_writer.py           IR → L5X
  main.py                 CLI entry point
```

Repo: https://github.com/dpasseraHOME/rsl-to-acd
