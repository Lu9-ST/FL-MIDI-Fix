#!/usr/bin/env python3
"""
FL MIDI Fix - clean FL Studio MIDI exports
Removes redundant and unnecessary commands/events
(without altering playback whatsoever)
and fixes pitch bend scaling if pitch bends don't surpass 4 semitones
(2 semitones up or down)
(DO NOT use this on regular MIDIs that weren't exported by FL Studio)

Usage:
  python flmidifix.py input.mid output.mid [options]

Options:
    --name "TEXT"         set first-track 'name' (Usually song title)  (FF 03)
    --text "TEXT"         set first-track 'text' (Usually author)      (FF 01)
    --copyright "TEXT"    set first-track 'copyright'                  (FF 02)
    --bend-scale F        rescale pitch bends about centre by F
                          (overrides auto-detection)
    --bend-range N        write RPN 0 = N on bending channels
                          (overrides auto-detection)
    --strip-rpn           remove all RPN 0 data, write none
                          (overrides auto-detection)
    --keep-centre-bend    keep centre bends even on channels that never bend
    --drop-centre-bend    delete ALL pitch-bend events equal to 8192
    --keep-noop           keep redundant channel events
    --keep-meta           keep repeated identical tempo / meta events
                          this will also keep the time signature
                          which FL always exports as 4/4
    --no-running-status   do not re-encode tracks with running status
    --strip-cc N,N...     delete specific CC events      (Mild size reduction,
                                                      potentially destructive)
    --merge-tracks N,N... merges 2 MIDI tracks into one
                          (using this overrides the default merging of
                          tracks 0 and 1)
     --no-merging-tracks  leave the track structure untouched
"""

import struct
import sys


# VLQ / parse
def rd_vlq(b, i):
    v = 0
    while True:
        c = b[i]
        i += 1
        v = (v << 7) | (c & 0x7F)
        if c < 0x80:
            return v, i


def wr_vlq(v):
    out = [v & 0x7F]
    v >>= 7
    while v:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.reverse()
    return bytes(out)


def status_len(status):
    return 1 if (status & 0xF0) in (0xC0, 0xD0) else 2


def parse_track(buf):
    ev, i, run, n = [], 0, None, len(buf)
    while i < n:
        delta, i = rd_vlq(buf, i)
        b = buf[i]
        if b == 0xFF:
            mtype = buf[i + 1]
            ln, j = rd_vlq(buf, i + 2)
            ev.append(
                {
                    "kind": "meta",
                    "delta": delta,
                    "meta": mtype,
                    "payload": buf[j : j + ln],
                }
            )
            i = j + ln
            run = None
        elif b in (0xF0, 0xF7):
            ln, j = rd_vlq(buf, i + 1)
            ev.append(
                {
                    "kind": "sysex",
                    "delta": delta,
                    "status": b,
                    "payload": buf[j : j + ln],
                }
            )
            i = j + ln
            run = None
        else:
            if b & 0x80:
                status = b
                i += 1
                run = status
            else:
                status = run
                if status is None:
                    raise ValueError("data byte with no running status @%d" % i)
            ln = status_len(status)
            ev.append(
                {
                    "kind": "midi",
                    "delta": delta,
                    "status": status,
                    "data": bytes(buf[i : i + ln]),
                }
            )
            i += ln
    return ev


def parse_file(path):
    data = open(path, "rb").read()
    if data[:4] != b"MThd":
        raise SystemExit("%s: not a Standard MIDI File" % path)
    hlen = struct.unpack(">I", data[4:8])[0]
    fmt, ntrk, div = struct.unpack(">HHH", data[8:14])
    pos, tracks = 8 + hlen, []
    while pos + 8 <= len(data) and data[pos : pos + 4] == b"MTrk":
        tlen = struct.unpack(">I", data[pos + 4 : pos + 8])[0]
        tracks.append(parse_track(data[pos + 8 : pos + 8 + tlen]))
        pos += 8 + tlen
    return {"format": fmt, "division": div, "tracks": tracks, "size": len(data)}


# transforms
def _drop(evs, pred):
    kept, carry = [], 0
    for e in evs:
        if pred(e):
            carry += e["delta"]
            continue
        e["delta"] += carry
        carry = 0
        kept.append(e)
    if carry and kept:
        kept[-1]["delta"] += carry
    return kept


def strip_rpn_range(evs, channels=None):
    """Remove the CC6/CC38 data-entry bytes of RPN 0 (pitch-bend range).
    If `channels` is given, only strip for those channels."""
    out, carry, sel = [], 0, {}
    for e in evs:
        if e["kind"] == "midi" and (e["status"] & 0xF0) == 0xB0:
            ch = e["status"] & 0x0F
            if channels is None or ch in channels:
                cc, val = e["data"][0], e["data"][1]
                s = sel.setdefault(ch, [None, None])
                if cc == 101:
                    s[0] = val
                elif cc == 100:
                    s[1] = val
                elif cc in (6, 38) and s == [0, 0]:
                    carry += e["delta"]
                    continue
        e["delta"] += carry
        carry = 0
        out.append(e)
    if carry and out:
        out[-1]["delta"] += carry
    return out


