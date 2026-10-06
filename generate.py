#!/usr/bin/env python3
"""discohaus identity generator.

Reads params.json and writes, per entity:

  assets/<id>/chip.svg     160x160, framed
  assets/<id>/avatar.svg   128x128, full bleed
  assets/<id>/favicon.svg  sprite only
  assets/<id>/lockup.svg   400x400, with wordmark
  assets/<id>/banner.svg   wide, mark beside wordmark
  assets/<id>/<prefix>-<w>.<format>   sized export, <w>x<h>,

plus README.md. Output depends only on params.json.
"""

import hashlib
import io
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
ASSETS = ROOT / "assets"

U = 10                                    # px per grid unit in chip.svg
FRAME = 16 * U                            # 160
BODY_XY, BODY_WH = 2 * U, 12 * U          # body rect 20,20 120x120
BODY_RX = 24
PIN_CENTERS = [44, 68, 92, 116]           # 4 pins per side
PIN_W, PIN_L, PIN_RX = 12, 20, 4
COIN_R = 7 * U                            # coin disc radius, frame struck round at one unit inset
AVATAR = 128
AVATAR_U = 8                              # px per grid unit in avatar.svg

CDN = "https://cdn.jsdelivr.net/gh/discohaus/identity@latest/assets"
DEFAULT_MARKS = ("chip", "avatar", "favicon", "lockup")


def load_params():
    with open(ROOT / "params.json", "r", encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------ sprites --
def sprite_cells(entity, params):
    """Resolve an entity's tile into (n_cells, cell_units, [(cx, cy, color)])."""
    spec = entity["tile"]
    px = {**params["pixels"], **spec.get("palette", {})}
    grid_cfg = params["grid"]
    cell = grid_cfg["cellUnits"]
    max_cells = grid_cfg["maxCells"]
    ramps = params["ramps"]
    shade = spec.get("shade", "checker")

    if spec["type"] in ("pixmap", "counterpart"):
        if spec["type"] == "counterpart":
            # rows derived from another entity's sprite by a transform +
            # key substitution, so the counterpart tracks its source
            other = next(o for o in params["entities"] if o["id"] == spec["of"])
            rows = other["tile"]["rows"]
            if spec.get("transform") == "rotate180":
                rows = ["".join(reversed(r)) for r in reversed(rows)]
            elif spec.get("transform") == "mirror":
                rows = ["".join(reversed(r)) for r in rows]
            sub = spec.get("substitute", {})
            rows = ["".join(sub.get(ch, ch) for ch in r) for r in rows]
        else:
            rows = spec["rows"]
    elif spec["type"] == "identicon":
        # deterministic fallback for unauthored tiles: sha256 of the id picks
        # a material, a distinct accent, and a mirrored 4x4 sprite (a mirrored
        # accent pair straddles both parities, so it dithers under the rule)
        digest = hashlib.sha256(entity["id"].encode("utf-8")).digest()
        cycle = params["accentCycle"]
        mat = cycle[digest[0] % len(cycle)]
        acc = cycle[(digest[0] + 1 + digest[10] % (len(cycle) - 1)) % len(cycle)]
        grid = [["."] * 4 for _ in range(4)]
        pairs = []
        for y in range(4):
            byte = digest[1 + y]
            for x in range(2):
                if (byte >> x) & 1:
                    grid[y][x] = grid[y][3 - x] = mat
                    pairs.append((x, y))
        if not pairs:
            grid[1][1] = grid[1][2] = mat
            pairs = [(1, 1)]
        ax, ay = pairs[digest[9] % len(pairs)]
        grid[ay][ax] = grid[ay][3 - ax] = acc
        if not any(ch == mat for r in grid for ch in r):
            ny = (ay + 1) % 4
            grid[ny][0] = grid[ny][3] = mat
        rows = ["".join(r) for r in grid]
        shade = "checker"
    else:
        raise ValueError(f"unknown tile type: {spec['type']}")

    n = len(rows)
    org_id = next(o["id"] for o in params["entities"] if o["kind"] == "org")
    assert n <= max_cells, f"{entity['id']}: {n}x{n} exceeds maxCells {max_cells}"
    assert n < max_cells or entity["kind"] == "org" or spec.get("of") == org_id, \
        f"{entity['id']}: {max_cells}x{max_cells} is reserved for the org and counterparts of its ball"

    if shade == "checker":
        seq = [ch for row in rows for ch in row if px.get(ch)]
        freq = {}
        for ch in seq:
            freq[ch] = freq.get(ch, 0) + 1
        material = max(freq, key=lambda k: (freq[k], -seq.index(k)))
        minors = [k for k in freq if k != material]

    cells = []
    for y, row in enumerate(rows):
        assert len(row) == n, f"{entity['id']}: row {y} is not {n} wide"
        for x, ch in enumerate(row):
            if px[ch] is None:
                continue
            if shade == "checker":
                # material dithers by parity; a lone minority dithers too,
                # two minorities render flat at hi
                if ch == material or len(minors) == 1:
                    color = ramps[ch][0 if (x + y) % 2 == 1 else 1]
                else:
                    color = ramps[ch][0]
            else:
                color = px[ch]
            cells.append((x, y, color))
    return n, cell, cells


def verify_canon(params):
    """The rule must reproduce the extracted discohaus ball byte-for-byte."""
    e = next(x for x in params["entities"] if x["id"] == "discohaus")
    _, _, cells = sprite_cells(e, params)
    got = {(x, y): c for x, y, c in cells}
    exp_rows = (".LSL.", "LMLCL", "SLSLS", "LSLSL", ".LSL.")
    exp = {(x, y): params["pixels"][ch]
           for y, r in enumerate(exp_rows) for x, ch in enumerate(r)
           if params["pixels"][ch]}
    assert got == exp, "canon violation: rule-derived ball != extracted ball"


def cell_rects(cells, origin, cell_px):
    ox, oy = origin
    return "".join(
        f'<rect x="{ox + x * cell_px}" y="{oy + y * cell_px}" '
        f'width="{cell_px}" height="{cell_px}" fill="{color}"/>'
        for x, y, color in cells
    )


# -------------------------------------------------------------------- parts --
def svg_open(viewbox, label, size=None, attrs=""):
    w, h = viewbox
    dims = f' width="{size[0]}" height="{size[1]}"' if size else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}"{dims} '
        f'role="img" aria-label="{label}"{attrs}>'
    )


