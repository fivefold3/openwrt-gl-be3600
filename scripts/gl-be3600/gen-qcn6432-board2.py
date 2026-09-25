#!/usr/bin/env python3
"""Build an ath12k board-2.bin container for the GL-BE3600 from GL's stock
board-data files - for BOTH radios, despite the historical filename.

Why every variant, not just one unit's: the firmware reports a board id
(the tested unit: 0x1b for the IPQ5332 radio, 0x60 for the QCN6432) and ath12k
looks up "bus=ahb,qmi-chip-id=0,qmi-board-id=<id>" in the container, then
falls back to "...qmi-board-id=255". A container with a single entry works
only on units that report that exact id. GL ships 8 plain IPQ5332 variants
and 12 plain QCN6432 variants (hardware revisions, region SKUs, antenna
options); a unit reporting any other id would get no board data at all,
which is the 0 dBm tx-power failure this port already hit once. So the
container carries every plain "bdwlan.b<id>" plus "bdwlan.bin" as the 255
default, and any unit matches.

Suffixed variants (bdwlanKCT.b12, bdwlan_2G_HP.b42, bdwlan_HP.b0052, ...)
are deliberately skipped: they share a board id with a plain file and are
selected by a "variant=" tag that the DT (qcom,ath12k-calibration-variant)
does not set, so including them would only create duplicate names for one
id and the driver would take whichever came first.

Container layout (ath12k core.c): magic "QCA-ATH12K-BOARD" NUL-padded to 20
bytes, then TLVs <u32 type, u32 len, data, pad4>: type 0 = board entry
(inner TLVs: 0 = name, 1 = data), type 1 = regdb. Names carry no NUL -
ath12k_core_parse_bd_ie_board() requires len == strlen(name).

The regdb source is explicit and normally omitted: ath12k prefers a REGDB IE
inside board-2.bin over regdb.bin, and its fallback name matches a 255 TLV,
so an embedded regdb silently overrides the stock regdb.bin. Leaving it out
makes the driver load regdb.bin, which is the stock database.

Usage:
  gen-qcn6432-board2.py <bdwlan file or directory> <out board-2.bin> [regdb source]

  IPQ5332 (2.4 GHz):
    python3 scripts/gl-be3600/gen-qcn6432-board2.py <stock>/wifi_fw \\
        package/firmware/ipq-wifi/src/board-glinet_gl-be3600.ipq5332
  QCN6432 (5 GHz):
    python3 scripts/gl-be3600/gen-qcn6432-board2.py <stock>/wifi_fw/qcn6432 \\
        files/lib/firmware/ath12k/QCN6432/hw1.0/board-2.bin
"""
import hashlib
import os
import re
import struct
import sys

MAGIC = b"QCA-ATH12K-BOARD"
NAME_FMT = "bus=ahb,qmi-chip-id=0,qmi-board-id=%d"
DEFAULT_ID = 255
PLAIN = re.compile(r"^bdwlan\.b([0-9a-fA-F]+)$")


def tlv(t, payload):
    return struct.pack("<II", t, len(payload)) + payload + b"\0" * ((-len(payload)) % 4)


def read_tlvs(blob, off):
    out = []
    while off + 8 <= len(blob):
        t, l = struct.unpack_from("<II", blob, off)
        off += 8
        out.append((t, blob[off:off + l]))
        off += (l + 3) & ~3
    return out


def board_id_of(fname):
    """bdwlan.b1b -> 27, bdwlan.b0060 -> 96 (the suffix is hex); None if not plain."""
    m = PLAIN.match(fname)
    return int(m.group(1), 16) if m else None


def collect(src):
    """Return [(board_id, path)] sorted by id; a directory yields every plain
    variant plus bdwlan.bin as the 255 default, a single file yields one."""
    if os.path.isdir(src):
        entries = []
        skipped = []
        for f in sorted(os.listdir(src)):
            if f == "bdwlan.bin":
                entries.append((DEFAULT_ID, os.path.join(src, f)))
                continue
            bid = board_id_of(f)
            if bid is None:
                if f.startswith("bdwlan"):
                    skipped.append(f)
                continue
            entries.append((bid, os.path.join(src, f)))
        if skipped:
            print("skipping suffixed variants (need a DT variant= tag): "
                  + " ".join(skipped), file=sys.stderr)
        return sorted(entries)
    bid = board_id_of(os.path.basename(src))
    if bid is None:
        sys.exit(f"cannot derive a board id from {src!r}; expected bdwlan.b<hex>")
    return [(bid, src)]


def main(src, out_path, regdb_path=None):
    entries = collect(src)
    if not entries:
        sys.exit(f"no bdwlan.b* files under {src}")
    ids = [b for b, _ in entries]
    if len(ids) != len(set(ids)):
        sys.exit(f"duplicate board ids: {ids}")
    if DEFAULT_ID not in ids:
        print("WARNING: no bdwlan.bin -> no qmi-board-id=255 default; an unknown "
              "board id will get no board data", file=sys.stderr)

    blob = MAGIC + b"\0" * (20 - len(MAGIC))
    for bid, path in entries:
        data = open(path, "rb").read()
        name = (NAME_FMT % bid).encode()
        blob += tlv(0, tlv(0, name) + tlv(1, data))
        print(f"  {os.path.basename(path):16s} -> {name.decode():44s} {len(data):7d} B "
              f"md5 {hashlib.md5(data).hexdigest()[:12]}", file=sys.stderr)

    if regdb_path:
        rb = open(regdb_path, "rb").read()
        if rb.startswith(MAGIC):
            for t, p in read_tlvs(rb, 20):
                if t == 1:
                    blob += tlv(1, p)
            print(f"regdb: carried TLV(s) from container {regdb_path}", file=sys.stderr)
        else:
            blob += tlv(1, tlv(0, (NAME_FMT % DEFAULT_ID).encode()) + tlv(1, rb))
            print(f"regdb: embedded raw {regdb_path} ({len(rb)} B)", file=sys.stderr)
    else:
        print("no regdb TLV embedded; the driver will load regdb.bin", file=sys.stderr)

    with open(out_path, "wb") as f:
        f.write(blob)
    print(f"wrote {out_path}: {len(entries)} board entries, {len(blob)} B, "
          f"md5 {hashlib.md5(blob).hexdigest()}", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        sys.exit(__doc__)
    main(*sys.argv[1:])
