# Copyright 2026 Firefly Software Solutions Inc
"""``ImageNormalizer`` -- HEIC / AVIF / TIFF / BMP / SVG -> PNG.

The loader pipeline (and the downstream OCR) only handles raster
formats that Pillow can decode natively. The normaliser ships HEIC
(iPhone photos), AVIF, multi-frame TIFF (fax scans), BMP, and SVG
to a canonical PNG so the OCR adapter never has to special-case
them.

Pillow registers HEIF support through ``pillow-heif``; AVIF support
is also handled by the same plugin since v0.18. SVG is rasterised via
``cairosvg`` (vector -> 2x DPI PNG).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from io import BytesIO

from pyfly.container import service

from flycanon.core.services.binary.errors import ImageConversionError

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class NormalisedImage:
    """Output of :meth:`ImageNormalizer.convert`."""

    bytes: bytes
    media_type: str
    page_count: int


@service
class ImageNormalizer:
    """Bytes in -> PNG bytes out."""

    def convert(
        self,
        data: bytes,
        *,
        media_type: str,
        filename: str | None = None,
    ) -> NormalisedImage:
        if media_type in {"image/heic", "image/heif", "image/avif"}:
            return self._heif_to_png(data, filename)
        if media_type == "image/tiff":
            return self._raster_to_png(data, filename, media_type="image/tiff")
        if media_type == "image/bmp":
            return self._raster_to_png(data, filename, media_type="image/bmp")
        if media_type == "image/svg+xml":
            return self._svg_to_png(data, filename)
        raise ImageConversionError(
            f"unsupported source media type for image normalisation: {media_type!r}",
            filename=filename,
        )

    # ------------------------------------------------------------------

    def _heif_to_png(self, data: bytes, filename: str | None) -> NormalisedImage:
        # ``pillow_heif`` registers itself as a Pillow plugin on
        # import, which is what lets ``self._pillow_open`` decode
        # HEIC / HEIF / AVIF below. The import is therefore
        # load-bearing despite looking unused.
        try:
            import pillow_heif  # noqa: F401
        except ImportError as exc:
            raise ImageConversionError(
                f"pillow-heif is required for HEIC/HEIF/AVIF decoding: {exc}",
                filename=filename,
            ) from exc
        return self._pillow_open(data, filename=filename)

    def _raster_to_png(
        self,
        data: bytes,
        filename: str | None,
        *,
        media_type: str,
    ) -> NormalisedImage:
        return self._pillow_open(data, filename=filename)

    def _svg_to_png(self, data: bytes, filename: str | None) -> NormalisedImage:
        try:
            import cairosvg
        except ImportError as exc:
            raise ImageConversionError(
                f"cairosvg is required for SVG rasterisation: {exc}",
                filename=filename,
            ) from exc
        try:
            png_bytes = cairosvg.svg2png(bytestring=data, output_width=2000)
        except Exception as exc:
            raise ImageConversionError(f"SVG rasterisation failed: {exc}", filename=filename) from exc
        return NormalisedImage(bytes=png_bytes, media_type="image/png", page_count=1)

    @staticmethod
    def _pillow_open(data: bytes, *, filename: str | None) -> NormalisedImage:
        try:
            from PIL import Image
        except ImportError as exc:
            raise ImageConversionError(
                f"Pillow is required for image normalisation: {exc}",
                filename=filename,
            ) from exc
        try:
            with Image.open(BytesIO(data)) as img:
                # Coerce to RGB(A) -- exotic palette modes don't round-trip
                # cleanly to PNG.
                if img.mode not in {"RGB", "RGBA", "L"}:
                    img = img.convert("RGB")
                # Multi-frame inputs (animated GIF, multi-page TIFF) get
                # collapsed to the first frame; full-page fan-out is
                # ArchiveUnpacker territory.
                n_frames = getattr(img, "n_frames", 1)
                if n_frames > 1:
                    img.seek(0)
                buf = BytesIO()
                img.save(buf, format="PNG", optimize=True)
                return NormalisedImage(bytes=buf.getvalue(), media_type="image/png", page_count=1)
        except Exception as exc:
            raise ImageConversionError(
                f"Pillow could not decode the image: {exc}",
                filename=filename,
            ) from exc
