"""
PLC Ladder Logic Converter — interactive wizard.
Run with:  python wizard.py
"""

import os
import sys
import re
from pathlib import Path


# ── Bootstrap: auto-install missing pip packages ──────────────────────────
# Runs before any third-party import so the script is self-healing when
# launched directly (python wizard.py) without going through run.bat.

def _can_import(name: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(name) is not None


def _bootstrap():
    packages = {
        "rich":          "rich",
        "acd":           "acd-tools",
        "kaitaistruct":  "kaitaistruct",
        "loguru":        "loguru",
    }
    missing = [pip for mod, pip in packages.items() if not _can_import(mod)]
    if not missing:
        return

    print()
    print(f"  Installing required packages: {', '.join(missing)}")
    print("  This only happens once. Please wait...")
    print()

    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install"] + missing
    )

    if result.returncode != 0:
        print()
        print("  Installation failed.")
        print(f"  Please run this command manually and then restart the tool:")
        print()
        print(f"      pip install {' '.join(missing)}")
        print()
        input("  Press Enter to exit...")
        sys.exit(1)

    print()
    print("  Setup complete. Starting the converter...")
    print()
    # Relaunch so the freshly-installed modules are importable
    import subprocess as _sp
    _sp.run([sys.executable, __file__] + sys.argv[1:])
    sys.exit(0)


_bootstrap()
# ─────────────────────────────────────────────────────────────────────────


from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.rule import Rule
from rich import box

# Add the project folder to the path so converter package is importable
sys.path.insert(0, str(Path(__file__).parent))

from converter import acd_reader, rsl_parser, rsl_writer, l5x_writer, translate
from converter.l5x_writer import elements_to_s5k

console = Console()

# ── Visual helpers ────────────────────────────────────────────────────────

def header(text: str):
    console.print()
    console.print(Rule(f"[bold cyan]{text}[/bold cyan]", style="cyan"))
    console.print()

def success(text: str):
    console.print(f"  [bold green]✓[/bold green]  {text}")

def warn(text: str):
    console.print(f"  [bold yellow]![/bold yellow]  {text}")

def info(text: str):
    console.print(f"     {text}")

def ask(prompt: str, default: str = "") -> str:
    if default:
        return console.input(f"  [bold]{prompt}[/bold] [[dim]{default}[/dim]]: ").strip() or default
    return console.input(f"  [bold]{prompt}[/bold]: ").strip()

