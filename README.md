# PLC Ladder Logic Converter

Converts ladder logic programs between two Rockwell Automation formats:

- **ACD** — the native project format for Studio 5000 / RSLogix 5000, used with ControlLogix and CompactLogix PLCs
- **RSL** — the text export format for RSLogix 500, used with SLC-500 and MicroLogix PLCs

## Why this exists

Studio 5000 and RSLogix 500 serve different hardware generations and don't talk to each other natively. If you're designing in Studio 5000 but need to implement the same logic on older SLC-500 hardware — or if you receive an RSL file and need to bring it into a modern project — this tool bridges that gap.

The converter also produces **L5X** (Logix 5000 XML), Rockwell's human-readable interchange format, which Studio 5000 can import directly and which is useful for version control, inspection, and scripting.

---

## Setup (one time only)

**1. Make sure Python is installed.**

Open Command Prompt (search for `cmd` in the Start menu) and run:
```
python --version
```
You should see `Python 3.8` or higher. If you see an error or are taken to the Microsoft Store, download Python from [python.org](https://www.python.org/downloads/). During installation, check the box that says **"Add Python to PATH"**.

**2. Install the required libraries.**

In Command Prompt, run:
```
pip install acd-tools rich
```

That's it. You're ready to use the tool.

---

## How to run it

**Option A — Double-click (easiest):**
Double-click `run.bat` in the `Ladder Programming` folder. The wizard opens in a Command Prompt window.

**Option B — Command Prompt:**
Open Command Prompt, navigate to the `Ladder Programming` folder, and run:
```
python wizard.py
```

A step-by-step guide will walk you through everything from there. No other commands are needed for typical use.

---

## What the wizard does

When you run `wizard.py` you'll see a menu:

```
  1  ACD → RSL   Convert a Studio 5000 program for use in RSLogix 500
  2  ACD → L5X   Export a Studio 5000 program to Logix XML
  3  RSL → L5X   Convert an RSLogix 500 program for use in Studio 5000
  4  Inspect     View the programs and rungs inside any file
  5  Exit
```

**ACD → RSL** is the primary workflow: you built something in Studio 5000, and you want to open it in RSLogix 500.

The wizard will:
1. Ask you to select your ACD file (opens a file picker)
2. Show you what's in the file — programs, tags, rungs
3. Walk you through the address mapping step (explained below)
4. Run the conversion
5. Tell you exactly where the output file was saved
6. Remind you how to import it in RSLogix 500

---

## The one concept to understand: address mapping

Studio 5000 uses **named tags** (`Motor_1`, `Stop_PB_1`). RSLogix 500 uses **physical I/O addresses** (`O:2/0`, `I:1/1`). These mean the same things electrically — the two platforms just refer to them differently.

During conversion, the wizard shows you each tag and its suggested SLC-500 address:

```
  #   Studio 5000 Tag   SLC-500 Address
  ─────────────────────────────────────
  1   Motor_1           O:2/0
  2   Motor_2           O:2/1
  3   Start_PB_1        I:1/0
  4   Stop_PB_1         I:1/3
  ...
```

You can accept the suggestions, edit them to match your real hardware wiring, or load a mapping you saved from a previous run.

**For a first test run**, accepting the suggestions is fine. Just know that the resulting RSL will use generic addresses — verify them against your wiring diagram before downloading to hardware.

The wizard offers to save the mapping as a `.json` file at the end. Save it and reuse it next time to skip the address step entirely.

**SLC-500 address format reference:**

| Format | Meaning | Example |
|---|---|---|
| `I:slot/bit` | Digital input | `I:1/0` = slot 1, bit 0 |
| `O:slot/bit` | Digital output | `O:2/0` = slot 2, bit 0 |
| `T4:n` | Timer (file 4) | `T4:0` = first timer |
| `C5:n` | Counter (file 5) | `C5:0` = first counter |
| `N7:n` | Integer (file 7) | `N7:0` = first integer |
| `B3:n/bit` | Internal bit (file 3) | `B3:0/0` |

---

## After converting: importing into RSLogix 500

1. Open RSLogix 500
2. Go to **File → Import**
3. Select the `.RSL` file the wizard produced

RSLogix 500 will create a new project from the file. Verify the rungs look correct before downloading to hardware.

---

## Limitations

**Instruction compatibility.** Both platforms share most common instructions: `XIC`, `XIO`, `OTE`, `OTL`, `OTU`, `TON`, `TOF`, `CTU`, `CTD`, `MOV`, `ADD`, `SUB`, `EQU`, and most compare/math instructions. Instructions that exist only in Studio 5000 — motion control (`MAM`, `MAS`), safety, `MSG`, Add-On Instructions (AOIs), and advanced process control — have no SLC-500 equivalents. The converter will carry them through verbatim; RSLogix 500 will not recognize them.

**Hardware is different.** An ACD is built for ControlLogix or CompactLogix hardware; an RSL is built for SLC-500 or MicroLogix. A successful conversion means the logic transferred — it does not mean the same physical rack and I/O modules will work. You will need to configure the I/O hardware in RSLogix 500 separately.

**One program per RSL file.** RSLogix 500 projects have one main ladder file (LAD 2) and optional subroutines (SBR 3–9). If your ACD has multiple programs, only the first is converted to the main ladder file.

**Producing ACD output directly.** The converter does not write ACD binary files. ACD output requires Studio 5000 — use the **ACD → L5X** option to produce a file that Studio 5000 can import.

---

## Advanced usage (command line)

If you prefer to skip the wizard and run conversions directly:

```
python -m converter.main <command> [arguments]

Commands:
  show     input.RSL|ACD                           Print a summary of any file
  genmap   input.RSL|ACD  mapping.json             Generate a starter mapping file
  acd2rsl  input.ACD  output.RSL  [--map FILE]     ACD → RSLogix 500 RSL
  acd2l5x  input.ACD  output.L5X                   ACD → Logix 5000 XML
  rsl2l5x  input.RSL  output.L5X  [--map FILE]     RSL → Logix 5000 XML
  rsl2rsl  input.RSL  output.RSL  [--map FILE]     RSL → RSL (apply address remapping)
```

All commands must be run from the `Ladder Programming` folder (the one containing `wizard.py`).

To navigate there in Command Prompt:
```
cd "C:\path\to\Ladder Programming"
```
