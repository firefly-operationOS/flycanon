# Copyright 2026 Firefly Software Solutions Inc
"""``BinaryNormalizer`` -- the entry point for binary normalisation.

Takes ``(bytes, declared_media_type, filename)`` and returns one or
more :class:`NormalizedArtifact` rows the loader pipeline can hand to
the appropriate :class:`SourceLoader`. Each artifact carries the
final byte payload + the canonical :class:`SourceKind` + the
ancestry chain so the audit trail can reconstruct the path back to
the inbound bundle when archives or emails are involved.

Recursion depth and total fan-out are bounded by
:class:`CanonSettings.binary_max_recursion_depth` and
:class:`CanonSettings.binary_max_expanded_files`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pyfly.container import service

from flycanon.config import CanonSettings
from flycanon.core.services.binary.archive_unpacker import ArchiveUnpacker
from flycanon.core.services.binary.email_unpacker import EmailUnpacker
from flycanon.core.services.binary.errors import (
    ArchiveExtractionError,
    BinaryFanoutCapExceeded,
    BinaryNormalizationError,
    BinaryTooLargeError,
    UnsupportedBinaryError,
)
from flycanon.core.services.binary.image_normalizer import ImageNormalizer
from flycanon.core.services.binary.office_converter import OfficeConverter
from flycanon.core.services.binary.pdf_guard import PdfGuard
from flycanon.core.services.binary.sniffer import sniff_media_type
from flycanon.interfaces.enums import SourceKind

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class NormalizedArtifact:
    """One loader-ready unit emitted by the normaliser.

    A single inbound PDF yields one artifact; a ZIP of N files yields
    N artifacts each carrying the ``derived_from`` ancestry tuple so
    the citation graph can trace back to the parent bundle.
    """

    bytes: bytes
    media_type: str
    kind: SourceKind
    filename: str
    derived_from: tuple[str, ...] = ()


# Media types that need image format conversion before they reach the
# OCR loader.
_IMAGE_CONVERT = {
    "image/heic",
    "image/heif",
    "image/avif",
    "image/tiff",
    "image/bmp",
    "image/svg+xml",
}
# Image media types the loader / OCR can handle without conversion.
_IMAGE_NATIVE = {"image/png", "image/jpeg", "image/gif", "image/webp"}
# Archive media types.
_ARCHIVES = {
    "application/zip",
    "application/x-7z-compressed",
    "application/x-tar",
    "application/gzip",
    "application/epub+zip",
}
# Email media types.
_EMAILS = {"message/rfc822", "application/vnd.ms-outlook"}
# Media-type -> SourceKind for the formats we keep as-is.
_KIND_OF: dict[str, SourceKind] = {
    "application/pdf": SourceKind.pdf,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": SourceKind.docx,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": SourceKind.xlsx,
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": SourceKind.pptx,
    "application/vnd.oasis.opendocument.text": SourceKind.odt,
    "application/vnd.oasis.opendocument.spreadsheet": SourceKind.ods,
    "application/vnd.oasis.opendocument.presentation": SourceKind.odp,
    "application/rtf": SourceKind.rtf,
    "application/epub+zip": SourceKind.epub,
    "text/html": SourceKind.html,
    "text/markdown": SourceKind.markdown,
    "text/plain": SourceKind.text,
    "text/csv": SourceKind.csv,
    "text/tab-separated-values": SourceKind.tsv,
    "application/json": SourceKind.json_,
    "application/xml": SourceKind.xml,
    "text/vtt": SourceKind.transcript,
    "application/x-subrip": SourceKind.transcript,
}


@service
class BinaryNormalizer:
    """Caller bytes -> a list of :class:`NormalizedArtifact`."""

    def __init__(
        self,
        *,
        settings: CanonSettings,
        pdf_guard: PdfGuard,
        image: ImageNormalizer,
        office: OfficeConverter,
        archive: ArchiveUnpacker,
        email_unpacker: EmailUnpacker,
    ) -> None:
        self._settings = settings
        self._pdf_guard = pdf_guard
        self._image = image
        self._office = office
        self._archive = archive
        self._email = email_unpacker

    async def normalise(
        self,
        data: bytes,
        *,
        declared_media_type: str | None = None,
        filename: str | None = None,
    ) -> list[NormalizedArtifact]:
        if not data:
            raise BinaryNormalizationError("inbound bytes are empty", filename=filename)
        if len(data) > self._settings.max_bytes:
            raise BinaryTooLargeError(
                f"binary exceeds the {self._settings.max_bytes}-byte cap",
                filename=filename,
            )
        if not self._settings.binary_normalize_enabled:
            media_type = sniff_media_type(data, default=declared_media_type, filename=filename)
            return [
                NormalizedArtifact(
                    bytes=data,
                    media_type=media_type,
                    kind=_kind_for(media_type),
                    filename=filename or "document",
                )
            ]

        out: list[NormalizedArtifact] = []
        await self._dispatch(
            data,
            declared_media_type=declared_media_type,
            filename=filename or "document",
            depth=0,
            ancestry=(),
            sink=out,
        )
        if not out:
            raise BinaryNormalizationError(
                "normalization produced no artifacts",
                filename=filename,
            )
        return out

    # ------------------------------------------------------------------

    async def _dispatch(
        self,
        data: bytes,
        *,
        declared_media_type: str | None,
        filename: str,
        depth: int,
        ancestry: tuple[str, ...],
        sink: list[NormalizedArtifact],
    ) -> None:
        if depth > self._settings.binary_max_recursion_depth:
            raise ArchiveExtractionError(
                f"binary nesting exceeds depth {self._settings.binary_max_recursion_depth}",
                filename=filename,
            )
        if len(sink) >= self._settings.binary_max_expanded_files:
            raise BinaryFanoutCapExceeded(
                f"binary expansion exceeded {self._settings.binary_max_expanded_files} files",
                filename=filename,
            )

        media_type = sniff_media_type(data, default=declared_media_type, filename=filename)

        # Archives -> fan out + recurse.
        if media_type in _ARCHIVES and self._archive.supports(media_type):
            for member_name, member_bytes in self._archive.unpack(
                data, media_type=media_type, filename=filename
            ):
                await self._dispatch(
                    member_bytes,
                    declared_media_type=None,
                    filename=member_name,
                    depth=depth + 1,
                    ancestry=(*ancestry, filename),
                    sink=sink,
                )
            return

        # Emails -> fan out + recurse.
        if media_type in _EMAILS and self._email.supports(media_type):
            for member_name, member_bytes in self._email.unpack(
                data, media_type=media_type, filename=filename
            ):
                await self._dispatch(
                    member_bytes,
                    declared_media_type=None,
                    filename=member_name,
                    depth=depth + 1,
                    ancestry=(*ancestry, filename),
                    sink=sink,
                )
            return

        # PDF -> integrity check + passthrough.
        if media_type == "application/pdf":
            self._pdf_guard.check(data, filename=filename)
            sink.append(
                NormalizedArtifact(
                    bytes=data,
                    media_type=media_type,
                    kind=SourceKind.pdf,
                    filename=filename,
                    derived_from=ancestry,
                )
            )
            return

        # Images that the OCR loader can read natively.
        if media_type in _IMAGE_NATIVE:
            sink.append(
                NormalizedArtifact(
                    bytes=data,
                    media_type=media_type,
                    kind=SourceKind.image,
                    filename=filename,
                    derived_from=ancestry,
                )
            )
            return

        # Images that need conversion to PNG before OCR.
        if media_type in _IMAGE_CONVERT:
            converted = self._image.convert(data, media_type=media_type, filename=filename)
            new_filename = _swap_extension(filename, "png")
            sink.append(
                NormalizedArtifact(
                    bytes=converted.bytes,
                    media_type=converted.media_type,
                    kind=SourceKind.image,
                    filename=new_filename,
                    derived_from=(*ancestry, filename),
                )
            )
            return

        # Office formats handled by the optional converter when enabled.
        if self._office.supports(media_type):
            converted = await self._office.convert(data, media_type=media_type, filename=filename)
            sink.append(
                NormalizedArtifact(
                    bytes=converted.bytes,
                    media_type=converted.media_type,
                    kind=SourceKind.pdf,
                    filename=_swap_extension(filename, "pdf"),
                    derived_from=(*ancestry, filename),
                )
            )
            return

        # Loader-handled formats (DOCX, XLSX, PPTX, HTML, MD, TXT, ...) ->
        # passthrough. The loader will pick the right converter
        # (MarkItDown or a dedicated loader) per kind.
        if media_type in _KIND_OF or media_type.startswith("text/"):
            kind = _kind_for(media_type)
            sink.append(
                NormalizedArtifact(
                    bytes=data,
                    media_type=media_type,
                    kind=kind,
                    filename=filename,
                    derived_from=ancestry,
                )
            )
            return

        # Out of the routing table -> typed 415.
        raise UnsupportedBinaryError(
            f"flycanon has no normaliser route for media type {media_type!r}",
            filename=filename,
        )


def _kind_for(media_type: str) -> SourceKind:
    return _KIND_OF.get(media_type, SourceKind.unknown)


def _swap_extension(filename: str, new_ext: str) -> str:
    if "." in filename:
        return f"{filename.rsplit('.', 1)[0]}.{new_ext}"
    return f"{filename}.{new_ext}"
