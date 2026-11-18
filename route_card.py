"""Locate a selected route card in a portrait TMAP route carousel."""
from pathlib import Path
import re

try:
    from PIL import Image, ImageFilter
except ImportError:
    Image = None


def _component_bounds(mask, seed, stride):
    """Consume one connected blue component in a mask with a zero border."""
    pending = [seed]
    mask[seed] = 0
    left = right = seed % stride
    top = bottom = seed // stride
    while pending:
        index = pending.pop()
        y, x = divmod(index, stride)
        left, right = min(left, x), max(right, x)
        top, bottom = min(top, y), max(bottom, y)
        for neighbor in (index - 1, index + 1, index - stride, index + stride):
            if mask[neighbor]:
                mask[neighbor] = 0
                pending.append(neighbor)
    return left, top, right, bottom


def _card_box(pixels):
    # Keep a one-pixel empty border so components can be traversed at the screen edges.
    w, h = pixels.size
    rgb = pixels.load()
    blue = bytearray(w * h)
    for y in range(h):
        for x in range(w):
            r, g, b = rgb[x, y]
            if r <= 85 and 35 <= g <= 170 and b >= 155 and b - g >= 65 and g - r >= 25:
                blue[y * w + x] = 255
    mask = Image.frombytes('L', (w, h), bytes(blue))
    padded = Image.new('L', (w + 2, h + 2))
    padded.paste(mask.filter(ImageFilter.MaxFilter(3)), (1, 1))
    outline = padded.load()
    work = bytearray(padded.tobytes())
    stride = w + 2
    # An outline must have a substantial horizontal edge. Ignore small map details.
    edge = re.compile(b'\xff{%d,}' % max(4, round(w * .15)))
    candidates = []
    for run in edge.finditer(bytes(work)):
        if not work[run.start()]:
            continue
        left, top, right, bottom = _component_bounds(work, run.start(), stride)
        width, height = right - left, bottom - top
        if not (.22 * w < width < .66 * w and .12 * w < height < .55 * w
                and .78 < width / height < 3.2):
            continue
        # Sample the straight sections away from the rounded corners.
        xs = [left + round(width * k / 10) for k in range(2, 9)]
        ys = [top + round(height * k / 10) for k in range(2, 9)]
        if not all(any(all(outline[x, y] for x in xs) for y in y_range)
                   for y_range in (range(top, top + 4), range(bottom - 3, bottom + 1))):
            continue
        if not all(any(all(outline[x, y] for y in ys) for x in x_range)
                   for x_range in (range(left, left + 4), range(right - 3, right + 1))):
            continue
        # Reject filled action buttons, dark map regions and other selected controls.
        light_samples = sum(min(rgb[x - 1, y - 1]) > 205 for x in xs for y in ys)
        if light_samples < .60 * len(xs) * len(ys):
            continue
        candidates.append((left - 1, top - 1, right, bottom))
        if len(candidates) > 1:
            return None
    return candidates[0] if candidates else None


def find_selected_route_card(image_path: str | Path) -> tuple[float, float, float, float] | None:
    """Return normalized left/top/right/bottom bounds, or None without a clear outline."""
    if Image is None:
        return None
    try:
        with Image.open(image_path) as source:
            if not (source.width >= 180 and 1 <= source.height / source.width <= 3.5):
                return None
            width = min(source.width, 360)
            height = round(source.height * width / source.width)
            # Only the lower carousel matters. A cut-off edge will not pass the outline checks.
            top = round(height * .50)
            bottom = round(height * .95)
            region = (0, source.height * top / height, source.width, source.height * bottom / height)
            pixels = source.convert('RGB').resize((width, bottom - top), Image.Resampling.BOX, box=region)
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
    box = _card_box(pixels)
    if box:
        x1, y1, x2, y2 = box
        return x1 / width, (y1 + top) / height, x2 / width, (y2 + top) / height
    return None
