"""Stored photos carry no metadata (location, device, time, thumbnails), but stay upright and keep their colours.

    python3 table/tests/photo_privacy_test.py        (standard library only)"""
import struct
import sys
import tempfile
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import core  # noqa: E402

ok_all = True
_real_strip_jpeg = core.strip_jpeg
_real_strip_png = core.strip_png


# ---- a tiny real baseline JPEG (8x8, solid colour): SOI + JFIF + DQT/SOF/DHT/SOS + EOI --------------------------------
BASE = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300100b0c0e0c0a100e0d0e1211101318281a181616183123251d283a333d3c"
    "3933383740485c4e404457453738506d51575f626768673e4d71797064785c656763ffdb0043011112121815182f1a1a2f634238426363"
    "636363636363636363636363636363636363636363636363636363636363636363636363636363636363636363636363ffc00011080008"
    "000803012200021101031101ffc4001f0000010501010101010100000000000000000102030405060708090a0bffc400b5100002010303"
    "020403050504040000017d01020300041105122131410613516107227114328191a1082342b1c11552d1f02433627282090a161718191a"
    "25262728292a3435363738393a434445464748494a535455565758595a636465666768696a737475767778797a838485868788898a9293"
    "9495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2"
    "f3f4f5f6f7f8f9faffc4001f0100030101010101010101010000000000000102030405060708090a0bffc400b511000201020404030407"
    "05040400010277000102031104052131061241510761711322328108144291a1b1c109233352f0156272d10a162434e125f11718191a26"
    "2728292a35363738393a434445464748494a535455565758595a636465666768696a737475767778797a82838485868788898a92939495"
    "969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae2e3e4e5e6e7e8e9eaf2f3f4f5f6"
    "f7f8f9faffda000c03010002110311003f00c7a28a2b84fa83ffd9")

JFIF_END = 20                                       # SOI(2) + APP0(18)
HEAD, BODY = BASE[:JFIF_END], BASE[JFIF_END:]       # BODY: DQT ... SOS, entropy data, EOI


def seg(marker, payload):
    return b"\xff" + bytes([marker]) + struct.pack(">H", len(payload) + 2) + payload


def tiff(orientation, order="MM", with_gps=True):
    """A TIFF block with Make, Model, Orientation, DateTime and a GPS IFD, all with recognisable strings."""
    e = ">" if order == "MM" else "<"
    make, model, dt = b"AcmePhone Corp\0", b"SecretModel X9\0", b"2026:10:07 08:15:42\0"
    lat = struct.pack(e + "6I", 51, 1, 30, 1, 1234, 100)
    tags = [(0x010F, 2, len(make), make), (0x0110, 2, len(model), model), (0x0112, 3, 1, struct.pack(e + "H", orientation) if orientation else None),
            (0x0132, 2, len(dt), dt)]
    tags = [t for t in tags if t[3] is not None]
    if with_gps:
        tags.append((0x8825, 4, 1, "GPS"))
    tags.sort()
    ifd0 = 8
    size0 = 2 + 12 * len(tags) + 4
    gps_at = ifd0 + size0
    gps_tags = [(1, 2, 2, b"N\0"), (2, 5, 3, "LAT"), (0x1D, 2, 11, b"2026:10:07\0")]
    gps_size = 2 + 12 * len(gps_tags) + 4
    data_at = gps_at + (gps_size if with_gps else 0)
    blob = b""

    def entry(tag, typ, cnt, val):
        nonlocal blob
        if val == "GPS":
            return struct.pack(e + "HHII", tag, typ, cnt, gps_at)
        if val == "LAT":
            val = lat
        if len(val) <= 4:
            return struct.pack(e + "HHI", tag, typ, cnt) + val.ljust(4, b"\0")
        off = data_at + len(blob)
        blob += val
        return struct.pack(e + "HHII", tag, typ, cnt, off)
    ifd = struct.pack(e + "H", len(tags)) + b"".join(entry(*t) for t in tags) + struct.pack(e + "I", 0)
    gps = (struct.pack(e + "H", len(gps_tags)) + b"".join(entry(*t) for t in gps_tags) + struct.pack(e + "I", 0)) if with_gps else b""
    return order.encode() + struct.pack(e + "HI", 42, 8) + ifd + gps + blob