def drop_noop(evs):
    out, carry, state = [], 0, {}
    for e in evs:
        if e["kind"] == "midi":
            nib = e["status"] & 0xF0
            ch = e["status"] & 0x0F
            key = val = None
            if nib == 0xB0 and e["data"][0] < 120:
                key, val = ("cc", ch, e["data"][0]), e["data"][1]
            elif nib == 0xE0:
                key, val = ("bend", ch), e["data"]
            elif nib == 0xC0:
                key, val = ("prog", ch), e["data"]
            if key is not None:
                if state.get(key) == val:
                    carry += e["delta"]
                    continue
                state[key] = val
        e["delta"] += carry
        carry = 0
        out.append(e)
    if carry and out:
        out[-1]["delta"] += carry
    return out


def drop_centre_bends(evs, channels=None):
    """Delete pitch-bend events equal to centre (8192). If `channels` is given,
    only for those channels."""
    return _drop(
        evs,
        lambda e: (
            e["kind"] == "midi"
            and (e["status"] & 0xF0) == 0xE0
            and (channels is None or (e["status"] & 0x0F) in channels)
            and e["data"] == b"\x00\x40"
        ),
    )


def strip_meta(evs, types):
    return _drop(evs, lambda e: e["kind"] == "meta" and e["meta"] in types)


def merge_tracks(tracks, indices):
    """Merge the given 0-indexed tracks into one, preserving every event.
    Interleaves by absolute tick (deltas are relative, so a plain concat
    would corrupt timing), drops the intermediate end-of-track metas and
    keeps a single one at the end."""
    idx = sorted(i for i in set(indices) if 0 <= i < len(tracks))
    if len(idx) < 2:
        return tracks

    merged, end = [], 0
    for i in idx:
        at = 0
        for e in tracks[i]:
            at += e["delta"]
            if e["kind"] == "meta" and e["meta"] == 0x2F:
                end = max(end, at)
                continue
            merged.append((at, e))
            end = max(end, at)

    merged.sort(key=lambda x: x[0])  # stable -> ties keep source order

    out, prev = [], 0
    for at, e in merged:
        e = dict(e)
        e["delta"] = at - prev
        prev = at
        out.append(e)
    out.append({"kind": "meta", "delta": end - prev, "meta": 0x2F, "payload": b""})

    drop = set(idx)
    return [
        out if i == idx[0] else t
        for i, t in enumerate(tracks)
        if i == idx[0] or i not in drop
    ]


def dedupe_meta(evs):
    kept, seen, carry = [], {}, 0
    for e in evs:
        if e["kind"] == "meta" and e["meta"] in (0x51, 0x58, 0x59):
            key = bytes(e["payload"])
            if seen.get(e["meta"]) == key:
                carry += e["delta"]
                continue
            seen[e["meta"]] = key
        e["delta"] += carry
        carry = 0
        kept.append(e)
    if carry and kept:
        kept[-1]["delta"] += carry
    return kept


# encoding
def encode_track(evs, running=False):
    out = bytearray()
    run = None
    for e in evs:
        out += wr_vlq(e["delta"])
        if e["kind"] == "meta":
            out += bytes((0xFF, e["meta"]))
            out += wr_vlq(len(e["payload"]))
            out += e["payload"]
            run = None
        elif e["kind"] == "sysex":
            out.append(e["status"])
            out += wr_vlq(len(e["payload"]))
            out += e["payload"]
            run = None
        else:
            if not (running and run == e["status"]):
                out.append(e["status"])
            out += e["data"]
            run = e["status"]
    return bytes(out)


# analysis
def scan_bends(tracks):
    """Return (ranges, bends).
    ranges[ch] = declared pitch-bend range in semitones (RPN 0); default 2.
    bends[ch]  = (min_dev, max_dev, count) of raw 14-bit deviations."""
    ranges, bends = {}, {}
    for evs in tracks:
        sel = {}
        for e in evs:
            if e["kind"] != "midi":
                continue
            nib = e["status"] & 0xF0
            ch = e["status"] & 0x0F
            if nib == 0xB0:
                cc, val = e["data"][0], e["data"][1]
                s = sel.setdefault(ch, [None, None])
                if cc == 101:
                    s[0] = val
                elif cc == 100:
                    s[1] = val
                elif cc in (6, 38) and s == [0, 0]:
                    if cc == 6:
                        ranges[ch] = val
                    else:
                        ranges[ch] = ranges.get(ch, 0) + val * 128
            elif nib == 0xE0:
                v = ((e["data"][1] << 7) | e["data"][0]) - 8192
                if ch in bends:
                    mn, mx, c = bends[ch]
                    bends[ch] = (min(mn, v), max(mx, v), c + 1)
                else:
                    bends[ch] = (v, v, 1)
    return ranges, bends


