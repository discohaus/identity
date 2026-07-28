#!/usr/bin/env python3
"""discohaus identity generator.

Reads params.json and writes, per entity:

  assets/<id>/chip.svg     160x160, framed
  assets/<id>/avatar.svg   128x128, full bleed
  assets/<id>/favicon.svg  sprite only
  assets/<id>/lockup.svg   400x400, with wordmark

plus README.md. Output depends only on params.json.
"""

import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
ASSETS = ROOT / "assets"

U = 10                                    # px per grid unit in chip.svg
FRAME = 16 * U                            # 160
BODY_XY, BODY_WH = 2 * U, 12 * U          # body rect 20,20 120x120
BODY_RX = 24
PIN_CENTERS = [44, 68, 92, 116]           # 4 pins per side
PIN_W, PIN_L, PIN_RX = 12, 20, 4
AVATAR = 128
AVATAR_U = 8                              # px per grid unit in avatar.svg

CDN = "https://cdn.jsdelivr.net/gh/discohaus/identity@latest/assets"


def load_params():
    with open(ROOT / "params.json", "r", encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------ sprites --
def sprite_cells(entity, params):
    """Resolve an entity's tile into (n_cells, cell_units, [(cx, cy, color)])."""
    spec = entity["tile"]
    px = params["pixels"]
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
    assert n <= max_cells, f"{entity['id']}: {n}x{n} exceeds maxCells {max_cells}"
    assert n < max_cells or entity["kind"] == "org", \
        f"{entity['id']}: {max_cells}x{max_cells} is reserved for the org"

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


# -------------------------------------------------------------------- marks --
def svg_chip(entity, params):
    tokens = params["tokens"]
    kind = params["kinds"][entity["kind"]]
    n, cell, cells = sprite_cells(entity, params)
    ink, pin = tokens["ink"], tokens["pin"]

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {FRAME} {FRAME}" '
        f'role="img" aria-label="{entity["id"]} mark">'
    ]
    if kind["pins"]:
        pins = []
        for c in PIN_CENTERS:
            pins.append(f'<rect x="{c - PIN_W // 2}" y="6" width="{PIN_W}" height="{PIN_L}" rx="{PIN_RX}"/>')
            pins.append(f'<rect x="{c - PIN_W // 2}" y="{FRAME - 6 - PIN_L}" width="{PIN_W}" height="{PIN_L}" rx="{PIN_RX}"/>')
            pins.append(f'<rect x="6" y="{c - PIN_W // 2}" width="{PIN_L}" height="{PIN_W}" rx="{PIN_RX}"/>')
            pins.append(f'<rect x="{FRAME - 6 - PIN_L}" y="{c - PIN_W // 2}" width="{PIN_L}" height="{PIN_W}" rx="{PIN_RX}"/>')
        parts.append(f'<g fill="{pin}">{"".join(pins)}</g>')

    parts.append(
        f'<rect x="{BODY_XY}" y="{BODY_XY}" width="{BODY_WH}" height="{BODY_WH}" '
        f'rx="{BODY_RX}" fill="{ink}"/>'
    )
    if kind["frame"] == "cartridge":
        parts.append(
            f'<rect x="{BODY_XY + 7}" y="{BODY_XY + 7}" width="{BODY_WH - 14}" '
            f'height="{BODY_WH - 14}" rx="{BODY_RX - 7}" fill="none" '
            f'stroke="{pin}" stroke-width="2"/>'
        )

    cell_px = cell * U
    origin = (FRAME - n * cell_px) // 2
    parts.append(f'<g shape-rendering="crispEdges">{cell_rects(cells, (origin, origin), cell_px)}</g>')
    parts.append("</svg>")
    return "".join(parts)


def svg_avatar(entity, params):
    """Full-bleed: no pins, ink canvas, magenta NW / cyan SE corner glows."""
    tokens = params["tokens"]
    n, cell, cells = sprite_cells(entity, params)
    ink = tokens["ink"]
    rx = round(AVATAR * tokens["avatarRadiusRatio"])
    op = tokens["beamOpacity"]
    eid = entity["id"]

    cell_px = cell * AVATAR_U
    origin = (AVATAR - n * cell_px) // 2
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {AVATAR} {AVATAR}" '
        f'role="img" aria-label="{eid} avatar">'
        f"<defs>"
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


