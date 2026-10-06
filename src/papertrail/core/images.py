"""Image intake: decode images or a PDF's first page, fix orientation, downscale, hash."""

from __future__ import annotations

import base64
import hashlib
import io
from dataclasses import dataclass

from PIL import Image, ImageOps

OCR_MAX_SIDE = 2200


@dataclass(frozen=True)
class PreparedImage:
    """A document image ready for the LLM (``jpeg``) and for OCR (``ocr_jpeg``)."""

    jpeg: bytes
    ocr_jpeg: bytes
    width: int
    height: int
    sha256: str
    dhash: int
    name: str = ""

    def data_url(self) -> str:
        """Base64 data URL for the chat-completions ``image_url`` content part."""
        return "data:image/jpeg;base64," + base64.b64encode(self.jpeg).decode()

    def ocr_image(self) -> Image.Image:
        """Higher-resolution copy for OCR."""
        return Image.open(io.BytesIO(self.ocr_jpeg))


def _first_pdf_page(data: bytes) -> Image.Image:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        page = pdf[0]
        return page.render(scale=2.0).to_pil()
    finally:
        pdf.close()


def _encode(img: Image.Image, max_side: int, quality: int) -> bytes:
    copy = img.copy()
    copy.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    copy.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def dhash(img: Image.Image, size: int = 8) -> int:
    """64-bit difference hash: robust to rescaling and recompression, used for duplicate detection."""
    small = img.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
    pixels = list(small.getdata())
    bits = 0
    for row in range(size):
        for col in range(size):
            left, right = pixels[row * (size + 1) + col], pixels[row * (size + 1) + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits


def load_document(data: bytes, name: str = "", max_side: int = 1024) -> PreparedImage:
    """Decode an image (or the first page of a PDF) into a PreparedImage.

    Raises ValueError if the bytes are neither a readable image nor a PDF.
    """
    try:
        if data[:5] == b"%PDF-":
            img = _first_pdf_page(data)
        else:
            img = Image.open(io.BytesIO(data))
            img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
    except Exception as exc:  # PIL raises many types for bad input
        raise ValueError(f"could not read {name or 'upload'} as an image or PDF: {exc}") from exc
    jpeg = _encode(img, max_side, 85)
    shown = Image.open(io.BytesIO(jpeg))
    return PreparedImage(
        jpeg=jpeg,
        ocr_jpeg=_encode(img, OCR_MAX_SIDE, 92),
        width=shown.width,
        height=shown.height,
        sha256=hashlib.sha256(jpeg).hexdigest(),
        dhash=dhash(img),
        name=name,
    )
