# Copyright 2026 Firefly Software Solutions Inc
"""``PdfGuard`` -- pre-flight integrity check on inbound PDFs.

Rejects:

* encrypted PDFs        -> :class:`EncryptedPdfError` (422 ``encrypted_pdf``)
* corrupt / truncated PDFs -> :class:`CorruptPdfError` (422 ``corrupt_pdf``)

The guard uses ``pypdf`` because it is already a project dependency
(also used by the :class:`PdfLoader`); no extra wheel to ship.
"""

from __future__ import annotations

import logging
from io import BytesIO

from pyfly.container import service
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from flycanon.core.services.binary.errors import CorruptPdfError, EncryptedPdfError

logger = logging.getLogger(__name__)


@service
class PdfGuard:
    """Stateless guard; safe to use as a singleton bean."""

    def check(self, data: bytes, *, filename: str | None = None) -> int:
        """Return the page count, or raise on encrypted / corrupt PDFs."""
        try:
            reader = PdfReader(BytesIO(data))
        except PdfReadError as exc:
            raise CorruptPdfError(f"could not parse PDF: {exc}", filename=filename) from exc
        if reader.is_encrypted:
            raise EncryptedPdfError(
                "encrypted PDFs are not supported by the canon intake",
                filename=filename,
            )
        try:
            return len(reader.pages)
        except Exception as exc:
            raise CorruptPdfError(
                f"could not enumerate PDF pages: {exc}",
                filename=filename,
            ) from exc
