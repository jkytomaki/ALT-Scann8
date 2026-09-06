"""Render a small annotated copy, without changing the captured image."""
from io import BytesIO
from PIL import Image, ImageDraw


def preview_jpeg(image, overlay=None):
    preview = image.convert('RGB')
    preview.thumbnail((1000, 750), Image.Resampling.LANCZOS)
    if overlay:
        offset, tolerance, shift, source = overlay
        draw = ImageDraw.Draw(preview)
        width, height = preview.size
        target = height * (0.5 + shift)
        for y in (target - height * tolerance / 100, target + height * tolerance / 100):
            draw.line((0, y, width, y), fill='#00ff80', width=2)
        draw.line((0, target, width, target), fill='#00ffff', width=2)
        if offset is not None:
            y = target + offset * height
            draw.line((0, y, width * 0.18, y), fill='#ff5050', width=4)
        text = ('Position unknown' if offset is None else f'Offset {offset * 100:+.1f}%')
        text += f' | tolerance +/-{tolerance:g}% | {source}'
        draw.rectangle((0, 0, width, 24), fill='black')
        draw.text((6, 6), text, fill='white')
    output = BytesIO()
    preview.save(output, 'JPEG', quality=80)
    return output.getvalue()
