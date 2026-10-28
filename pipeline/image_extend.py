"""Shift art and fill newly exposed edges via a masked images.edit (or soft fill fallback)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image


def _mean_rgb(pixels) -> tuple[int, int, int]:
    n = len(pixels) or 1
    return (
        sum(p[0] for p in pixels) // n,
        sum(p[1] for p in pixels) // n,
        sum(p[2] for p in pixels) // n,
    )


def edge_fill_colour(rgb: Image.Image, *, top: bool = True, left: bool = True) -> tuple[int, int, int]:
    width, height = rgb.size
    band = max(1, min(8, height // 40, width // 40))
    if top and left:
        sample = rgb.crop((0, 0, width, band))
    elif top:
        sample = rgb.crop((0, 0, width, band))
    elif left:
        sample = rgb.crop((0, 0, band, height))
    else:
        sample = rgb.crop((0, max(0, height - band), width, height))
    return _mean_rgb(list(sample.getdata()))


def shift_canvas(
    rgb: Image.Image, dx_px: int, dy_px: int
) -> tuple[Image.Image, Image.Image]:
    """Paste ``rgb`` shifted by (dx, dy) onto a fill canvas; return (canvas, mask).

    Mask: white (255) where the original art remains, black (0) on exposed edges
    (OpenAI edits the *transparent* / zero-alpha region — we store an L mask where
    0 = extend zone, 255 = keep).
    """
    width, height = rgb.size
    fill = edge_fill_colour(rgb, top=dy_px >= 0, left=dx_px >= 0)
    canvas = Image.new("RGB", (width, height), fill)
    mask = Image.new("L", (width, height), 0)  # 0 = exposed (to extend)

    src_x0 = max(0, -dx_px)
    src_y0 = max(0, -dy_px)
    dst_x0 = max(0, dx_px)
    dst_y0 = max(0, dy_px)
    src_x1 = min(width, width - dx_px)
    src_y1 = min(height, height - dy_px)
    if src_x1 > src_x0 and src_y1 > src_y0:
        cropped = rgb.crop((src_x0, src_y0, src_x1, src_y1))
        canvas.paste(cropped, (dst_x0, dst_y0))
        keep = Image.new("L", cropped.size, 255)
        mask.paste(keep, (dst_x0, dst_y0))
    return canvas, mask


def soft_blend_exposed(canvas: Image.Image, mask: Image.Image, rgb: Image.Image, dx: int, dy: int) -> Image.Image:
    """Deterministic soft edge blend when the model extend is unavailable."""
    width, height = canvas.size
    out = canvas.copy()
    pixels = out.load()
    m = mask.load()
    src = rgb.load()
    # Soften a band along the keep/expose boundary.
    blend = max(4, min(abs(dy) or abs(dx) or 8, height // 30, width // 30))
    for y in range(height):
        for x in range(width):
            if m[x, y] >= 200:
                continue
            # Find nearest kept pixel roughly toward the content.
            sx = min(width - 1, max(0, x - dx))
            sy = min(height - 1, max(0, y - dy))
            # Distance into the expose zone from the paste edge.
            if dy > 0 and y < dy:
                t = min(1.0, (dy - y) / blend)
                edge = src[min(width - 1, max(0, x - dx)), 0] if 0 <= x - dx < width else pixels[x, y]
            elif dy < 0 and y >= height + dy:
                t = min(1.0, (y - (height + dy - 1)) / blend)
                edge = src[min(width - 1, max(0, x - dx)), height - 1] if 0 <= x - dx < width else pixels[x, y]
            elif dx > 0 and x < dx:
                t = min(1.0, (dx - x) / blend)
                edge = src[0, min(height - 1, max(0, y - dy))] if 0 <= y - dy < height else pixels[x, y]
            elif dx < 0 and x >= width + dx:
                t = min(1.0, (x - (width + dx - 1)) / blend)
                edge = src[width - 1, min(height - 1, max(0, y - dy))] if 0 <= y - dy < height else pixels[x, y]
            else:
                continue
            fill = pixels[x, y]
            pixels[x, y] = (
                int(fill[0] * t + edge[0] * (1 - t)),
                int(fill[1] * t + edge[1] * (1 - t)),
                int(fill[2] * t + edge[2] * (1 - t)),
            )
    return out


def apply_shift_only(
    art_path: Path,
    shift_x: float,
    shift_y: float,
    *,
    client=None,
    extend_prompt: str = "Extend the background seamlessly. No new characters, faces, or objects. Match style and lighting.",
) -> dict:
    """Shift the image (no scale). Prefer masked model extend for exposed edges; else soft fill.

    ``shift_x`` / ``shift_y`` are fractions of width/height (positive moves content right/down).
    """
    path = Path(art_path)
    with Image.open(path) as source:
        rgb = source.convert("RGB")
        width, height = rgb.size
        dx = int(round(shift_x * width))
        dy = int(round(shift_y * height))
        if dx == 0 and dy == 0:
            return {"shifted": False, "extended": False, "dx": 0, "dy": 0}
        canvas, mask = shift_canvas(rgb, dx, dy)
        extended = False
        if client is not None and (abs(dx) > 2 or abs(dy) > 2):
            try:
                from .openai_util import image_edit, square_image_size, largest_image_size

                # Mask PNG: transparent = edit zone (OpenAI convention).
                rgba_mask = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                keep_rgb = Image.new("RGB", (width, height), (0, 0, 0))
                # Where mask is 255 (keep), put opaque black; exposed stays transparent.
                alpha = mask  # 255 keep → opaque
                rgba_mask = Image.merge(
                    "RGBA",
                    (
                        keep_rgb.split()[0],
                        keep_rgb.split()[1],
                        keep_rgb.split()[2],
                        alpha,
                    ),
                )
                work = path.parent / f".{path.stem}_extend_work.png"
                mask_path = path.parent / f".{path.stem}_extend_mask.png"
                canvas.save(work, format="PNG")
                rgba_mask.save(mask_path, format="PNG")
                size = square_image_size() if width == height else largest_image_size(aspect="landscape")
                result = image_edit(
                    client,
                    work,
                    extend_prompt,
                    size=size,
                    mask_path=mask_path,
                    description="Background extend",
                )
                data = getattr(result.data[0], "b64_json", None) if result.data else None
                if data:
                    import base64

                    path.write_bytes(base64.b64decode(data))
                    extended = True
                for junk in (work, mask_path):
                    if junk.is_file():
                        junk.unlink()
            except Exception:  # noqa: BLE001
                extended = False
        if not extended:
            out = soft_blend_exposed(canvas, mask, rgb, dx, dy)
            out.save(path, format="PNG")
        return {"shifted": True, "extended": extended, "dx": dx, "dy": dy}