def pin_rects(pin):
    rects = []
    for c in PIN_CENTERS:
        rects.append(f'<rect x="{c - PIN_W // 2}" y="6" width="{PIN_W}" height="{PIN_L}" rx="{PIN_RX}"/>')
        rects.append(f'<rect x="{c - PIN_W // 2}" y="{FRAME - 6 - PIN_L}" width="{PIN_W}" height="{PIN_L}" rx="{PIN_RX}"/>')
        rects.append(f'<rect x="6" y="{c - PIN_W // 2}" width="{PIN_L}" height="{PIN_W}" rx="{PIN_RX}"/>')
        rects.append(f'<rect x="{FRAME - 6 - PIN_L}" y="{c - PIN_W // 2}" width="{PIN_L}" height="{PIN_W}" rx="{PIN_RX}"/>')
    return f'<g fill="{pin}">{"".join(rects)}</g>'


def body_shape(kind):
    if kind["frame"] == "coin":
        return f'<circle cx="{FRAME // 2}" cy="{FRAME // 2}" r="{COIN_R}"'
    return (f'<rect x="{BODY_XY}" y="{BODY_XY}" width="{BODY_WH}" '
            f'height="{BODY_WH}" rx="{BODY_RX}"')


def framed_mark(entity, params, tag, glow):
    tokens = params["tokens"]
    kind = params["kinds"][entity["kind"]]
    n, cell, cells = sprite_cells(entity, params)
    ink, pin = tokens["ink"], tokens["pin"]
    body = body_shape(kind)
    defs, parts = [], []

    if kind["pins"]:
        parts.append(pin_rects(pin))
    rim = ""
    if glow:
        rim = f' stroke="{pin}" stroke-opacity="{params["lockup"]["bodyStrokeOpacity"]}" stroke-width="2"'
    parts.append(f'{body} fill="{ink}"{rim}/>')
    if kind["frame"] == "cartridge":
        parts.append(
            f'<rect x="{BODY_XY + 7}" y="{BODY_XY + 7}" width="{BODY_WH - 14}" '
            f'height="{BODY_WH - 14}" rx="{BODY_RX - 7}" fill="none" '
            f'stroke="{pin}" stroke-width="2"/>'
        )
    if glow:
        beam_color = {"M": tokens["beamMagenta"], "C": tokens["beamCyan"]}
        defs.append(f'<clipPath id="{tag}-body">{body}/></clipPath>')
        beams = []
        for i, bm in enumerate(params["lockup"]["beams"]):
            defs.append(
                f'<radialGradient id="{tag}-beam{i}" gradientUnits="userSpaceOnUse" '
                f'cx="{bm["cx"]}" cy="{bm["cy"]}" r="{bm["r"]}">'
                f'<stop offset="0" stop-color="{beam_color[bm["px"]]}" stop-opacity="{bm["opacity"]}"/>'
                f'<stop offset="1" stop-color="{beam_color[bm["px"]]}" stop-opacity="0"/></radialGradient>'
            )
            beams.append(f'<rect width="{FRAME}" height="{FRAME}" fill="url(#{tag}-beam{i})"/>')
        parts.append(f'<g clip-path="url(#{tag}-body)">{"".join(beams)}</g>')

    cell_px = cell * U
    origin = (FRAME - n * cell_px) // 2
    parts.append(f'<g shape-rendering="crispEdges">{cell_rects(cells, (origin, origin), cell_px)}</g>')
    return "".join(defs), "".join(parts)


