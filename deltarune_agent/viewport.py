from __future__ import annotations


def camera_viewport_box(
    image_size: tuple[int, int],
    camera_size: tuple[float, float],
) -> tuple[int, int, int, int] | None:
    """Return the centered frame area that represents the reported camera."""
    image_width, image_height = image_size
    camera_width, camera_height = camera_size
    if (
        image_width <= 0
        or image_height <= 0
        or camera_width <= 0
        or camera_height <= 0
    ):
        return None
    image_ratio = image_width / image_height
    camera_ratio = camera_width / camera_height
    if abs(image_ratio - camera_ratio) <= 0.002:
        return (0, 0, image_width, image_height)
    if image_ratio > camera_ratio:
        viewport_width = max(1, round(image_height * camera_ratio))
        left = (image_width - viewport_width) // 2
        return (left, 0, left + viewport_width, image_height)
    viewport_height = max(1, round(image_width / camera_ratio))
    top = (image_height - viewport_height) // 2
    return (0, top, image_width, top + viewport_height)
