from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import base64
import binascii
import math
import warnings

from PIL import Image, ImageChops, ImageFilter


@dataclass
class PreparedEdit:
    source: Image.Image
    mask: Image.Image
    box: tuple[int, int, int, int]
    model_image: Image.Image
    model_mask: Image.Image
    padded_size: tuple[int, int] | None = None
    resized_size: tuple[int, int] | None = None


def _pad_edges(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Extend only outside the image; keep existing pixels at their coordinates."""
    if image.size == size:
        return image
    working = Image.new(image.mode, size)
    working.paste(image, (0, 0))
    if size[0] > image.width:
        edge = image.crop((image.width - 1, 0, image.width, image.height))
        working.paste(edge.resize((size[0] - image.width, image.height)), (image.width, 0))
    if size[1] > image.height:
        edge = working.crop((0, image.height - 1, size[0], image.height))
        working.paste(edge.resize((size[0], size[1] - image.height)), (0, image.height))
    return working


def decode_png(encoded: str, max_pixels: int) -> Image.Image:
    if not isinstance(encoded, str):
        raise ValueError("The image must be a base64-encoded PNG.")
    try:
        data = base64.b64decode(encoded, validate=True)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.format != "PNG":
                    raise ValueError("Only PNG images are supported.")
                if image.width * image.height > max_pixels:
                    raise ValueError(f"The image exceeds the limit of {max_pixels:,} pixels.")
                image.load()
                return image.copy()
    except (binascii.Error, OSError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as error:
        raise ValueError("Could not read the PNG image.") from error


def png_bytes(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def bounded_int(value, low: int, high: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{name}: expected an integer between {low} and {high}.")
    return value


def prepare_edit(source: Image.Image, mask: Image.Image, *, context: int = 96,
                 max_size: int = 768, grow: int = 8, preserve_roi: bool = False,
                 pixel_aligned: bool = False, downsample: bool = False) -> PreparedEdit:
    if mask.size != source.size:
        raise ValueError("The mask must have the same dimensions as the image.")
    source, mask = source.convert("RGBA"), mask.convert("L")
    bounds = mask.getbbox()
    if bounds is None:
        raise ValueError("The mask is empty. Make a selection or paint over an object.")
    context = bounded_int(context, 0, 1024, "Context")
    max_size = bounded_int(max_size, 256, 2048, "Resolution")
    grow = bounded_int(grow, 0, 64, "Mask expansion")
    x0, y0, x1, y1 = bounds
    # At least 256 source pixels of context for small masks; clamp at image borders.
    pad_x = max(context + grow, (256 - (x1 - x0) + 1) // 2)
    pad_y = max(context + grow, (256 - (y1 - y0) + 1) // 2)
    box = (max(0, x0 - pad_x), max(0, y0 - pad_y),
           min(source.width, x1 + pad_x), min(source.height, y1 + pad_y))
    if preserve_roi:
        box = (0, 0, source.width, source.height)
    cropped, crop_mask = source.crop(box), mask.crop(box)
    if pixel_aligned:
        # Match the model grid by extending the rectangle, never by stretching
        # its pixels. Native capture supplies real context when canvas space is
        # available; at canvas edges replicate only the missing outside border.
        padded = tuple((n + 31) // 32 * 32 for n in cropped.size)
        if padded[0] * padded[1] > 16 << 20 or max(padded) > 16384:
            raise ValueError("The aligned AI fill rectangle is too large. Use a smaller selection.")
        working = _pad_edges(cropped, padded)
        inference_mask = Image.new("L", padded)
        inference_mask.paste(crop_mask, (0, 0))
        if grow:
            inference_mask = inference_mask.filter(ImageFilter.MaxFilter(2 * grow + 1))
        resized = None
        if downsample and max(cropped.size) > 768:
            # Progressive economy: no reduction through 768 px, sqrt growth
            # above it, and at most 2x reduction from 3072 px onward. Round
            # content UP, then pad to the grid instead of distorting its ratio.
            scale = max(0.5, math.sqrt(768 / max(padded)))
            size = tuple(math.ceil(n * scale) for n in padded)
            if size != padded:
                resized = size
                model_size = tuple((n + 31) // 32 * 32 for n in size)
                working = _pad_edges(working.resize(size, Image.Resampling.LANCZOS), model_size)
                # Preserve even a single-pixel stroke during <=2x reduction.
                small_mask = inference_mask.filter(ImageFilter.MaxFilter(3)).resize(size, Image.Resampling.NEAREST)
                inference_mask = Image.new("L", model_size)
                inference_mask.paste(small_mask, (0, 0))
        return PreparedEdit(source, mask, box, working, inference_mask, padded, resized)
    scale = min(1.0, max_size / max(cropped.size))
    size = tuple(max(32, min(max_size // 32 * 32, round(n * scale / 32) * 32))
                 for n in cropped.size)
    inference_mask = crop_mask.filter(ImageFilter.MaxFilter(2 * grow + 1)) if grow else crop_mask
    # A max reduction preserves even single-pixel marks when reducing a large ROI.
    reduction = max(cropped.width / size[0], cropped.height / size[1])
    if reduction > 1:
        inference_mask = inference_mask.filter(ImageFilter.MaxFilter(2 * int(reduction) + 1))
    return PreparedEdit(source, mask, box,
                        cropped.resize(size, Image.Resampling.LANCZOS),
                        inference_mask.resize(size, Image.Resampling.NEAREST))


def masked_reference(image: Image.Image, mask: Image.Image) -> Image.Image:
    """RGBA reference with an explicit hole, including the expanded inference margin.

    White RGB under zero alpha avoids leaking the removed object to a vision encoder
    that composites transparent pixels onto white. Alpha is the missing-area marker.
    """
    reference = image.convert("RGBA")
    hole = mask.convert("L").point(lambda value: 255 if value else 0)
    return Image.composite(Image.new("RGBA", reference.size, (255, 255, 255, 0)), reference, hole)


def restore_result(edit: PreparedEdit, generated: Image.Image) -> Image.Image:
    x0, y0, x1, y1 = edit.box
    generated = generated.convert("RGBA")
    if edit.padded_size is None:
        return generated.resize((x1 - x0, y1 - y0), Image.Resampling.LANCZOS)
    if generated.size != edit.model_image.size:
        raise ValueError("The engine returned different dimensions from the requested AI fill rectangle.")
    if edit.resized_size is not None:
        generated = generated.crop((0, 0, *edit.resized_size))
        generated = generated.resize(edit.padded_size, Image.Resampling.LANCZOS)
    return generated.crop((0, 0, x1 - x0, y1 - y0))


def result_patch(edit: PreparedEdit, generated: Image.Image, feather: int = 4) -> Image.Image:
    feather = bounded_int(feather, 0, 64, "Edge softness")
    generated = restore_result(edit, generated)
    alpha = edit.mask.crop(edit.box)
    if feather:
        # Feather inward only: zeros outside the user's mask remain exactly zero.
        alpha = ImageChops.multiply(alpha, alpha.filter(ImageFilter.GaussianBlur(feather)))
    alpha = ImageChops.multiply(alpha, generated.getchannel("A"))
    generated.putalpha(alpha)
    return generated
