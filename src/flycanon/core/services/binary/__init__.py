# Copyright 2026 Firefly Software Solutions Inc
"""Binary normalisation -- caller bytes in, loader-ready artifacts out.

The normaliser is the only thing in flycanon that touches raw caller
bytes directly. Its job is to:

1. Sniff the actual media type from magic bytes (don't trust the
   declared Content-Type or the filename extension on their own).
2. Route the bytes to the right adapter:
   * **PDF** -> :class:`PdfGuard` integrity check + passthrough.
   * **LLM/loader-native rasters** (PNG / JPEG / GIF / WebP) ->
     passthrough.
   * **Convertible images** (HEIC / HEIF / AVIF / TIFF / BMP / SVG) ->
     :class:`ImageNormalizer` produces PNG.
   * **Office / structured docs** (DOCX / XLSX / PPTX / RTF / ODT /
     HTML / CSV / EPUB / JSON / XML) -> handed to the loader as is;
     MarkItDown will convert downstream.
   * **Archives** (ZIP / 7Z / TAR / GZ) ->
     :class:`ArchiveUnpacker`, recurse on each member.
   * **Emails** (EML / MSG) -> :class:`EmailUnpacker`, recurse on
     body + each attachment.
3. Fan out into a list of :class:`NormalizedArtifact`. A single
   inbound PDF yields one artifact; a ZIP with N PDFs yields N
   artifacts, each carrying its ``derived_from`` ancestry so the
   audit trail can reconstruct the path back to the inbound bundle.

Recursion depth and total fan-out are bounded by
:class:`CanonSettings.binary_max_recursion_depth` and
:class:`CanonSettings.binary_max_expanded_files` so a malicious
zip-bomb never blows up the worker.
"""

from __future__ import annotations

from flycanon.core.services.binary.archive_unpacker import ArchiveUnpacker
from flycanon.core.services.binary.email_unpacker import EmailUnpacker
from flycanon.core.services.binary.errors import (
    ArchiveExtractionError,
    BinaryNormalizationError,
    EncryptedPdfError,
    ImageConversionError,
    OfficeConversionError,
    UnsupportedBinaryError,
)
from flycanon.core.services.binary.image_normalizer import ImageNormalizer, NormalisedImage
from flycanon.core.services.binary.normalizer import (
    BinaryNormalizer,
    NormalizedArtifact,
)
from flycanon.core.services.binary.office_converter import (
    GotenbergConverter,
    LibreOfficeConverter,
    OfficeConverter,
)
from flycanon.core.services.binary.pdf_guard import PdfGuard
from flycanon.core.services.binary.sniffer import sniff_media_type

__all__ = [
    "ArchiveExtractionError",
    "ArchiveUnpacker",
    "BinaryNormalizationError",
    "BinaryNormalizer",
    "EmailUnpacker",
    "EncryptedPdfError",
    "GotenbergConverter",
    "ImageConversionError",
    "ImageNormalizer",
    "LibreOfficeConverter",
    "NormalisedImage",
    "NormalizedArtifact",
    "OfficeConverter",
    "OfficeConversionError",
    "PdfGuard",
    "UnsupportedBinaryError",
    "sniff_media_type",
]