ICC = b"ICC_PROFILE\0\x01\x01" + b"colour-profile-bytes-" * 5
THUMB = BASE                                        # an embedded thumbnail is itself a whole JPEG
SECRETS = [b"AcmePhone Corp", b"SecretModel X9", b"2026:10:07", b"hunter-xmp-author", b"iptc-caption-private",
           b"private comment", b"MakerNotePrivate", b"trailing-second-image-secret"]


def make_jpeg(orientation=6, order="MM", extra=True):
    exif = b"Exif\0\0" + tiff(orientation, order) + b"MakerNotePrivate"
    parts = [HEAD, seg(0xE1, exif + THUMB if extra else exif)]
    if extra:
        parts += [seg(0xE1, b"http://ns.adobe.com/xap/1.0/\0<x:xmpmeta>hunter-xmp-author</x:xmpmeta>"),
                  seg(0xE2, ICC),
                  seg(0xE2, b"MPF\0some-multi-picture-index"),
                  seg(0xED, b"Photoshop 3.0\0" + b"iptc-caption-private"),
                  seg(0xEE, b"Adobe\0\x64\x80\x00\x00\x00\x01"),
                  seg(0xFE, b"private comment")]
    parts.append(BODY)
    if extra:
        parts.append(b"trailing-second-image-secret" + THUMB)  # data after EOI (MPF second images live here)
    return b"".join(parts)


def read_orientation(jpg):
    """Independent re-parse: the orientation tag of the first EXIF APP1, or None."""
    try:
        return _read_orientation(jpg)
    except (struct.error, IndexError):
        return None


def _read_orientation(jpg):
    pos = 2
    while pos + 4 <= len(jpg) and jpg[pos] == 0xFF and jpg[pos + 1] != 0xDA:
        ln = struct.unpack(">H", jpg[pos + 2:pos + 4])[0]
        p = jpg[pos + 4:pos + 2 + ln]
        if jpg[pos + 1] == 0xE1 and p[:6] == b"Exif\0\0":
            t = p[6:]
            e = ">" if t[:2] == b"MM" else "<"
            off = struct.unpack(e + "I", t[4:8])[0]
            n = struct.unpack(e + "H", t[off:off + 2])[0]
            for k in range(n):
                tag, typ, cnt = struct.unpack(e + "HHI", t[off + 2 + 12 * k: off + 10 + 12 * k])
                if tag == 0x0112:
                    return struct.unpack(e + "H", t[off + 10 + 12 * k: off + 12 + 12 * k])[0]
            return None
        pos += 2 + ln
    return None


def segments(jpg):
    """[(marker, bytes)] of header segments up to and including SOS, plus the scan+EOI tail."""
    out, pos = [], 2
    while jpg[pos] == 0xFF:
        m = jpg[pos + 1]
        ln = struct.unpack(">H", jpg[pos + 2:pos + 4])[0]
        out.append((m, jpg[pos:pos + 2 + ln]))
        pos += 2 + ln
        if m == 0xDA:
            break
    return out, jpg[pos:]


def png_chunk(typ, data):
    return struct.pack(">I", len(data)) + typ + data + struct.pack(">I", zlib.crc32(typ + data))


def make_png(extra=True):
    ihdr = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\0" + b"\xff\0\0\0\xff\0" + b"\0" + b"\0\0\xff\xff\xff\0")
    ch = [png_chunk(b"IHDR", ihdr), png_chunk(b"sRGB", b"\0"), png_chunk(b"gAMA", struct.pack(">I", 45455))]
    if extra:
        ch += [png_chunk(b"tEXt", b"Author\0png-author-secret"), png_chunk(b"zTXt", b"c\0\0" + zlib.compress(b"png-ztxt-secret")),
               png_chunk(b"iTXt", b"k\0\0\0\0\0png-itxt-secret"), png_chunk(b"eXIf", b"MM\0*\0\0\0\x08png-exif-secret"),
               png_chunk(b"tIME", b"\x07\xea\x0a\x07\x08\x0f\x2a")]
    ch += [png_chunk(b"IDAT", idat), png_chunk(b"IEND", b"")]
    return b"\x89PNG\r\n\x1a\n" + b"".join(ch), idat


