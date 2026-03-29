"""Utilities for before/after screenshot comparisons in chatbot workflows."""

from __future__ import annotations

from PIL import Image, ImageChops, ImageDraw


def crop_image_bounds(
    image: Image.Image,
    bounds: tuple[float, float, float, float] | None,
) -> Image.Image:
    """Crop a normalized region from an image."""
    if not bounds:
        return image
    left, top, right, bottom = bounds
    width, height = image.size
    crop_box = (
        max(0, min(width, int(width * left))),
        max(0, min(height, int(height * top))),
        max(0, min(width, int(width * right))),
        max(0, min(height, int(height * bottom))),
    )
    if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
        return image
    return image.crop(crop_box)


def build_transition_composite(
    before: Image.Image,
    after: Image.Image,
    *,
    crop_bounds: tuple[float, float, float, float] | None = None,
    include_diff: bool = True,
) -> Image.Image:
    """Build a left-to-right BEFORE / AFTER / DIFF composite image."""
    before = crop_image_bounds(before.convert("RGB"), crop_bounds)
    after = crop_image_bounds(after.convert("RGB"), crop_bounds)
    if after.size != before.size:
        after = after.resize(before.size, Image.LANCZOS)

    panels = [before, after]
    labels = ["BEFORE", "AFTER"]
    if include_diff:
        diff = ImageChops.difference(before, after)
        diff = ImageChops.multiply(diff, Image.new("RGB", diff.size, (3, 3, 3)))
        panels.append(diff)
        labels.append("DIFF")

    panel_width, panel_height = before.size
    gutter = 16
    label_height = 36
    canvas = Image.new(
        "RGB",
        (panel_width * len(panels) + gutter * (len(panels) - 1), panel_height + label_height),
        (255, 255, 255),
    )
    draw = ImageDraw.Draw(canvas)

    for index, (panel, label) in enumerate(zip(panels, labels)):
        left = index * (panel_width + gutter)
        canvas.paste(panel, (left, label_height))
        draw.rectangle((left, 0, left + panel_width, label_height), fill=(24, 24, 28))
        draw.text((left + 12, 10), label, fill=(255, 255, 255))

    return canvas
