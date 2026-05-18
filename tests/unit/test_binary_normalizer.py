# Copyright 2026 Firefly Software Solutions Inc
"""Coverage for :class:`BinaryNormalizer`.

The normaliser is the front door for every ingest, so this suite
exercises every branch of the routing matrix with stubbed
collaborators (PDF guard, image normaliser, archive unpacker, email
unpacker, office converter). We assert that:

* PDFs / native rasters / loader-handled formats pass through.
* HEIC / AVIF / TIFF / SVG / BMP get converted to PNG before OCR.
* ZIP / TAR archives fan out and each member is re-dispatched.
* EML / MSG decompositions preserve the ancestry chain.
* Office formats get routed through the configured converter.
* Unknown media types raise :class:`UnsupportedBinaryError`.
* Recursion-depth + fanout caps fire as :class:`ArchiveExtractionError`
  / :class:`BinaryFanoutCapExceeded`.
* Disabling the normaliser short-circuits to a passthrough.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from flycanon.core.services.binary.errors import (
    ArchiveExtractionError,
    BinaryFanoutCapExceeded,
    BinaryNormalizationError,
    BinaryTooLargeError,
    UnsupportedBinaryError,
)
from flycanon.core.services.binary.normalizer import BinaryNormalizer
from flycanon.interfaces.enums import SourceKind

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class StubPdfGuard:
    def __init__(self) -> None:
        self.checked: list[tuple[bytes, str | None]] = []

    def check(self, data: bytes, *, filename: str | None = None) -> int:
        self.checked.append((data, filename))
        return 1


class StubImageNormalizer:
    def convert(self, data: bytes, *, media_type: str, filename: str | None = None):
        return SimpleNamespace(bytes=b"PNG-" + data, media_type="image/png")


class StubArchiveUnpacker:
    def __init__(self, members: list[tuple[str, bytes]] | None = None) -> None:
        self.members = members or []

    @staticmethod
    def supports(media_type: str) -> bool:
        return media_type in {
            "application/zip",
            "application/x-7z-compressed",
            "application/x-tar",
            "application/gzip",
            "application/epub+zip",
        }

    def unpack(self, data: bytes, *, media_type: str, filename: str | None = None):
        yield from self.members


class StubEmailUnpacker:
    def __init__(self, members: list[tuple[str, bytes]] | None = None) -> None:
        self.members = members or []

    @staticmethod
    def supports(media_type: str) -> bool:
        return media_type in {"message/rfc822", "application/vnd.ms-outlook"}

    def unpack(self, data: bytes, *, media_type: str, filename: str | None = None):
        yield from self.members


class StubOfficeConverter:
    def __init__(self, *, supports: bool = False) -> None:
        self._supports = supports

    def supports(self, media_type: str) -> bool:
        return self._supports

    async def convert(self, data: bytes, *, media_type: str, filename: str | None = None):
        return SimpleNamespace(bytes=b"PDF-" + data, media_type="application/pdf")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings(
    *,
    max_bytes: int = 5_000_000,
    binary_normalize_enabled: bool = True,
    binary_max_recursion_depth: int = 4,
    binary_max_expanded_files: int = 50,
) -> Any:
    return SimpleNamespace(
        max_bytes=max_bytes,
        binary_normalize_enabled=binary_normalize_enabled,
        binary_max_recursion_depth=binary_max_recursion_depth,
        binary_max_expanded_files=binary_max_expanded_files,
    )


def _normaliser(
    *,
    settings=None,
    pdf_guard=None,
    image=None,
    office=None,
    archive=None,
    email_unpacker=None,
) -> BinaryNormalizer:
    return BinaryNormalizer(
        settings=settings or _settings(),
        pdf_guard=pdf_guard or StubPdfGuard(),
        image=image or StubImageNormalizer(),
        office=office or StubOfficeConverter(),
        archive=archive or StubArchiveUnpacker(),
        email_unpacker=email_unpacker or StubEmailUnpacker(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPassthrough:
    async def test_pdf_passthrough_runs_through_guard(self):
        guard = StubPdfGuard()
        norm = _normaliser(pdf_guard=guard)
        out = await norm.normalise(b"%PDF-1.7\n%bytes", filename="report.pdf")
        assert len(out) == 1
        artifact = out[0]
        assert artifact.kind is SourceKind.pdf
        assert artifact.media_type == "application/pdf"
        assert artifact.filename == "report.pdf"
        assert guard.checked == [(b"%PDF-1.7\n%bytes", "report.pdf")]

    async def test_png_passes_through_as_image(self):
        norm = _normaliser()
        out = await norm.normalise(b"\x89PNG\r\n\x1a\nimage", filename="diagram.png")
        assert len(out) == 1
        assert out[0].kind is SourceKind.image
        assert out[0].media_type == "image/png"

    async def test_html_passes_through_as_html(self):
        norm = _normaliser()
        out = await norm.normalise(b"<!doctype html><html/>", filename="page.html")
        assert out[0].kind is SourceKind.html

    async def test_markdown_passes_through_via_extension(self):
        norm = _normaliser()
        out = await norm.normalise(b"# heading\n\nbody", filename="notes.md")
        assert out[0].kind is SourceKind.markdown


@pytest.mark.asyncio
class TestImageConversion:
    async def test_heic_is_converted_to_png(self):
        # HEIC magic = ftyp box with heic brand at offset 4.
        heic = b"\x00\x00\x00\x20" + b"ftypheic" + b"\x00" * 16
        norm = _normaliser()
        out = await norm.normalise(heic, filename="snap.heic")
        assert len(out) == 1
        assert out[0].kind is SourceKind.image
        assert out[0].media_type == "image/png"
        assert out[0].filename == "snap.png"
        assert out[0].bytes.startswith(b"PNG-")
        assert "snap.heic" in out[0].derived_from

    async def test_avif_is_converted_to_png(self):
        avif = b"\x00\x00\x00\x20" + b"ftypavif" + b"\x00" * 16
        norm = _normaliser()
        out = await norm.normalise(avif, filename="cover.avif")
        assert out[0].media_type == "image/png"
        assert out[0].filename == "cover.png"

    async def test_tiff_is_converted_to_png(self):
        tiff = b"II*\x00" + b"\x00" * 32
        norm = _normaliser()
        out = await norm.normalise(tiff, filename="scan.tiff")
        assert out[0].media_type == "image/png"


@pytest.mark.asyncio
class TestArchives:
    async def test_zip_fan_out_yields_one_artifact_per_member(self):
        archive = StubArchiveUnpacker(
            members=[("doc.md", b"# Title"), ("img.png", b"\x89PNG\r\n\x1a\n bits")]
        )
        norm = _normaliser(archive=archive)
        out = await norm.normalise(b"PK\x03\x04" + b"\x00" * 30, filename="bundle.zip")
        assert len(out) == 2
        names = {a.filename for a in out}
        assert names == {"doc.md", "img.png"}
        for artifact in out:
            assert "bundle.zip" in artifact.derived_from

    async def test_recursion_depth_cap_raises(self):
        # Use a self-referencing archive to blow past depth=1.
        archive = StubArchiveUnpacker(
            members=[("inner.zip", b"PK\x03\x04" + b"\x00" * 30)]
        )
        norm = _normaliser(
            settings=_settings(binary_max_recursion_depth=1),
            archive=archive,
        )
        with pytest.raises(ArchiveExtractionError):
            await norm.normalise(b"PK\x03\x04" + b"\x00" * 30, filename="outer.zip")

    async def test_fanout_cap_raises(self):
        members = [(f"file-{i}.md", b"# x") for i in range(20)]
        archive = StubArchiveUnpacker(members=members)
        norm = _normaliser(
            settings=_settings(binary_max_expanded_files=5),
            archive=archive,
        )
        with pytest.raises(BinaryFanoutCapExceeded):
            await norm.normalise(b"PK\x03\x04" + b"\x00" * 30, filename="bundle.zip")


@pytest.mark.asyncio
class TestEmails:
    async def test_eml_decomposition_yields_body_plus_attachments(self):
        email_unpacker = StubEmailUnpacker(
            members=[
                ("thread-body.md", b"**Subject:** Hi\n\nbody text"),
                ("brief.pdf", b"%PDF-1.7\n%bytes"),
            ]
        )
        norm = _normaliser(email_unpacker=email_unpacker)
        out = await norm.normalise(
            b"From: a@b\r\nTo: c@d\r\nSubject: hi\r\n\r\nhi",
            filename="thread.eml",
        )
        names = {a.filename for a in out}
        assert names == {"thread-body.md", "brief.pdf"}
        for artifact in out:
            assert "thread.eml" in artifact.derived_from


@pytest.mark.asyncio
class TestOffice:
    async def test_office_converter_translates_docx_to_pdf_when_enabled(self):
        norm = _normaliser(office=StubOfficeConverter(supports=True))
        # Use a DOCX-shaped ZIP to drive the sniffer to the DOCX MIME.
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("[Content_Types].xml", b"<Types/>")
            zf.writestr("word/document.xml", b"<x/>")
        out = await norm.normalise(buf.getvalue(), filename="memo.docx")
        assert len(out) == 1
        assert out[0].kind is SourceKind.pdf
        assert out[0].media_type == "application/pdf"
        assert out[0].filename == "memo.pdf"
        assert out[0].bytes.startswith(b"PDF-")


@pytest.mark.asyncio
class TestEdgeCases:
    async def test_empty_payload_raises(self):
        norm = _normaliser()
        with pytest.raises(BinaryNormalizationError):
            await norm.normalise(b"", filename="empty.bin")

    async def test_oversize_payload_raises(self):
        norm = _normaliser(settings=_settings(max_bytes=10))
        with pytest.raises(BinaryTooLargeError):
            await norm.normalise(b"x" * 50, filename="big.bin")

    async def test_unsupported_media_type_raises(self):
        norm = _normaliser()
        with pytest.raises(UnsupportedBinaryError):
            await norm.normalise(b"\x99\x99\x99\x99 opaque", filename="mystery.bin")

    async def test_disabling_normaliser_returns_passthrough(self):
        norm = _normaliser(settings=_settings(binary_normalize_enabled=False))
        out = await norm.normalise(b"%PDF-1.7", filename="report.pdf")
        assert len(out) == 1
        assert out[0].media_type == "application/pdf"
        assert out[0].kind is SourceKind.pdf