def vignette_def(tag, tokens):
    """Canvas fill fading from the lockup color at upper center to the vignette color at the edges"""
    return (
        f'<radialGradient id="{tag}-vignette" cx="0.5" cy="0.45" r="0.75">'
        f'<stop offset="0" stop-color="{tokens["lockupBg"]}"/>'
        f'<stop offset="1" stop-color="{tokens["lockupVignette"]}"/></radialGradient>'
    )


def wordmark_chars(entity):
    return sum(len(text) for text, _ in entity["wordmark"])


def fit_wordmark(wm, chars, max_width, scale=1.0):
    adv = min(wm["advance"] * scale, max_width / chars)
    return adv, wm["fontSize"] * adv / wm["advance"]


def wordmark_text(entity, wm, pixels, x, baseline, adv, fsize):
    natural = wm["glyphAdvance"] * fsize
    runs = []
    for text, key in entity["wordmark"]:
        runs.append(
            f'<text transform="translate({x:.1f} {baseline:.1f}) scale({adv / natural:.4f} 1)" '
            f'textLength="{len(text) * natural:.1f}" lengthAdjust="spacingAndGlyphs" '
            f'font-family="{wm["fontFamily"]}" font-weight="{wm["fontWeight"]}" '
            f'font-size="{fsize:.1f}" fill="{pixels[key]}">{text}</text>'
        )
        x += len(text) * adv
    return "".join(runs)


# -------------------------------------------------------------------- marks --
def svg_chip(entity, params, size=None):
    """Framed mark on a transparent frame"""
    _, mark = framed_mark(entity, params, f"chip-{entity['id']}", glow=False)
    return svg_open((FRAME, FRAME), f'{entity["id"]} mark', size) + mark + "</svg>"


def svg_avatar(entity, params, size=None):
    """Full-bleed: no pins, ink canvas, magenta NW / cyan SE corner glows, coins cut round."""
    tokens = params["tokens"]
    kind = params["kinds"][entity["kind"]]
    n, cell, cells = sprite_cells(entity, params)
    ink = tokens["ink"]
    rx = AVATAR // 2 if kind["frame"] == "coin" else round(AVATAR * tokens["avatarRadiusRatio"])
    op = tokens["beamOpacity"]
    eid = entity["id"]

    cell_px = cell * AVATAR_U
    origin = (AVATAR - n * cell_px) // 2
    return (
        svg_open((AVATAR, AVATAR), f"{eid} avatar", size)
        + f"<defs>"
        f'<clipPath id="clip-{eid}"><rect width="{AVATAR}" height="{AVATAR}" rx="{rx}"/></clipPath>'
        f'<radialGradient id="bm-{eid}" gradientUnits="userSpaceOnUse" cx="10" cy="10" r="88">'
        f'<stop offset="0" stop-color="{tokens["beamMagenta"]}" stop-opacity="{op}"/>'
        f'<stop offset="1" stop-color="{tokens["beamMagenta"]}" stop-opacity="0"/></radialGradient>'
        f'<radialGradient id="bc-{eid}" gradientUnits="userSpaceOnUse" cx="118" cy="118" r="88">'
        f'<stop offset="0" stop-color="{tokens["beamCyan"]}" stop-opacity="{op}"/>'
        f'<stop offset="1" stop-color="{tokens["beamCyan"]}" stop-opacity="0"/></radialGradient>'
        f"</defs>"
        f'<g clip-path="url(#clip-{eid})">'
        f'<rect width="{AVATAR}" height="{AVATAR}" fill="{ink}"/>'
        f'<rect width="{AVATAR}" height="{AVATAR}" fill="url(#bm-{eid})"/>'
        f'<rect width="{AVATAR}" height="{AVATAR}" fill="url(#bc-{eid})"/>'
        f'<g shape-rendering="crispEdges">{cell_rects(cells, (origin, origin), cell_px)}</g>'
        f"</g></svg>"
    )