# fix
SEMITONE_RANGE = 2.0  # (200 cents)
TOLERANCE = 0.2  # 20 cents grace range (will be capped to 200)


def fix(
    inp,
    outp,
    *,
    bend_scale=None,
    bend_range=None,
    strip_rpn=False,
    drop_centre=False,
    keep_centre_bend=False,
    drop_noop_enabled=True,
    dedupe_meta_enabled=True,
    running=True,
    strip_cc=(),
    name=None,
    text=None,
    copyright=None,
    merge=(0, 1),
):
    info = parse_file(inp)
    tracks = [[dict(e) for e in evs] for evs in info["tracks"]]

    if merge:
        tracks = merge_tracks(tracks, merge)

    ranges, bends = scan_bends(tracks)
    # "active" = channels that actually move away from centre
    active = {ch for ch, (mn, mx, _) in bends.items() if mn != 0 or mx != 0}
    silent_bend_chs = set(bends) - active
    rpn_only_chs = set(ranges) - set(bends)
    manual = bend_scale is not None or bend_range is not None or strip_rpn

    scale_of = {}
    rpn_of = {}
    strip_chs = set()
    strip_all_rpn = False

    if manual:
        f = bend_scale if bend_scale is not None else 1.0
        if f != 1.0:
            for ch in bends:
                scale_of[ch] = f
        if strip_rpn or bend_range is not None:
            strip_all_rpn = True
        if bend_range is not None:
            for ch in bends:
                rpn_of[ch] = bend_range
        mode = "manual"
    elif not active:
        strip_all_rpn = True
        mode = "no-bends"
    else:
        for ch in active:
            mn, mx, _ = bends[ch]
            R = ranges.get(ch, 2)
            if R <= 0 or R == 2:
                continue
            extent = max(abs(mn), abs(mx)) / 8192.0 * R
            if extent <= SEMITONE_RANGE + TOLERANCE:
                scale_of[ch] = R / 2.0
                rpn_of[ch] = 2
                strip_chs.add(ch)
        strip_chs |= silent_bend_chs | rpn_only_chs
        mode = "auto"

    # report
    print(
        "  tracks: %d; bend events on %d channel(s), %d with real movement"
        % (len(tracks), len(bends), len(active))
    )
    if mode == "manual":
        print(
            "  mode: manual (scale=%s, %s)"
            % (
                bend_scale if bend_scale is not None else 1.0,
                "RPN stripped"
                if strip_all_rpn and not rpn_of
                else (
                    "RPN 0 = %d" % bend_range
                    if bend_range is not None
                    else "RPN untouched"
                ),
            )
        )
    elif mode == "no-bends":
        print("  mode: no real pitch-bend movement -> all RPN 0 stripped")
    else:
        for ch in sorted(active):
            mn, mx, _ = bends[ch]
            R = ranges.get(ch, 2)
            ext = max(abs(mn), abs(mx)) / 8192.0 * R
            if ch in scale_of:
                act = "re-encode to range 2 (x%g) + RPN 2" % scale_of[ch]
            elif ext > SEMITONE_RANGE + TOLERANCE:
                act = "exceeds 2.00 -> FL import WILL shrink the bends"
            else:
                act = "no change needed"
            print(
                "    ch %2d: declared range %2d, max %.2f st -> %s" % (ch, R, ext, act)
            )
        if silent_bend_chs:
            print(
                "    ch %s: centre-only bends -> %s"
                % (
                    sorted(silent_bend_chs),
                    "centre bends dropped + RPN stripped"
                    if (not drop_centre and not keep_centre_bend)
                    else "RPN stripped",
                )
            )
        if rpn_only_chs:
            print("    ch %s: no bends -> RPN stripped" % sorted(rpn_only_chs))

    # apply
    if scale_of:
        for evs in tracks:
            for e in evs:
                if e["kind"] == "midi" and (e["status"] & 0xF0) == 0xE0:
                    f = scale_of.get(e["status"] & 0x0F)
                    if not f or f == 1.0:
                        continue
                    v = ((e["data"][1] << 7) | e["data"][0]) - 8192
                    nv = max(0, min(16383, int(round(v * f)) + 8192))
                    e["data"] = bytes((nv & 0x7F, (nv >> 7) & 0x7F))

    if strip_all_rpn:
        tracks = [strip_rpn_range(t, None) for t in tracks]
    elif strip_chs:
        tracks = [strip_rpn_range(t, strip_chs) for t in tracks]

    if drop_centre:
        tracks = [drop_centre_bends(t, None) for t in tracks]
    elif silent_bend_chs and not keep_centre_bend:
        tracks = [drop_centre_bends(t, silent_bend_chs) for t in tracks]

    if strip_cc:
        sc = set(strip_cc)
        tracks = [
            _drop(
                t,
                lambda e: (
                    e["kind"] == "midi"
                    and (e["status"] & 0xF0) == 0xB0
                    and e["data"][0] in sc
                ),
            )
            for t in tracks
        ]

    if dedupe_meta_enabled:
        tracks = [dedupe_meta(t) for t in tracks]
        tracks = [strip_meta(t, {0x58}) for t in tracks]

    if drop_noop_enabled:
        tracks = [drop_noop(t) for t in tracks]

    if rpn_of:
        for ti, evs in enumerate(tracks):
            chs = sorted(
                {
                    e["status"] & 0x0F
                    for e in evs
                    if e["kind"] == "midi"
                    and (e["status"] & 0xF0) == 0xE0
                    and (e["status"] & 0x0F) in rpn_of
                }
            )
            if not chs:
                continue
            block = []
            for ch in chs:
                for cc, val in (
                    (101, 0),
                    (100, 0),
                    (6, rpn_of[ch]),
                    (38, 0),
                    (101, 127),
                    (100, 127),
                ):
                    block.append(
                        {
                            "kind": "midi",
                            "delta": 0,
                            "status": 0xB0 | ch,
                            "data": bytes((cc, val)),
                        }
                    )
            k = 0
            while k < len(evs) and evs[k]["kind"] == "meta":
                k += 1
            tracks[ti] = evs[:k] + block + evs[k:]

    head = []
    if name:
        head.append(
            {
                "kind": "meta",
                "delta": 0,
                "meta": 0x03,
                "payload": name.encode("latin-1", "replace"),
            }
        )
    if text:
        head.append(
            {
                "kind": "meta",
                "delta": 0,
                "meta": 0x01,
                "payload": text.encode("latin-1", "replace"),
            }
        )
    if copyright:
        head.append(
            {
                "kind": "meta",
                "delta": 0,
                "meta": 0x02,
                "payload": copyright.encode("latin-1", "replace"),
            }
        )
    if head:
        tracks[0] = head + tracks[0]

    chunks = []
    for evs in tracks:
        body = encode_track(evs, running)
        chunks.append(b"MTrk" + struct.pack(">I", len(body)) + body)
    hdr = b"MThd" + struct.pack(
        ">IHHH", 6, info["format"], len(tracks), info["division"]
    )
    blob = hdr + b"".join(chunks)
    open(outp, "wb").write(blob)
    print("wrote %s (%d bytes, was %d)" % (outp, len(blob), info["size"]))


