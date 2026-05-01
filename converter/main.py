"""CLI for RSL ↔ ACD / L5X conversion.

Usage
-----
  python main.py rsl2l5x  input.RSL  output.L5X  [--map mapping.json]
  python main.py rsl2rsl  input.RSL  output.RSL  [--map mapping.json]
  python main.py acd2l5x  input.ACD  output.L5X
  python main.py acd2rsl  input.ACD  output.RSL  [--map mapping.json]
  python main.py genmap   input.RSL  mapping.json           (RSL → suggested map)
  python main.py genmap   input.ACD  mapping.json           (ACD → suggested map)
  python main.py show     input.RSL|ACD                     (dump human-readable summary)
"""

from __future__ import annotations
import argparse
import sys
from pathlib import Path

from . import acd_reader, rsl_parser, rsl_writer, l5x_writer, translate
from .ir import PLCProject
from .l5x_writer import elements_to_s5k


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="converter",
        description="RSL ↔ ACD / L5X ladder logic converter",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # rsl2l5x
    p = sub.add_parser("rsl2l5x", help="Convert RSL → L5X")
    p.add_argument("input", help="Input .RSL file")
    p.add_argument("output", help="Output .L5X file")
    p.add_argument("--map", metavar="FILE", help="JSON address→tag mapping")

    # rsl2rsl (round-trip / address rename)
    p = sub.add_parser("rsl2rsl", help="Round-trip RSL (apply address remapping)")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--map", metavar="FILE")

    # acd2l5x
    p = sub.add_parser("acd2l5x", help="Convert ACD → L5X")
    p.add_argument("input", help="Input .ACD file")
    p.add_argument("output", help="Output .L5X file")

    # acd2rsl
    p = sub.add_parser("acd2rsl", help="Convert ACD → RSL")
    p.add_argument("input", help="Input .ACD file")
    p.add_argument("output", help="Output .RSL file")
    p.add_argument("--map", metavar="FILE", help="JSON tag→address mapping")

    # genmap
    p = sub.add_parser("genmap", help="Generate a suggested address/tag mapping file")
    p.add_argument("input", help="Input .RSL or .ACD file")
    p.add_argument("output", help="Output .json mapping file")

    # show
    p = sub.add_parser("show", help="Print a human-readable summary of an RSL or ACD file")
    p.add_argument("input", help="Input .RSL or .ACD file")

    args = parser.parse_args(argv)

    if args.cmd == "rsl2l5x":
        project = _load(args.input)
        if args.map:
            mapping = translate.load_map(args.map)
            project = translate.apply_map(project, mapping)
        l5x_writer.write_file(project, args.output)
        print(f"Written: {args.output}")

    elif args.cmd == "rsl2rsl":
        project = _load(args.input)
        if args.map:
            mapping = translate.load_map(args.map)
            project = translate.apply_map(project, mapping)
        rsl_writer.write_file(project, args.output)
        print(f"Written: {args.output}")

    elif args.cmd == "acd2l5x":
        project = _load(args.input)
        l5x_writer.write_file(project, args.output)
        print(f"Written: {args.output}")

    elif args.cmd == "acd2rsl":
        project = _load(args.input)
        if args.map:
            mapping = translate.load_map(args.map)
        else:
            mapping = translate.auto_map_from_acd(project)
            print("No map provided — auto-assigned SLC-500 addresses.")
            print("Run 'genmap' to inspect and customise the mapping.")
        project = translate.apply_map(project, mapping)
        rsl_writer.write_file(project, args.output)
        print(f"Written: {args.output}")

    elif args.cmd == "genmap":
        project = _load(args.input)
        suffix = Path(args.input).suffix.lower()
        if suffix == ".rsl":
            mapping = translate.auto_map_from_rsl(project)
        else:
            mapping = translate.auto_map_from_acd(project)
        translate.save_map(mapping, args.output)
        print(f"Mapping written: {args.output}")
        for k, v in mapping.items():
            print(f"  {k!r:30s} → {v!r}")

    elif args.cmd == "show":
        project = _load(args.input)
        _print_summary(project)


def _load(path: str) -> PLCProject:
    suffix = Path(path).suffix.lower()
    if suffix == ".rsl":
        return rsl_parser.parse_file(path)
    elif suffix == ".acd":
        return acd_reader.read_file(path)
    else:
        sys.exit(f"Unsupported file type: {suffix}  (expected .rsl or .acd)")


def _print_summary(project: PLCProject) -> None:
    print(f"Project : {project.name}")
    for prog in project.programs:
        print(f"  Program : {prog.name}")
        print(f"    Tags ({len(prog.tags)}):")
        for tag in prog.tags:
            print(f"      {tag.name:30s}  {tag.data_type}")
        for routine in prog.routines:
            active = [r for r in routine.rungs if not r.is_empty and not r.is_end]
            print(f"    Routine : {routine.name}  ({len(active)} rungs)")
            for rung in active:
                text = elements_to_s5k(rung.elements)
                comment = f"  ← {rung.comment}" if rung.comment else ""
                print(f"      [{rung.number}] {text}{comment}")


if __name__ == "__main__":
    main()