def run(label_prefix=""):
    """Run every check; return the names of the failed ones (printed unless quiet)."""
    failed = []

    def check(name, cond, detail=""):
        if not cond:
            failed.append(name)
        if label_prefix == "":
            print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f" — {detail}"))

    def save(data, cap=300, folder=None):
        folder = folder or Path(tempfile.mkdtemp()) / "photos"
        code, out = core.save_photo(data, folder, cap)
        stored = None
        if code == 200:
            stored = (folder / out["photo"].rsplit("/", 1)[1]).read_bytes()
        return code, out, stored, folder

    src = make_jpeg(6, "MM")
    check("fixture really contains the secrets", all(s in src for s in SECRETS))
    code, out, got, folder = save(src)
    check("a JPEG is accepted with the same return shape", code == 200 and out["ok"] is True and out["photo"].startswith("/photos/")
          and out["photo"].endswith(".jpg"), (code, out))
    got = got or b""
    check("no metadata string survives anywhere in the stored bytes", not any(s in got for s in SECRETS),
          [s for s in SECRETS if s in got])
    check("camera Make/Model/DateTime/GPS strings are gone", b"AcmePhone" not in got and b"SecretModel" not in got and b"2026:10" not in got)
    expected = (len(HEAD + BODY) + len(seg(0xE2, ICC)) + len(seg(0xEE, b"Adobe\0\x64\x80\x00\x00\x00\x01"))
                + len(core._orientation_segment(6)))
    check("the embedded thumbnail and the trailing second image are gone (size is exactly image + ICC + Adobe + orientation)",
          len(got) == expected, (len(got), expected))
    segs_in, tail_in = segments(src)
    segs_out, tail_out = segments(got) if got[:2] == b"\xff\xd8" else ([], b"")
    keep_ids = (0xDB, 0xC0, 0xC4, 0xDA)
    check("image segments (DQT/SOF/DHT/SOS) are byte-identical",
          [s for m, s in segs_in if m in keep_ids] == [s for m, s in segs_out if m in keep_ids] and len(segs_out) > 0)
    check("entropy-coded data and EOI are byte-identical, and nothing follows EOI", tail_in.startswith(tail_out) and tail_out == BODY[-len(tail_out):]
          and got.endswith(b"\xff\xd9") and tail_out.endswith(b"\xff\xd9") and tail_out == segments(BASE)[1])
    check("JFIF survives first", segs_out[:1] == segs_in[:1] and segs_out[0][0] == 0xE0 if segs_out else False)
    check("orientation survives (re-parsed)", read_orientation(got) == 6, read_orientation(got))
    exifs = [s for m, s in segs_out if m == 0xE1]
    check("the only APP1 is a minimal orientation-only EXIF", len(exifs) == 1 and len(exifs[0]) < 50, [len(e) for e in exifs])
    check("the ICC profile survives", any(m == 0xE2 and s[4:16] == b"ICC_PROFILE\0" for m, s in segs_out) and ICC[12:] in got)
    check("the Adobe colour-transform segment survives", any(m == 0xEE for m, s in segs_out))
    check("comment, XMP, IPTC and MPF segments are gone", not any(m in (0xFE, 0xED) for m, s in segs_out)
          and len([m for m, s in segs_out if m == 0xE2]) == 1 and b"xmpmeta" not in got and b"MPF" not in got)
    mode = (folder / out["photo"].rsplit("/", 1)[1]).stat().st_mode & 0o777
    check("stored file is private (0600)", mode == 0o600, oct(mode))

    for order in ("MM", "II"):
        for o in (2, 3, 8):
            _, _, g, _ = save(make_jpeg(o, order))
            check(f"orientation {o} survives from a {order} (big/little-endian) EXIF", read_orientation(g or b"") == o)
    _, _, g, _ = save(make_jpeg(1, "MM"))
    check("orientation 1 inserts nothing (no EXIF at all)", g is not None and read_orientation(g) is None and b"Exif" not in g)
    _, _, g, _ = save(make_jpeg(0, "II"))
    check("no orientation tag inserts nothing", g is not None and read_orientation(g) is None and b"SecretModel" not in g)
    _, _, g, _ = save(HEAD + BODY)
    check("a clean JPEG round-trips byte for byte", g == HEAD + BODY)
    no_jfif = b"\xff\xd8" + seg(0xE1, b"Exif\0\0" + tiff(5)) + BODY
    _, _, g, _ = save(no_jfif)
    check("orientation survives when there is no JFIF header", g is not None and read_orientation(g) == 5 and g[:4] == b"\xff\xd8\xff\xe1")

    # refusals
    base = make_jpeg()
    for name, bad in (("truncated mid-scan (no EOI)", base[:len(HEAD) + len(BODY) // 2 + 300]),
                      ("missing EOI", (HEAD + BODY)[:-2]),
                      ("segment length past the end", HEAD[:2] + b"\xff\xe1\xff\xf0Exif\0\0" + b"x" * 50),
                      ("zero-length segment", HEAD[:2] + b"\xff\xe1\x00\x00" + BODY),
                      ("header only", HEAD),
                      ("stray bytes between segments", HEAD + b"\x00\x00junk" + BODY)):
        code, out, stored, _ = save(bad if bad[:3] == b"\xff\xd8\xff" else b"\xff\xd8\xff" + bad)
        check(f"refused: {name}", code == 400 and out == {"error": "that isn't a readable JPEG"} and stored is None, (code, out))
    code, out, _, _ = save(b"GIF89a....")
    check("a non-image is refused", code == 400 and out == {"error": "send a JPEG or PNG"}, (code, out))
    code, out, _, _ = save(b"\xff\xd8\xff" + b"\0" * 8_000_001)
    check("the 8 MB size cap still applies", code == 413, code)
    code, out, _, _ = save(b"")
    check("empty body is refused", code == 413)
    f = Path(tempfile.mkdtemp()) / "photos"
    codes = [core.save_photo(src, f, 2)[0] for _ in range(3)]
    check("the per-table photo cap still applies", codes == [200, 200, 429], codes)

    # PNG
    png, idat = make_png()
    code, out, got, _ = save(png)
    got = got or b""
    check("a PNG is accepted with the same return shape", code == 200 and out["photo"].endswith(".png"), (code, out))
    check("PNG text, EXIF and time chunks are gone", not any(s in got for s in (b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME", b"png-author-secret",
                                                                               b"png-itxt-secret", b"png-exif-secret")), got[:40])
    check("PNG IDAT is unchanged", png_chunk(b"IDAT", idat) in got)
    check("PNG rendering chunks survive", all(t in got for t in (b"IHDR", b"sRGB", b"gAMA", b"IEND")))
    check("a clean PNG round-trips byte for byte", save(make_png(False)[0])[2] == make_png(False)[0])
    bad = bytearray(png)
    bad[40] ^= 0xFF
    check("a PNG with a corrupt chunk is refused", save(bytes(bad))[0] == 400)
    check("a truncated PNG is refused", save(png[:-5])[0] == 400)
    check("a PNG without IEND is refused", save(png[:-12])[0] == 400)
    check("a PNG whose first chunk is not IHDR is refused", save(png[:8] + png_chunk(b"tEXt", b"a\0b") + png[8:])[0] == 400)
    return failed


print("photo privacy:")
real_failed = run()
ok_all &= not real_failed

# ---- mutation check: each broken stripper must make the checks above fail ------------------------------------------
print("mutation check:")


def drop_segments(jpg, pred):
    segs, tail = segments(jpg)
    return jpg[:2] + b"".join(s for m, s in segs if not pred(m, s)) + tail


mutants = {
    "stripper is a no-op": lambda d: d,
    "stripper also drops the ICC profile": lambda d: drop_segments(_real_strip_jpeg(d), lambda m, s: m == 0xE2 and s[4:16] == b"ICC_PROFILE\0"),
    "stripper drops the orientation": lambda d: drop_segments(_real_strip_jpeg(d), lambda m, s: m == 0xE1),
    "stripper keeps comments": lambda d: _real_strip_jpeg(d)[:20] + seg(0xFE, b"private comment") + _real_strip_jpeg(d)[20:],
    "stripper keeps data after EOI": lambda d: _real_strip_jpeg(d) + b"trailing-second-image-secret",
}
for name, fn in mutants.items():
    core.strip_jpeg = lambda d, fn=fn: fn(d) if _real_strip_jpeg(d) is not None else None
    caught = run("quiet")
    core.strip_jpeg = _real_strip_jpeg
    print(("  ✓ caught: " if caught else "  ✗ NOT CAUGHT: ") + name + (f" ({len(caught)} checks failed)" if caught else ""))
    ok_all &= bool(caught)
core.strip_png = lambda d: d
caught = run("quiet")
core.strip_png = _real_strip_png
print(("  ✓ caught: " if caught else "  ✗ NOT CAUGHT: ") + "PNG stripper is a no-op" + (f" ({len(caught)} checks failed)" if caught else ""))
ok_all &= bool(caught)
check_after = run("quiet")
ok_all &= not check_after

print("ok" if ok_all else "FAILED")
sys.exit(0 if ok_all else 1)