def svg_favicon(entity, params, size=None):
    """Sprite only, full bleed on the slot grid, for 16px tabs."""
    n, cell, cells = sprite_cells(entity, params)
    slot = params["grid"]["slotUnits"]
    canvas = max(slot, n * cell)
    off = (canvas - n * cell) // 2
    return (
        svg_open((canvas, canvas), f'{entity["id"]} favicon', size, ' shape-rendering="crispEdges"')
        + f"{cell_rects(cells, (off, off), cell)}</svg>"
    )


def svg_lockup(entity, params, size=None):
    """Mark over wordmark on a vignetted canvas."""
    lk, eid = params["lockup"], entity["id"]
    tag = f"lockup-{eid}"
    S = lk["canvas"]
    wm = lk["wordmark"]
    chars = wordmark_chars(entity)
    adv, fsize = fit_wordmark(wm, chars, wm["maxWidth"])
    defs, mark = framed_mark(entity, params, tag, glow=True)
    return (
        svg_open((S, S), f"{eid} lockup", size)
        + f'<defs>{vignette_def(tag, params["tokens"])}{defs}</defs>'
        f'<rect width="{S}" height="{S}" fill="url(#{tag}-vignette)"/>'
        f'<g transform="translate({(S - FRAME) // 2} {lk["frameTop"]})">{mark}</g>'
        + wordmark_text(entity, wm, params["pixels"], (S - chars * adv) / 2, wm["baseline"], adv, fsize)
        + "</svg>"
    )


def svg_banner(entity, params, size=None):
    """The lockup's mark and wordmark side by side, laid out for whatever canvas is asked"""
    bn, lk, eid = params["banner"], params["lockup"], entity["id"]
    tag = f"banner-{eid}"
    W, H = size or bn["canvas"]
    scale = H * bn["frameHeight"] / FRAME
    gap = bn["gapUnits"] * U * scale
    wm = lk["wordmark"]
    chars = wordmark_chars(entity)
    room = W * (1 - 2 * bn["sidePadding"]) - FRAME * scale - gap
    assert room > 0, f"{eid}: a {W}x{H} banner leaves no room beside the mark for the wordmark"
    adv, fsize = fit_wordmark(wm, chars, room, scale)
    fx = (W - FRAME * scale - gap - chars * adv) / 2
    fy = (H - FRAME * scale) / 2
    defs, mark = framed_mark(entity, params, tag, glow=True)
    return (
        svg_open((W, H), f"{eid} banner", size)
        + f'<defs>{vignette_def(tag, params["tokens"])}{defs}</defs>'
        f'<rect width="{W}" height="{H}" fill="url(#{tag}-vignette)"/>'
        f'<g transform="translate({fx:.1f} {fy:.1f}) scale({scale:.4f})">{mark}</g>'
        + wordmark_text(entity, wm, params["pixels"], fx + FRAME * scale + gap,
                        H / 2 + bn["capHeight"] * fsize / 2, adv, fsize)
        + "</svg>"
    )


MARKS = {
    "chip": svg_chip,
    "avatar": svg_avatar,
    "favicon": svg_favicon,
    "lockup": svg_lockup,
    "banner": svg_banner,
}


def entity_marks(entity):
    extra = tuple(entity.get("marks", ()))
    optional = sorted(set(MARKS) - set(DEFAULT_MARKS))
    assert all(m in optional for m in extra), f"{entity['id']}: marks may only add {optional}"
    return DEFAULT_MARKS + extra


# ------------------------------------------------------------------ exports --
def aspect(mark, params):
    if mark == "banner":
        w, h = params["banner"]["canvas"]
        return w / h
    return 1


def export_size(mark, size, params):
    if isinstance(size, int):
        return size, round(size / aspect(mark, params))
    w, h = size
    assert aspect(mark, params) != 1 or w == h, f"{mark} is square, {w}x{h} is not"
    return w, h


