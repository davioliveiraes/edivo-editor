"""Layout for the application-owned DejaVu font, without additional dependencies.

Only the fixed, licensed runtime font is accepted in production. FreeType is
already required by FFmpeg/drawtext. Faces are private to one layout operation;
the shared library has no mutable face state shared across worker threads.
"""

import ctypes
import hashlib
import struct
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

FONT_PATH = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
FONT_SHA256 = "abdc775b21b1bc470d50c97e790d276f2054b7504e56e5bd3e64f48d68582322"
SIZE_RATIOS = {"small": 0.042, "medium": 0.056, "large": 0.072}


class FontLayoutError(RuntimeError):
    """Public-safe failure; never include font paths or text contents."""


class _Vector(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


@lru_cache(maxsize=1)
def _freetype() -> ctypes.CDLL:
    try:
        library = ctypes.CDLL("libfreetype.so.6")
        signatures = {
            "FT_Init_FreeType": ([ctypes.POINTER(ctypes.c_void_p)], ctypes.c_int),
            "FT_New_Face": (
                [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.POINTER(ctypes.c_void_p)],
                ctypes.c_int,
            ),
            "FT_Set_Pixel_Sizes": ([ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint], ctypes.c_int),
            "FT_Get_Char_Index": ([ctypes.c_void_p, ctypes.c_ulong], ctypes.c_uint),
            "FT_Get_Advance": (
                [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_long)],
                ctypes.c_int,
            ),
            "FT_Get_Kerning": (
                [
                    ctypes.c_void_p,
                    ctypes.c_uint,
                    ctypes.c_uint,
                    ctypes.c_uint,
                    ctypes.POINTER(_Vector),
                ],
                ctypes.c_int,
            ),
            "FT_Done_Face": ([ctypes.c_void_p], ctypes.c_int),
            "FT_Done_FreeType": ([ctypes.c_void_p], ctypes.c_int),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(library, name)
            function.argtypes = arguments
            function.restype = result
        return library
    except (OSError, AttributeError):
        raise FontLayoutError("preview_font_unavailable") from None


def _vertical_metrics(data: bytes) -> tuple[float, float]:
    """Read fixed font hhea/head metrics used for CSS-compatible line boxes."""
    try:
        count = struct.unpack_from(">H", data, 4)[0]
        tables = {}
        for index in range(count):
            tag, _, offset, length = struct.unpack_from(">4sIII", data, 12 + index * 16)
            if offset + length > len(data):
                raise ValueError
            tables[tag] = offset
        units = struct.unpack_from(">H", data, tables[b"head"] + 18)[0]
        ascent, descent = struct.unpack_from(">hh", data, tables[b"hhea"] + 4)
        if units <= 0 or ascent <= 0 or descent > 0:
            raise ValueError
        return ascent / units, -descent / units
    except (ValueError, KeyError, struct.error):
        raise FontLayoutError("preview_font_unavailable") from None


@dataclass(frozen=True)
class TextLayout:
    font_size: int
    border_width: int
    line_height: float
    baseline: float
    block_top: float
    line_widths: tuple[float, ...]


class FontMetrics:
    def __init__(self, path: Path = FONT_PATH) -> None:
        # A path argument is internal/test-only; no HTTP setting selects fonts.
        self.path = path
        self.library = _freetype()
        self.handle, self.face = ctypes.c_void_p(), ctypes.c_void_p()
        try:
            data = path.read_bytes()
            if len(data) > 2 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != FONT_SHA256:
                raise FontLayoutError("preview_font_unavailable")
            self.ascent, self.descent = _vertical_metrics(data)
            if self.library.FT_Init_FreeType(ctypes.byref(self.handle)):
                raise FontLayoutError("preview_font_unavailable")
            if self.library.FT_New_Face(self.handle, bytes(path), 0, ctypes.byref(self.face)):
                raise FontLayoutError("preview_font_unavailable")
        except (OSError, FontLayoutError):
            self.close()
            raise FontLayoutError("preview_font_unavailable") from None

    def __enter__(self) -> "FontMetrics":
        return self

    def __exit__(self, *_arguments: object) -> None:
        self.close()

    def close(self) -> None:
        if self.face:
            self.library.FT_Done_Face(self.face)
            self.face = ctypes.c_void_p()
        if self.handle:
            self.library.FT_Done_FreeType(self.handle)
            self.handle = ctypes.c_void_p()

    def width(self, text: str, pixels: int) -> float:
        if self.library.FT_Set_Pixel_Sizes(self.face, 0, pixels):
            raise FontLayoutError("preview_font_unavailable")
        width, previous = 0.0, 0
        for character in text:
            glyph = self.library.FT_Get_Char_Index(self.face, ord(character))
            if glyph == 0:
                raise FontLayoutError("preview_text_unsupported")
            kerning, advance = _Vector(), ctypes.c_long()
            if previous and self.library.FT_Get_Kerning(
                self.face, previous, glyph, 0, ctypes.byref(kerning)
            ):
                raise FontLayoutError("preview_font_unavailable")
            if self.library.FT_Get_Advance(self.face, glyph, 0, ctypes.byref(advance)):
                raise FontLayoutError("preview_font_unavailable")
            width += kerning.x / 64 + advance.value / 65536
            previous = glyph
        return width

    def layout(
        self, text: str, size: str, position: str, outlined: bool, width: int, height: int
    ) -> TextLayout:
        lines = text.split("\n")
        base = height * SIZE_RATIOS[size]
        available = width * 0.86 - max(2, base * 0.08)
        pixels = max(1, round(base))
        maximum = max(self.width(line, pixels) for line in lines)
        if maximum > available:
            pixels = max(1, int(pixels * available / maximum))
        while True:
            widths = tuple(self.width(line, pixels) for line in lines)
            border = max(1, round(pixels * 0.04)) if outlined else 0
            if max(widths) + 2 * border <= width * 0.86 or pixels == 1:
                break
            pixels -= 1
        line_height = pixels * 1.2
        block_height = line_height * len(lines)
        block_top = {
            "top": height * 0.07,
            "center": (height - block_height) / 2,
            "bottom": height * 0.93 - block_height,
        }[position]
        baseline = pixels * self.ascent + (line_height - pixels * (self.ascent + self.descent)) / 2
        return TextLayout(pixels, border, line_height, baseline, block_top, widths)
