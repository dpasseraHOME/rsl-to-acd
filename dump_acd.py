"""
Dump an ACD file into human-readable files for manual inspection.

Usage:
    python dump_acd.py <file.ACD> [output_folder]

Output folder (default: dump/<stem>) will contain:
    TagInfo.xml          -- tag data types and IOI paths (UTF-8)
    project.l5x          -- full L5X XML from acd-tools
    QuickInfo.xml        -- project metadata
    comps_dump.txt       -- every tag's full attribute data as hex + decoded strings
    raw/                 -- all raw extracted binary files from the ACD
"""

import sys
import os
import shutil
import tempfile
from pathlib import Path

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    acd_path = Path(sys.argv[1])
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("dump") / acd_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Reading {acd_path.name} ...")

    from loguru import logger
    logger.disable("acd")
    from acd.l5x.export_l5x import ExportL5x
    from acd.generated.comps.rx_generic import RxGeneric

    tmp = tempfile.mkdtemp()
    try:
        export = ExportL5x(str(acd_path), _temp_dir=tmp)
        cur = export._cur

        # ── 1. Copy and convert XML files ────────────────────────────────
        for fname in ["TagInfo.XML", "QuickInfo.XML"]:
            src = Path(tmp) / fname
            if src.exists():
                raw = src.read_bytes()
                # Detect UTF-16 BOM
                if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
                    text = raw.decode("utf-16")
                else:
                    text = raw.decode("utf-8", errors="replace")
                dest = out_dir / fname.lower().replace("xml", "xml")
                dest.write_text(text, encoding="utf-8")
                print(f"  Written: {dest.name}")

        # ── 2. L5X XML from project object ───────────────────────────────
        try:
            xml = export.project.to_xml()
            (out_dir / "project.l5x").write_text(xml, encoding="utf-8")
            print(f"  Written: project.l5x")
        except Exception as e:
            print(f"  project.l5x: skipped ({e})")

        # ── 3. Full comps dump ────────────────────────────────────────────
        cur.execute("SELECT comp_name, record FROM comps ORDER BY comp_name")
        rows = cur.fetchall()

        lines = []
        lines.append(f"ACD COMPS DUMP: {acd_path.name}")
        lines.append(f"Total records: {len(rows)}")
        lines.append("=" * 80)

        for comp_name, record in rows:
            record = bytes(record)
            lines.append(f"\n[{comp_name}]  ({len(record)} bytes)")

            try:
                r = RxGeneric.from_bytes(record)
                lines.append(f"  cip_type : 0x{r.cip_type:04X}")
                for a in r.extended_records:
                    val = bytes(a.value)
                    # Try to decode as UTF-8 string (first 64 bytes)
                    printable = ""
                    try:
                        s = val[:64].decode("utf-8", errors="ignore")
                        printable = "  text: " + repr(s.strip("\x00"))
                    except Exception:
                        pass
                    # Show all non-zero bytes
                    nonzero = {i: val[i] for i in range(len(val)) if val[i] != 0}
                    lines.append(f"  attr[0x{a.attribute_id:02X}]  ({len(val)} bytes)")
                    if printable:
                        lines.append(f"    {printable}")
                    # Hex dump: 32 bytes per line
                    for offset in range(0, len(val), 32):
                        chunk = val[offset:offset+32]
                        hex_part = " ".join(f"{b:02x}" for b in chunk)
                        # Mark non-zero bytes
                        ann_part = "".join(
                            chr(b) if 32 <= b < 127 else ("." if b == 0 else f"[{b}]")
                            for b in chunk
                        )
                        lines.append(f"    {offset:4d}: {hex_part:<96}  {ann_part}")
            except Exception as e:
                lines.append(f"  [parse error: {e}]")
                lines.append(f"  raw hex: {record.hex()}")

        dump_path = out_dir / "comps_dump.txt"
        dump_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"  Written: comps_dump.txt  ({len(rows)} records)")

        # ── 4. Copy raw extracted files ───────────────────────────────────
        raw_dir = out_dir / "raw"
        raw_dir.mkdir(exist_ok=True)
        for fname in os.listdir(tmp):
            src = Path(tmp) / fname
            if src.is_file():
                shutil.copy2(src, raw_dir / fname)
        print(f"  Written: raw/  ({len(os.listdir(raw_dir))} files)")

        export._db.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nDone. Browse: {out_dir.resolve()}")


if __name__ == "__main__":
    main()
