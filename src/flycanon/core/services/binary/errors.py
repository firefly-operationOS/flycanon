# Copyright 2026 Firefly Software Solutions Inc
"""Typed exceptions raised by the binary-normalisation stage.

Each subclass carries a stable ``code`` (mapped to ``ProblemDetails.code``
by the controller advice) and a default ``http_status`` -- the
adapter throws with the right HTTP status without the controller having
to translate.
"""

from __future__ import annotations


class BinaryNormalizationError(Exception):
    """Base class for every binary-normalisation failure."""

    code: str = "binary_normalization_failed"
    http_status: int = 422

    def __init__(self, message: str, *, filename: str | None = None) -> None:
        super().__init__(message)
        self.filename = filename


class UnsupportedBinaryError(BinaryNormalizationError):
    code = "unsupported_binary"
    http_status = 415


class EncryptedPdfError(BinaryNormalizationError):
    code = "encrypted_pdf"
    http_status = 422


class CorruptPdfError(BinaryNormalizationError):
    code = "corrupt_pdf"
    http_status = 422


class ImageConversionError(BinaryNormalizationError):
    code = "image_conversion_failed"
    http_status = 422


class ArchiveExtractionError(BinaryNormalizationError):
    code = "archive_extraction_failed"
    http_status = 422


class OfficeConversionError(BinaryNormalizationError):
    code = "office_conversion_failed"
    http_status = 422


class EmailParseError(BinaryNormalizationError):
    code = "email_parse_failed"
    http_status = 422


class BinaryTooLargeError(BinaryNormalizationError):
    code = "binary_too_large"
    http_status = 413


class BinaryFanoutCapExceeded(BinaryNormalizationError):
    code = "binary_fanout_cap_exceeded"
    http_status = 422
