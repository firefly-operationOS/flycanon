# Copyright 2026 Firefly Software Solutions Inc
"""Coverage for :class:`PdfGuard`.

The guard pre-flights every inbound PDF so MarkItDown only ever sees
a parseable, unencrypted document. We assert the two failure modes
map to their stable error codes and that the happy path returns the
page count.
"""

from __future__ import annotations

import io

import pytest
from pypdf import PdfWriter

from flycanon.core.services.binary.errors import CorruptPdfError, EncryptedPdfError
from flycanon.core.services.binary.pdf_guard import PdfGuard


def _make_pdf(*, encrypted: bool = False, pages: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    if encrypted:
        writer.encrypt("password")
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.fixture
def guard() -> PdfGuard:
    return PdfGuard()


class TestCheck:
    def test_returns_page_count_on_clean_pdf(self, guard: PdfGuard):
        assert guard.check(_make_pdf(pages=3)) == 3

    def test_rejects_encrypted_pdf(self, guard: PdfGuard):
        with pytest.raises(EncryptedPdfError) as excinfo:
            guard.check(_make_pdf(encrypted=True), filename="secret.pdf")
        assert excinfo.value.code == "encrypted_pdf"
        assert excinfo.value.http_status == 422
        assert excinfo.value.filename == "secret.pdf"

    def test_rejects_corrupt_pdf(self, guard: PdfGuard):
        with pytest.raises(CorruptPdfError) as excinfo:
            guard.check(b"not a pdf at all", filename="garbage.pdf")
        assert excinfo.value.code == "corrupt_pdf"
        assert excinfo.value.http_status == 422
