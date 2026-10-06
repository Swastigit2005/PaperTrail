"""Scan/photo degradations per difficulty tier: clean, scanned, hard."""

from __future__ import annotations

import io
import random

from PIL import Image, ImageChops, ImageDraw, ImageFilter

TIERS = ("clean", "scanned", "hard")


def _gradient(size: tuple[int, int], rng: random.Random, low: float) -> Image.Image:
    """Uneven lighting: a soft brightness ramp from one corner."""
    w, h = size
    small = Image.new("L", (2, 2))
    vals = [255, int(255 * rng.uniform(low, 1.0)), int(255 * rng.uniform(low, 1.0)), int(255 * low)]
    rng.shuffle(vals)
    small.putdata(vals)
    return small.resize(size, Image.Resampling.BILINEAR).convert("RGB")


def _noise(img: Image.Image, sigma: float) -> Image.Image:
    """Zero-mean Gaussian noise: effect_noise is centred on 128, so add it with offset -128."""
    noise = Image.effect_noise(img.size, sigma).convert("RGB")
    return ImageChops.add(img, noise, scale=1.0, offset=-128)


def _jpeg(img: Image.Image, quality: int) -> Image.Image:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")


def occlude(img: Image.Image, box: tuple[int, int, int, int], rng: random.Random) -> None:
    """Cover most of a field with a sticky note or a coffee stain (in place)."""
    x0, y0, x1, y1 = box
    pad_x, pad_y = (x1 - x0) * 0.1, (y1 - y0) * 0.35
    cover = (x0 - pad_x + rng.uniform(0, (x1 - x0) * 0.1), y0 - pad_y, x1 + pad_x, y1 + pad_y)
    d = ImageDraw.Draw(img)
    if rng.random() < 0.5:
        d.rectangle(cover, fill=(250, 232, 120))  # sticky note
    else:
        d.ellipse(cover, fill=(150, 110, 70))  # stain


def degrade(img: Image.Image, tier: str, rng: random.Random) -> tuple[Image.Image, dict]:
    """Apply a tier's degradations; returns the image and the parameters used."""
    params: dict = {"tier": tier}
    if tier == "clean":
        return img, params
    if tier == "scanned":
        angle, blur, sigma, light, quality = (rng.uniform(-2.5, 2.5), rng.uniform(0.4, 0.8), rng.uniform(6, 12),
                                              rng.uniform(0.78, 0.92), rng.randint(45, 65))
        scale = 1.0
    elif tier == "hard":
        angle, blur, sigma, light, quality = (rng.uniform(-5, 5), rng.uniform(0.9, 1.4), rng.uniform(18, 28),
                                              rng.uniform(0.6, 0.75), rng.randint(25, 40))
        scale = rng.uniform(0.42, 0.55)
    else:
        raise ValueError(tier)
    params.update(angle=round(angle, 2), blur=round(blur, 2), noise=round(sigma, 1), light=round(light, 2),
                  jpeg=quality, scale=round(scale, 2))
    out = img.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=(235, 235, 230))
    if scale < 1.0:
        w, h = out.size
        out = out.resize((int(w * scale), int(h * scale)), Image.Resampling.BILINEAR).resize(
            (w, h), Image.Resampling.BILINEAR)
    out = out.filter(ImageFilter.GaussianBlur(blur))
    out = ImageChops.multiply(out, _gradient(out.size, rng, light))
    out = _noise(out, sigma)
    return _jpeg(out, quality), params
