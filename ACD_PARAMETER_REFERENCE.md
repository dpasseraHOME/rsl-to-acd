# ACD Binary Parameter Reference

How to find where any instruction's parameters are stored in a Rockwell ACD file,
and how to extract them accurately for conversion to RSL/SLC-500.

---

## 1. The Core Distinction: Inline vs. Structured Parameters

Studio 5000 rung text falls into two categories:

### Inline operands — value IS in the rung text
Instructions that operate on BOOL, DINT, INT, SINT, REAL, or literal constants store
their operands directly in the rung text string.

```
MOV(5, MyTag)          → source literal 5 is inline
ADD(TagA, TagB, TagC)  → tag references are inline
GRT(TagA, 100)         → comparison constant 100 is inline
XIC(Tag1)              → bit operand is inline
```

These translate directly — no binary lookup needed.

### Structured operands — value is stored in the tag's data table, `?` is a placeholder
Instructions whose operands are members of a structured tag use `?` as a placeholder
in the rung text. The actual values live elsewhere in the binary.

```
CTU(Counter, ?, ?)     → Counter.PRE and Counter.ACC are NOT in the rung text
TON(MyTimer, ?, ?)     → MyTimer.PRE and MyTimer.ACC are NOT in the rung text
```

**Rule of thumb:** if a rung operand would be a named member of a structured type
(e.g., `Counter.PRE`, `MyTimer.PRE`, `ControlTag.LEN`), Studio 5000 writes `?` instead.

---

## 2. The Parameter Lookup Chain

For any instruction with `?` operands, the chain is:

```
tag name (from rung text)
    ↓  comps table: SELECT record FROM comps WHERE comp_name = <tag_name>
RxTag record (700 bytes typical)
    ↓  record[50:54] as u32 LE = data_table_instance OID
Data table comp OID
    ↓  comps table: SELECT record FROM comps WHERE object_id = <OID>
Data table comp record ($hexhex$ comp, 428 bytes typical)
    ↓  last extended record (attr 0x66)
Tag data bytes
    ↓  interpret per data type structure
PRE, LEN, POS, etc.
```

**Key offset:** `data_table_instance` is always at **absolute byte offset 50** of the tag's
comp record (u32 LE). This is true for both `rx_tag.RxTag` V63/V60 and `RxGeneric.main_record`.

**Data table comp naming:** these comps are named `$hexhex$` (e.g., `$c7c28f94$`). The hex
string is NOT directly derived from the OID — always look up by OID, not by name.