def menu(options: list[str]) -> int:
    """Display a numbered menu, return 1-based choice."""
    for i, opt in enumerate(options, 1):
        console.print(f"  [bold cyan]{i}[/bold cyan]  {opt}")
    console.print()
    while True:
        raw = console.input("  [bold]Choice:[/bold] ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw)
        warn(f"Please enter a number between 1 and {len(options)}.")

def confirm(prompt: str, default: bool = True) -> bool:
    hint = "[dim](Y/n)[/dim]" if default else "[dim](y/N)[/dim]"
    raw = console.input(f"  {prompt} {hint}: ").strip().lower()
    if not raw:
        return default
    return raw.startswith("y")

# ── File selection ────────────────────────────────────────────────────────

def pick_file(prompt: str, extension: str) -> Path:
    """Ask the user to select a file. Try a native dialog first, fall back to typed path."""
    console.print(f"  {prompt}")
    console.print()

    path = _try_dialog(extension)
    if path is None:
        console.print("  [dim](Type or paste the full file path, then press Enter)[/dim]")
        raw = console.input("  [bold]File path:[/bold] ").strip().strip('"').strip("'")
        path = Path(raw)

    if not path.exists():
        console.print(f"\n  [red]File not found:[/red] {path}\n")
        return pick_file(prompt, extension)

    if path.suffix.lower() != extension:
        warn(f"Expected a {extension.upper()} file — got {path.suffix.upper()}. Continue anyway?")
        if not confirm("Continue?", default=False):
            return pick_file(prompt, extension)

    success(f"[bold]{path.name}[/bold]  [dim]({path.parent})[/dim]")
    return path


def _try_dialog(extension: str) -> Path | None:
    """Attempt to open a native file-picker dialog (works on Windows and macOS)."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)  # bring dialog to front on Windows
        ext_label = extension.upper().lstrip(".")
        result = filedialog.askopenfilename(
            title=f"Select a {ext_label} file",
            filetypes=[(f"{ext_label} files", f"*{extension}"), ("All files", "*.*")],
        )
        root.destroy()
        if result:
            return Path(result)
    except Exception:
        pass
    return None


def pick_save_path(suggested: Path) -> Path:
    """Ask where to save the output file."""
    console.print()
    console.print(f"  [dim]Suggested output location:[/dim]")
    console.print(f"  [bold]{suggested}[/bold]")
    console.print()
    change = confirm("Save to this location?", default=True)
    if change:
        return suggested
    console.print("  [dim](Type or paste the full path including filename)[/dim]")
    raw = console.input("  [bold]Save as:[/bold] ").strip().strip('"').strip("'")
    return Path(raw)


# ── File summary ──────────────────────────────────────────────────────────

def show_file_summary(project):
    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()

    total_rungs = sum(
        len([r for r in routine.rungs if not r.is_empty and not r.is_end])
        for prog in project.programs
        for routine in prog.routines
    )
    total_tags = sum(len(p.tags) for p in project.programs)
    prog_names = [p.name for p in project.programs if p.routines and not p.name.startswith("Subroutine_")]

    table.add_row("Project", f"[bold]{project.name}[/bold]")
    table.add_row("Programs", ", ".join(prog_names) or "—")
    table.add_row("Total rungs", str(total_rungs))
    table.add_row("Total tags", str(total_tags))
    console.print(table)


def show_rungs(project):
    for prog in project.programs:
        if prog.name.startswith("Subroutine_"):
            continue
        for routine in prog.routines:
            active = [r for r in routine.rungs if not r.is_empty and not r.is_end]
            if not active:
                continue
            console.print(f"  [bold]{prog.name} / {routine.name}[/bold]")
            for rung in active:
                text = elements_to_s5k(rung.elements)
                comment = f"  [dim italic]{rung.comment}[/dim italic]" if rung.comment else ""
                console.print(f"    [cyan][{rung.number}][/cyan] {text}{comment}")
    console.print()


# ── Address mapping wizard ────────────────────────────────────────────────

def _is_valid_address(addr: str) -> bool:
    patterns = [
        r"^[IO]:\d+/\d+$",     # I:1/0  O:2/3
        r"^[IO]:\d+\.\d+/\d+$",# I:1.0/0 (word.bit)
        r"^T\d+:\d+$",          # T4:0
        r"^C\d+:\d+$",          # C5:2
        r"^N\d+:\d+$",          # N7:0
        r"^F\d+:\d+$",          # F8:0
        r"^B\d+:\d+/\d+$",      # B3:0/0
        r"^B\d+/\d+$",          # B3/0
    ]
    return any(re.match(p, addr) for p in patterns)


def address_mapping_step(project, source_ext: str) -> dict:
    """
    Guide the user through assigning SLC-500 addresses to Studio 5000 tags
    (or vice versa for RSL source). Returns the final mapping dict.
    """
    header("Step 2 of 3: I/O Address Mapping")

    if source_ext == ".acd":
        auto_map = translate.auto_map_from_acd(project)
        direction = "tag → SLC-500 address"
        col_a, col_b = "Studio 5000 Tag", "SLC-500 Address"
    else:
        auto_map = translate.auto_map_from_rsl(project)
        direction = "SLC-500 address → tag name"
        col_a, col_b = "SLC-500 Address", "Tag Name"

    console.print(f"  Your program has [bold]{len(auto_map)}[/bold] tags that need addresses. ({direction})")
    console.print()

    _print_mapping_table(auto_map, col_a, col_b)

    console.print()
    choice = menu([
        "Use suggested addresses  (quick start — you can always change later)",
        "Edit addresses manually  (recommended before downloading to hardware)",
        "Load from a saved mapping file",
    ])

    if choice == 1:
        return auto_map

    if choice == 2:
        return _edit_mapping(auto_map, source_ext)

    # choice == 3 — load from file
    return _load_mapping_from_file(auto_map, source_ext)


def _print_mapping_table(mapping: dict, col_a: str, col_b: str):
    table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
    table.add_column("#", style="dim", justify="right", width=4)
    table.add_column(col_a, style="bold")
    table.add_column(col_b, style="cyan")
    for i, (k, v) in enumerate(mapping.items(), 1):
        table.add_row(str(i), k, v)
    console.print(table)


def _edit_mapping(mapping: dict, source_ext: str) -> dict:
    console.print()
    console.print("  Press [bold]Enter[/bold] to keep the suggested address, or type a new one.")
    console.print()

    if source_ext == ".acd":
        console.print("  [dim]Input format:  I:slot/bit  (e.g. I:1/0)  for inputs[/dim]")
        console.print("  [dim]              O:slot/bit  (e.g. O:2/0)  for outputs[/dim]")
        console.print("  [dim]              T4:n                       for timers[/dim]")
        console.print("  [dim]              C5:n                       for counters[/dim]")
    else:
        console.print("  [dim]Type a descriptive tag name, e.g. Motor_1, Start_PB_1[/dim]")
    console.print()

    result = {}
    for tag, suggested in mapping.items():
        while True:
            raw = console.input(f"  [bold]{tag:30s}[/bold] [[cyan]{suggested}[/cyan]]: ").strip()
            value = raw if raw else suggested

            if source_ext == ".acd" and raw and not _is_valid_address(value):
                warn(f"'{value}' doesn't look like a valid SLC-500 address. Try again or press Enter to use the suggestion.")
                continue
            break
        result[tag] = value

    console.print()
    console.print("  [bold]Final mapping:[/bold]")
    _print_mapping_table(result, "Tag", "Address")
    return result


def _load_mapping_from_file(fallback: dict, source_ext: str) -> dict:
    console.print()
    path = pick_file("Select your mapping .json file", ".json")
    try:
        mapping = translate.load_map(path)
        success(f"Loaded {len(mapping)} entries from {path.name}")
        _print_mapping_table(mapping, "Tag / Address", "Value")

        # Fill in any tags that were missing from the file
        missing = {k: v for k, v in fallback.items() if k not in mapping}
        if missing:
            warn(f"{len(missing)} tag(s) not found in mapping file — auto-assigning:")
            for k, v in missing.items():
                info(f"  {k} → {v}")
                mapping[k] = v
        return mapping
    except Exception as e:
        warn(f"Could not read mapping file: {e}")
        if confirm("Use auto-suggested addresses instead?", default=True):
            return fallback
        return _load_mapping_from_file(fallback, source_ext)


# ── Conversion steps ──────────────────────────────────────────────────────

def load_project(path: Path):
    console.print()
    with console.status("  Reading file..."):
        try:
            if path.suffix.lower() == ".acd":
                project = acd_reader.read_file(path)
            else:
                project = rsl_parser.parse_file(path)
        except Exception as e:
            console.print(f"\n  [red]Could not read file:[/red] {e}\n")
            sys.exit(1)
    return project


def do_convert(project, mapping, output_path: Path, output_format: str):
    if mapping:
        project = translate.apply_map(project, mapping)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with console.status("  Converting..."):
        if output_format == "rsl":
            rsl_writer.write_file(project, output_path)
        else:
            l5x_writer.write_file(project, output_path)

    console.print()
    success("[bold]Conversion complete![/bold]")
    console.print()
    console.print(Panel(
        f"[bold white]{output_path}[/bold white]",
        title="[green]Output file[/green]",
        border_style="green",
        padding=(0, 2),
    ))
    console.print()


def offer_save_mapping(mapping: dict, input_path: Path):
    if not confirm("Save this address mapping for future use?", default=True):
        return
    default_path = input_path.with_name(input_path.stem + "_mapping.json")
    save_path = pick_save_path(default_path)
    translate.save_map(mapping, save_path)
    success(f"Mapping saved: [bold]{save_path.name}[/bold]")


def show_next_steps(output_format: str, output_path: Path):
    if output_format == "rsl":
        console.print("  [bold]Next step in RSLogix 500:[/bold]")
        console.print("  1. Open RSLogix 500")
        console.print("  2. Go to  [bold]File → Import[/bold]")
        console.print(f"  3. Select  [bold cyan]{output_path.name}[/bold cyan]")
    else:
        console.print("  [bold]Next step in Studio 5000:[/bold]")
        console.print("  1. Open Studio 5000")
        console.print("  2. Go to  [bold]File → Import[/bold]")
        console.print(f"  3. Select  [bold cyan]{output_path.name}[/bold cyan]")
    console.print()


# ── Wizard flows ──────────────────────────────────────────────────────────

def wizard_acd_to_rsl():
    header("Step 1 of 3: Select your ACD file")
    input_path = pick_file("Select the ACD file you exported from Studio 5000.", ".acd")
    project = load_project(input_path)
    show_file_summary(project)

    if confirm("Show the ladder rungs?", default=False):
        show_rungs(project)

    mapping = address_mapping_step(project, ".acd")

    header("Step 3 of 3: Convert and Save")
    default_out = input_path.with_suffix(".RSL")
    output_path = pick_save_path(default_out)

    if output_path.exists():
        if not confirm(f"[yellow]{output_path.name}[/yellow] already exists. Overwrite?", default=False):
            return

    do_convert(project, mapping, output_path, "rsl")
    offer_save_mapping(mapping, input_path)
    show_next_steps("rsl", output_path)


def wizard_acd_to_l5x():
    header("Step 1 of 2: Select your ACD file")
    input_path = pick_file("Select the ACD file you exported from Studio 5000.", ".acd")
    project = load_project(input_path)
    show_file_summary(project)

    if confirm("Show the ladder rungs?", default=False):
        show_rungs(project)

    header("Step 2 of 2: Convert and Save")
    default_out = input_path.with_suffix(".L5X")
    output_path = pick_save_path(default_out)

    if output_path.exists():
        if not confirm(f"[yellow]{output_path.name}[/yellow] already exists. Overwrite?", default=False):
            return

    do_convert(project, None, output_path, "l5x")
    show_next_steps("l5x", output_path)


def wizard_rsl_to_l5x():
    header("Step 1 of 3: Select your RSL file")
    input_path = pick_file("Select the RSL file exported from RSLogix 500.", ".rsl")
    project = load_project(input_path)
    show_file_summary(project)

    if confirm("Show the ladder rungs?", default=False):
        show_rungs(project)

    mapping = address_mapping_step(project, ".rsl")

    header("Step 3 of 3: Convert and Save")
    default_out = input_path.with_suffix(".L5X")
    output_path = pick_save_path(default_out)

    if output_path.exists():
        if not confirm(f"[yellow]{output_path.name}[/yellow] already exists. Overwrite?", default=False):
            return

    do_convert(project, mapping, output_path, "l5x")
    offer_save_mapping(mapping, input_path)
    show_next_steps("l5x", output_path)


def wizard_inspect():
    header("Inspect a File")
    console.print("  Supported formats: ACD (Studio 5000)  and  RSL (RSLogix 500)")
    console.print()
    raw = console.input("  [bold]Select file (Enter for file picker):[/bold] ").strip()
    if not raw:
        # Try ACD first, then RSL
        choice = menu(["ACD file  (Studio 5000)", "RSL file  (RSLogix 500)"])
        ext = ".acd" if choice == 1 else ".rsl"
        input_path = pick_file("Select the file to inspect.", ext)
    else:
        input_path = Path(raw.strip('"').strip("'"))

    project = load_project(input_path)
    console.print()
    show_file_summary(project)
    show_rungs(project)


# ── Entry point ───────────────────────────────────────────────────────────

def main():
    console.print()
    console.print(Panel(
        "[bold white]PLC Ladder Logic Converter[/bold white]\n"
        "[dim]Studio 5000 (ACD)  ↔  RSLogix 500 (RSL)[/dim]",
        border_style="cyan",
        padding=(1, 4),
    ))
    console.print()
    console.print(
        "  This tool converts ladder logic programs between [bold]Studio 5000[/bold]\n"
        "  and [bold]RSLogix 500[/bold]. No programming knowledge required.\n"
    )

    while True:
        header("Main Menu")
        choice = menu([
            "[bold]ACD → RSL[/bold]   Convert a Studio 5000 program for use in RSLogix 500",
            "[bold]ACD → L5X[/bold]   Export a Studio 5000 program to Logix XML",
            "[bold]RSL → L5X[/bold]   Convert an RSLogix 500 program for use in Studio 5000",
            "[bold]Inspect[/bold]     View the programs and rungs inside any file",
            "[bold]Exit[/bold]",
        ])

        if choice == 1:
            wizard_acd_to_rsl()
        elif choice == 2:
            wizard_acd_to_l5x()
        elif choice == 3:
            wizard_rsl_to_l5x()
        elif choice == 4:
            wizard_inspect()
        elif choice == 5:
            console.print()
            console.print("  Goodbye!\n")
            break

        console.print()
        if not confirm("Return to main menu?", default=True):
            console.print()
            console.print("  Goodbye!\n")
            break


if __name__ == "__main__":
    main()
