"""Visual templates: ffmpeg filter chains that turn source footage into a vertical frame.

Each template takes an input label, an output label and the target size, and returns
filter_complex text. Rotating between them keeps renders from looking uniform.
"""

from collections.abc import Callable


def blur_fill(src: str, out: str, w: int, h: int) -> str:
    """Whole frame centred over a blurred, darkened copy of itself."""
    return (
        f"[{src}]split=2[bf_a][bf_b];"
        f"[bf_a]scale={w // 4}:{h // 4}:force_original_aspect_ratio=increase,crop={w // 4}:{h // 4},"
        f"boxblur=8:2,scale={w}:{h},eq=brightness=-0.12[bf_bg];"
        f"[bf_b]scale={w}:{h}:force_original_aspect_ratio=decrease[bf_fg];"
        f"[bf_bg][bf_fg]overlay=(W-w)/2:(H-h)/2[{out}]"
    )


def center_crop(src: str, out: str, w: int, h: int) -> str:
    """Fill the frame with the centre of the footage."""
    return f"[{src}]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}[{out}]"


def stack_zoom(src: str, out: str, w: int, h: int) -> str:
    """Whole frame on top, a 2x zoom of its centre underneath."""
    return (
        f"[{src}]split=2[sz_a][sz_b];"
        f"[sz_a]scale={w}:-2[sz_top];"
        f"[sz_b]crop=iw/2:ih/2,scale={w}:-2[sz_bot];"
        f"[sz_top][sz_bot]vstack[sz_st];"
        f"[sz_st]scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=0x101014[{out}]"
    )


def subject_crop(src: str, out: str, w: int, h: int) -> str:
    """Segments arrive already cropped around the moving subject (see render/subject.py)."""
    return f"[{src}]null[{out}]"


# Templates that need a per-segment crop centre. When no clear subject is found the render
# falls back to FALLBACK_TEMPLATE and says so in the sidecar.
SUBJECT_AWARE = frozenset({"subject_crop"})
FALLBACK_TEMPLATE = "blur_fill"

TEMPLATES: dict[str, Callable[[str, str, int, int], str]] = {
    "blur_fill": blur_fill,
    "center_crop": center_crop,
    "stack_zoom": stack_zoom,
    "subject_crop": subject_crop,
}