def rasterize(svg, size):
    """PNG bytes at size through rsvg-convert"""
    w, h = size
    try:
        done = subprocess.run(
            ["rsvg-convert", "-w", str(w), "-h", str(h), "-f", "png"],
            input=svg.encode("utf-8"), stdout=subprocess.PIPE, check=True,
        )
    except FileNotFoundError:
        sys.exit("rsvg-convert not found, png and webp exports need librsvg")
    return done.stdout


def webp_bytes(png):
    from PIL import Image
    out = io.BytesIO()
    Image.open(io.BytesIO(png)).save(out, format="WEBP", lossless=True)
    return out.getvalue()


FORMATS = {
    "svg": lambda svg, size: (svg + "\n").encode("utf-8"),
    "png": rasterize,
    "webp": lambda svg, size: webp_bytes(rasterize(svg, size)),
}


def export_spec(entity, params, export):
    """Validated (filename, mark, format, width, height) of one export entry"""
    eid, mark, fmt = entity["id"], export["mark"], export["format"]
    assert mark in MARKS, f"{eid}: unknown export mark {mark!r}, use one of {sorted(MARKS)}"
    assert fmt in FORMATS, f"{eid}: unknown export format {fmt!r}, use one of {sorted(FORMATS)}"
    prefix = export.get("prefix", mark)
    assert re.fullmatch(r"[a-z0-9][a-z0-9_-]*", prefix), \
        f"{eid}: export prefix {prefix!r} must be lowercase letters, digits, - or _"
    w, h = export_size(mark, export["size"], params)
    name = f"{prefix}-{w}.{fmt}" if w == h else f"{prefix}-{w}x{h}.{fmt}"
    return name, mark, fmt, w, h


def entity_files(entity, params):
    """Every filename written under assets/<id>, svg marks then exports, no two alike"""
    names = [f"{m}.svg" for m in entity_marks(entity)]
    names += [export_spec(entity, params, x)[0] for x in entity.get("exports", ())]
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, f"{entity['id']}: exports collide on {dupes}, change a prefix or size"
    return names


def write_export(entity, params, export):
    name, mark, fmt, w, h = export_spec(entity, params, export)
    svg = MARKS[mark](entity, params, (w, h))
    (ASSETS / entity["id"] / name).write_bytes(FORMATS[fmt](svg, (w, h)))


# ------------------------------------------------------------------- readme --
def readme_md(params):
    def cell(eid, mark, width):
        return (f'<a href="{CDN}/{eid}/{mark}.svg">'
                f'<img src="assets/{eid}/{mark}.svg" width="{width}" alt="{eid} {mark}"></a>')

    def row(e):
        eid = e["id"]
        kind = "fallback demo" if e.get("demo") else e["kind"]
        cells = " | ".join(cell(eid, m, w) for m, w in (("chip", 96), ("avatar", 96), ("lockup", 96), ("favicon", 32)))
        return f"| **{eid}**<br><sub>{kind}</sub> | {cells} | `{CDN}/{eid}/` |"

    rows = "\n".join(row(e) for e in params["entities"])
    banners = "\n".join(
        f'| **{e["id"]}** | {cell(e["id"], "banner", 600)} |'
        for e in params["entities"] if "banner" in entity_marks(e)
    )
    banner_section = f"\n## Banners\n\n| entity | banner |\n| --- | --- |\n{banners}\n" if banners else ""
    title = params["system"].replace("-", " ")
    return f"""<!-- Generated by generate.py from params.json - edit those, not this file. -->

# {title}

Marks for the Disco ecosystem. Everything in [`assets/`](assets/) is generated
from [`params.json`](params.json):

```
python3 generate.py
```

<p align="center"><img src="assets/discohaus/lockup.svg" width="256" alt="discohaus lockup"></p>

## Marks

| entity | chip | avatar | lockup | favicon | cdn |
| --- | :-: | :-: | :-: | :-: | --- |
{rows}
{banner_section}"""


# --------------------------------------------------------------------- main --
def main():
    params = load_params()
    verify_canon(params)
    ASSETS.mkdir(exist_ok=True)
    n_files = 0
    for e in params["entities"]:
        n_files += len(entity_files(e, params))
        d = ASSETS / e["id"]
        d.mkdir(exist_ok=True)
        for mark in entity_marks(e):
            (d / f"{mark}.svg").write_text(MARKS[mark](e, params) + "\n", encoding="utf-8")
        for export in e.get("exports", ()):
            write_export(e, params, export)
    (ROOT / "README.md").write_text(readme_md(params), encoding="utf-8")
    print(f"generated {n_files} files -> {ASSETS} + README.md")


if __name__ == "__main__":
    sys.exit(main())