**I/O data table comps** are named `&hexhex:slot:I` or `&hexhex:slot:O`. If
`data_table_instance` resolves to one of these, it is a BOOL I/O tag — skip it in the
preset map (these don't have attr 0x66).

---

## 3. ACD SQLite Record Binary Layout (RxGeneric)

```
Offset   Size  Field
──────────────────────────────────────────────────────────
0        4     parent_id              (u32 LE)
4        4     unique_tag_identifier  (u32 LE)
8        2     record_format_version  (u16 LE)
10       2     cip_type               (u16 LE)  0x68=Tag, 0x6B=DataTable
12       2     comment_id             (u16 LE)
14       60    main_record            (sub-buffer, parsed as RxTag for 0x68/0x6B)
74       4     len_record             (u32 LE, total bytes of extended records area)
78       4     count_record           (u32 LE, total number of records including last)
82+      —     extended records
```

Within the 60-byte `main_record` sub-buffer (absolute offsets from record start):

```
Record offset 50 = main_record[36] → data_table_instance (u32 LE)
```

---

## 4. Extended Record Format

**Regular records** (there are `count_record - 1` of these, parsed by `RxGeneric.extended_records`):

```
attr_id    4 bytes  u32 LE
length     4 bytes  u32 LE  ← actual data length (does NOT include header)
data       <length> bytes
```

**Last record** (1 record, NOT in `RxGeneric.extended_records` — must be parsed manually):

```
attr_id     4 bytes  u32 LE   ← always 0x66 for structured tag data
total_size  4 bytes  u32 LE   ← includes these 4 bytes; actual data = total_size - 4
data        total_size-4 bytes
```

To walk to the last record manually:

```python
offset = 82
for _ in range(r.count_record - 1):
    attr_len = struct.unpack_from("<I", record, offset + 4)[0]
    offset += 8 + attr_len
# now at the last record
attr_id    = struct.unpack_from("<I", record, offset)[0]       # should be 0x66
total_size = struct.unpack_from("<I", record, offset + 4)[0]
data       = record[offset + 8 : offset + 8 + (total_size - 4)]
```

---

## 5. Known Extended Attributes

| attr_id | Typical size | Purpose |
|---------|-------------|---------|
| 0x01    | 288 or 590  | I/O path / logical path. For I/O-aliased BOOL tags: byte 257 = bit#, byte 524 = direction (0x05=input I:1, 0x06=output O:2) |
| 0x64    | 16          | Unknown; always zeros so far |
| 0x65    | 2           | CIP data type code for this tag (see §8) |
| 0x66    | last record | Structured tag data: `(Control DINT, PRE DINT, …)` |
| 0x6B    | last record | Appears in I/O data table comps (`&…` named); ignore |
| 0x82    | 4           | Appears in some Routine/Program comps; ignore |

---

## 6. Known Data Type Structures (from TagInfo.xml)

All sizes are at download time. ACC is a runtime accumulator — not stored in the ACD.
The ACD stores only design-time configuration values (Control flags + PRE).

### COUNTER (attr 0x66 data = 8 bytes)

```
Byte offset  Field    RSL meaning
0–3          Control  Status bits: CU=15, CD=14, DN=13, OV=12, UN=11, UA=10
4–7          PRE      Preset count  → CTU/CTD operand[1] in RSL
```

RSL instruction: `CTU,C5:N,<PRE>,0`

### TIMER (attr 0x66 data = 8 bytes — same layout)

```
Byte offset  Field    RSL meaning
0–3          Control  Status bits: EN=15, TT=14, DN=13
4–7          PRE      Preset in milliseconds  → needs conversion for RSL
```

RSL instruction: `TON,T4:N,<time_base>,<PRE_converted>,0`

⚠️ **Time base conversion required:** Studio 5000 stores PRE in milliseconds.
SLC-500 TON/TOF/RTO use a time base (1.0 s, 0.1 s, or 0.01 s) and a count.
`RSL_count = PRE_ms / (time_base_seconds * 1000)`.
Choose time_base so that the count is a whole number.
Example: PRE=5000 ms → time_base=1.0, RSL_PRE=5.

### CONTROL (for FFL/FFU/BSL/BSR/SQO/SQL/SQC — NOT YET INVESTIGATED)

Studio 5000 CONTROL tag has: Control DINT, LEN DINT, POS DINT (12 bytes total).
These instructions will show `?` operands for LEN. The lookup chain is the same;
the data layout within attr 0x66 needs to be verified empirically.

### PID (NOT YET INVESTIGATED)

Very large structure. Requires a dedicated investigation pass with a known-values ACD.

---

## 7. Instructions with `?` Placeholders (structured tag parameters)

| Instruction | Operands           | Structured type | `?` positions |
|-------------|-------------------|-----------------|---------------|
| CTU         | tag, PRE, ACC     | COUNTER         | [1], [2]      |
| CTD         | tag, PRE, ACC     | COUNTER         | [1], [2]      |
| TON         | tag, PRE, ACC     | TIMER           | [1], [2]      |
| TOF         | tag, PRE, ACC     | TIMER           | [1], [2]      |
| RTO         | tag, PRE, ACC     | TIMER           | [1], [2]      |
| FFL         | source, tag, LEN  | CONTROL         | [2] (LEN)     |
| FFU         | tag, dest, LEN    | CONTROL         | [2] (LEN)     |
| BSL         | arr, tag, bit, LEN| CONTROL         | [3] (LEN)     |
| BSR         | arr, tag, bit, LEN| CONTROL         | [3] (LEN)     |
| SQO/SQL/SQC | tag, mask, source, pos, LEN | CONTROL | multiple |

When a new instruction with `?` operands is encountered, use the investigation
workflow in §9 to find the data.

---

## 8. CIP Data Type Codes (attr 0x65)

| attr 0x65 (hex) | Type    | Notes |
|-----------------|---------|-------|
| `82 8f`         | COUNTER | verified |
| `83 8f`         | TIMER   | to be verified empirically |
| `c1 00`         | BOOL    | CIP standard type 0x00C1 |
| `c4 00`         | DINT    | CIP standard type 0x00C4 |

Use attr 0x65 to quickly confirm which structured type a data table comp holds
before parsing attr 0x66. Add newly discovered codes here.

---

## 9. Investigation Workflow for New Instructions

When encountering a `?` in a new instruction type:

**Step 1 — Create a test ACD with known values.**
Set a counter to PRE=999, a timer to PRE=12345, etc. Use values easy to spot in hex
(e.g., 999 = `0xE7 03 00 00`, 12345 = `0x39 30 00 00`).

**Step 2 — Run the dump tool.**
```bash
python3 dump_acd.py <file.ACD> dump/<stem>
```

**Step 3 — Find the data table comp.**
```python
# In a quick Python session:
cur.execute("SELECT record FROM comps WHERE comp_name=?", (tag_name,))
(record,) = cur.fetchone()
dti = struct.unpack_from("<I", bytes(record), 50)[0]
cur.execute("SELECT comp_name, length(record) FROM comps WHERE object_id=?", (dti,))
print(cur.fetchone())
```

**Step 4 — Search the comps dump for the known value.**
```bash
# 999 in LE hex = e7 03 00 00
grep "e7 03 00 00" dump/<stem>/comps_dump.txt
```

**Step 5 — Read the surrounding bytes.**
In `comps_dump.txt`, find the attr block and count back to the start of the data.
The data layout is: data[0:4]=field0, data[4:8]=field1, etc.

**Step 6 — Verify with a second ACD with different values.**
Change the PRE value in Studio 5000 and dump again. Confirm the same offset
changes.

**Step 7 — Document the result in §6 above.**

---

## 10. Implementation Notes

### What ACC is / is not
ACC (accumulated value) is a runtime counter. It is NOT stored in the ACD project
binary — there are only 8 bytes of attr 0x66 data for COUNTER/TIMER, covering only
Control + PRE. Always emit ACC=0 in RSL output.

### `?` safety net
`converter/translate.py:_translate_operand` contains `if op == "?": return "0"` as a
fallback. This should never fire if `_fill_placeholders` in `acd_reader.py` runs
first. Keep it as a guard.

### Timer time-base selection
Currently **not implemented** — raw PRE ms value is passed through. This will produce
incorrect RSL for timers unless PRE happens to be in whole seconds. Implement a
`_ms_to_slc_timer(pre_ms)` helper that:
1. Returns `(1.0, pre_ms // 1000)` when `pre_ms % 1000 == 0`
2. Returns `(0.01, pre_ms // 10)` when `pre_ms % 10 == 0`
3. Returns `(0.001, pre_ms)` otherwise (if SLC supports 1ms base — verify)

### Where `_fill_placeholders` lives
`converter/acd_reader.py` — called in `_build_project` after `_parse_s5k_rung`.
Preset values come from `_read_tag_maps` → `_parse_pre_from_data_table`.

### Where the counter/timer address is assigned
`converter/translate.py:auto_map_from_acd` — sequential assignment: T4:0, T4:1… / C5:0, C5:1…
The actual tag name is preserved in the mapping so dot-notation members
(Counter.DN → C5:0/13) translate correctly via `_translate_operand`.