def svg_lockup(entity, params):
    """Chip over wordmark on a vignetted canvas."""
    lk = params["lockup"]
    tokens = params["tokens"]
    px = params["pixels"]
    kind = params["kinds"][entity["kind"]]
    n, cell, cells = sprite_cells(entity, params)
    eid = entity["id"]
    S = lk["canvas"]
    fx, fy = (S - FRAME) // 2, lk["frameTop"]
    ink, pin = tokens["ink"], tokens["pin"]

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {S} {S}" '
        f'role="img" aria-label="{eid} lockup">',
        "<defs>",
        f'<radialGradient id="vg-{eid}" gradientUnits="userSpaceOnUse" cx="{S // 2}" cy="{int(S * 0.45)}" r="{int(S * 0.75)}">'
        f'<stop offset="0" stop-color="{tokens["lockupBg"]}"/>'
        f'<stop offset="1" stop-color="{tokens["lockupVignette"]}"/></radialGradient>',
        f'<clipPath id="lb-{eid}"><rect x="{fx + BODY_XY}" y="{fy + BODY_XY}" '
        f'width="{BODY_WH}" height="{BODY_WH}" rx="{BODY_RX}"/></clipPath>',
    ]
    beam_color = {"M": tokens["beamMagenta"], "C": tokens["beamCyan"]}
    for i, bm in enumerate(lk["beams"]):
        parts.append(
            f'<radialGradient id="lg-{eid}-{i}" gradientUnits="userSpaceOnUse" '
            f'cx="{fx + bm["cx"]}" cy="{fy + bm["cy"]}" r="{bm["r"]}">'
            f'<stop offset="0" stop-color="{beam_color[bm["px"]]}" stop-opacity="{bm["opacity"]}"/>'
            f'<stop offset="1" stop-color="{beam_color[bm["px"]]}" stop-opacity="0"/></radialGradient>'
        )
    parts.append("</defs>")
    parts.append(f'<rect width="{S}" height="{S}" fill="url(#vg-{eid})"/>')

    if kind["pins"]:
        pins = []
        for c in PIN_CENTERS:
            pins.append(f'<rect x="{fx + c - PIN_W // 2}" y="{fy + 6}" width="{PIN_W}" height="{PIN_L}" rx="{PIN_RX}"/>')
            pins.append(f'<rect x="{fx + c - PIN_W // 2}" y="{fy + FRAME - 6 - PIN_L}" width="{PIN_W}" height="{PIN_L}" rx="{PIN_RX}"/>')
            pins.append(f'<rect x="{fx + 6}" y="{fy + c - PIN_W // 2}" width="{PIN_L}" height="{PIN_W}" rx="{PIN_RX}"/>')
            pins.append(f'<rect x="{fx + FRAME - 6 - PIN_L}" y="{fy + c - PIN_W // 2}" width="{PIN_L}" height="{PIN_W}" rx="{PIN_RX}"/>')
        parts.append(f'<g fill="{pin}">{"".join(pins)}</g>')

    parts.append(
        f'<rect x="{fx + BODY_XY}" y="{fy + BODY_XY}" width="{BODY_WH}" height="{BODY_WH}" '
        f'rx="{BODY_RX}" fill="{ink}" stroke="{pin}" stroke-opacity="{lk["bodyStrokeOpacity"]}" stroke-width="2"/>'
    )
    if kind["frame"] == "cartridge":
        parts.append(
            f'<rect x="{fx + BODY_XY + 7}" y="{fy + BODY_XY + 7}" width="{BODY_WH - 14}" '
            f'height="{BODY_WH - 14}" rx="{BODY_RX - 7}" fill="none" '
            f'stroke="{pin}" stroke-width="2"/>'
        )
    beams = "".join(
        f'<rect x="{fx + BODY_XY}" y="{fy + BODY_XY}" width="{BODY_WH}" height="{BODY_WH}" '
        f'fill="url(#lg-{eid}-{i})"/>'
        for i in range(len(lk["beams"]))
    )
    parts.append(f'<g clip-path="url(#lb-{eid})">{beams}</g>')

    cell_px = cell * U
    origin = (FRAME - n * cell_px) // 2
    parts.append(
        f'<g shape-rendering="crispEdges">'
        f"{cell_rects(cells, (fx + origin, fy + origin), cell_px)}</g>"
    )

    # wordmark: monospace flow pinned with textLength, so layout is deterministic regardless of which font in the stack resolves
    wm = lk["wordmark"]
    name_len = sum(len(t) for t, _ in entity["wordmark"])
    adv = min(wm["advance"], wm["maxWidth"] / name_len)
    fsize = wm["fontSize"] * adv / wm["advance"]
    x = (S - name_len * adv) / 2
    for text, key in entity["wordmark"]:
        tl = len(text) * adv
        parts.append(
            f'<text x="{x:.1f}" y="{wm["baseline"]}" textLength="{tl:.1f}" '
            f'lengthAdjust="spacingAndGlyphs" font-family="{wm["fontFamily"]}" '
            f'font-weight="{wm["fontWeight"]}" font-size="{fsize:.1f}" '
            f'fill="{px[key]}">{text}</text>'
        )
        x += tl
    parts.append("</svg>")
    return "".join(parts)


def svg_favicon(entity, params):
    """Sprite only, full bleed on the slot grid, for 16px tabs."""
    n, cell, cells = sprite_cells(entity, params)
    slot = params["grid"]["slotUnits"]
    canvas = max(slot, n * cell)
    off = (canvas - n * cell) // 2
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {canvas} {canvas}" '
        f'role="img" aria-label="{entity["id"]} favicon" shape-rendering="crispEdges">'
        f"{cell_rects(cells, (off, off), cell)}</svg>"
    )


# ------------------------------------------------------------------- readme --
def readme_md(params):
    def row(e):
        eid = e["id"]
        kind = "fallback demo" if e.get("demo") else e["kind"]
        cells = " | ".join(
            f'<a href="{CDN}/{eid}/{name}.svg">'
            f'<img src="assets/{eid}/{name}.svg" width="{w}" alt="{eid} {name}"></a>'
            for name, w in (("chip", 96), ("avatar", 96), ("lockup", 96), ("favicon", 32))
        )
        return f"| **{eid}**<br><sub>{kind}</sub> | {cells} | `{CDN}/{eid}/` |"

    rows = "\n".join(row(e) for e in params["entities"])
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
"""


# --------------------------------------------------------------------- main --
def main():
    params = load_params()
    verify_canon(params)
    ASSETS.mkdir(exist_ok=True)
    for e in params["entities"]:
        d = ASSETS / e["id"]
        d.mkdir(exist_ok=True)
        marks = {
            "chip": svg_chip(e, params),
            "avatar": svg_avatar(e, params),
            "favicon": svg_favicon(e, params),
            "lockup": svg_lockup(e, params),
        }
        for name, svg in marks.items():
            (d / f"{name}.svg").write_text(svg + "\n", encoding="utf-8")
    (ROOT / "README.md").write_text(readme_md(params), encoding="utf-8")
    print(f"generated {len(params['entities'])} marks -> {ASSETS} + README.md")


if __name__ == "__main__":
    sys.exit(main())
