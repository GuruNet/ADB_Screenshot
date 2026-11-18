"""Display an image within available bounds without changing the original."""
from __future__ import annotations

import math
import tkinter as tk

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = ImageTk = None


def fitted_size(width, height, max_width, max_height):
    """Preserve proportions and never enlarge beyond the source dimensions."""
    if min(width, height, max_width, max_height) < 1:
        raise ValueError('이미지와 표시 영역의 크기를 확인하세요.')
    ratio = min(1.0, max_width / width, max_height / height)
    return max(1, int(width * ratio)), max(1, int(height * ratio))


def load_preview(path, master):
    if Image is None:
        # shortcut: Tk-only fallback uses integer scaling; install Pillow for smooth fitting.
        return tk.PhotoImage(master=master, file=str(path))
    try:
        with Image.open(path) as image:
            image.load()
            return image.convert('RGBA')
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise tk.TclError('이미지를 읽을 수 없습니다.') from exc


def preview_size(source, width, height):
    if Image is not None and isinstance(source, Image.Image):
        return fitted_size(source.width, source.height, width, height)
    source_width, source_height = source.width(), source.height()
    factor = max(1, math.ceil(max(source_width / width, source_height / height)))
    return math.ceil(source_width / factor), math.ceil(source_height / factor)


def render_preview(source, size, master):
    if Image is not None and isinstance(source, Image.Image):
        rendered = source.resize(size, Image.Resampling.LANCZOS) if size != source.size else source
        return ImageTk.PhotoImage(rendered, master=master)
    factor = max(1, math.ceil(source.width() / size[0]), math.ceil(source.height() / size[1]))
    return source.subsample(factor, factor)