# CLI
def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    inp, outp = argv[1], argv[2]
    o = dict(
        bend_scale=None,
        bend_range=None,
        strip_rpn=False,
        drop_centre=False,
        keep_centre_bend=False,
        drop_noop_enabled=True,
        dedupe_meta_enabled=True,
        running=True,
        strip_cc=(),
        name=None,
        text=None,
        copyright=None,
        merge=(0, 1),
    )
    i = 3
    while i < len(argv):
        a = argv[i]
        if a == "--bend-scale":
            o["bend_scale"] = float(argv[i + 1])
            i += 2
        elif a == "--bend-range":
            o["bend_range"] = int(argv[i + 1])
            i += 2
        elif a == "--strip-rpn":
            o["strip_rpn"] = True
            i += 1
        elif a == "--drop-centre-bend":
            o["drop_centre"] = True
            i += 1
        elif a == "--keep-centre-bend":
            o["keep_centre_bend"] = True
            i += 1
        elif a == "--keep-noop":
            o["drop_noop_enabled"] = False
            i += 1
        elif a == "--keep-meta":
            o["dedupe_meta_enabled"] = False
            i += 1
        elif a == "--no-running-status":
            o["running"] = False
            i += 1
        elif a == "--strip-cc":
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                o["strip_cc"] = tuple(int(x) for x in argv[i + 1].split(",") if x)
                i += 2
            else:
                o["strip_cc"] = ()
                i += 1
        elif a == "--merge-tracks":
            o["merge"] = [int(x) for x in argv[i + 1].split(",") if x]
            i += 2
        elif a == "--no-merging-tracks":
            o["merge"] = None
            i += 1
        elif a == "--name":
            o["name"] = argv[i + 1]
            i += 2
        elif a == "--text":
            o["text"] = argv[i + 1]
            i += 2
        elif a == "--copyright":
            o["copyright"] = argv[i + 1]
            i += 2
        else:
            print("unknown option %s" % a)
            return 2
    fix(inp, outp, **o)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
