# Copyright 2026 Firefly Software Solutions Inc
"""``ArchiveUnpacker`` -- iterate the members of ZIP / 7Z / TAR / GZ.

Yields ``(filename, bytes)`` pairs. The :class:`BinaryNormalizer`
recurses on each member; the depth + count caps from
:class:`CanonSettings` prevent zip-bomb style fan-out.
"""

from __future__ import annotations

import gzip
import io
import logging
import tarfile
import zipfile
from collections.abc import Iterator

from pyfly.container import service

from flycanon.core.services.binary.errors import ArchiveExtractionError

logger = logging.getLogger(__name__)


_SUPPORTED = {
    "application/zip",
    "application/x-7z-compressed",
    "application/x-tar",
    "application/gzip",
    "application/epub+zip",
}


@service
class ArchiveUnpacker:
    """Stateless adapter -- safe as a singleton."""

    @staticmethod
    def supports(media_type: str) -> bool:
        return media_type in _SUPPORTED

    def unpack(
        self,
        data: bytes,
        *,
        media_type: str,
        filename: str | None = None,
    ) -> Iterator[tuple[str, bytes]]:
        """Yield each member as ``(member_filename, member_bytes)``.

        Raises :class:`ArchiveExtractionError` on corrupt archives or
        unsupported encryption.
        """
        if media_type in {"application/zip", "application/epub+zip"}:
            yield from self._iter_zip(data, filename)
        elif media_type == "application/x-7z-compressed":
            yield from self._iter_7z(data, filename)
        elif media_type == "application/x-tar":
            yield from self._iter_tar(data, filename)
        elif media_type == "application/gzip":
            yield from self._iter_gz(data, filename)
        else:
            raise ArchiveExtractionError(
                f"unsupported archive media type {media_type!r}",
                filename=filename,
            )

    # ------------------------------------------------------------------

    def _iter_zip(self, data: bytes, filename: str | None) -> Iterator[tuple[str, bytes]]:
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise ArchiveExtractionError(f"corrupt zip: {exc}", filename=filename) from exc
        try:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if info.flag_bits & 0x1:
                    raise ArchiveExtractionError(
                        "encrypted zip entries are not supported",
                        filename=filename,
                    )
                try:
                    yield info.filename, zf.read(info)
                except (zipfile.BadZipFile, RuntimeError) as exc:
                    raise ArchiveExtractionError(
                        f"could not read zip entry {info.filename!r}: {exc}",
                        filename=filename,
                    ) from exc
        finally:
            zf.close()

    def _iter_7z(self, data: bytes, filename: str | None) -> Iterator[tuple[str, bytes]]:
        try:
            import py7zr
        except ImportError as exc:
            raise ArchiveExtractionError(
                f"py7zr is required to extract 7z archives: {exc}",
                filename=filename,
            ) from exc
        try:
            with py7zr.SevenZipFile(io.BytesIO(data), mode="r") as sz:
                if sz.password_protected:
                    raise ArchiveExtractionError(
                        "password-protected 7z archives are not supported",
                        filename=filename,
                    )
                for name, bio in sz.readall().items():  # type: ignore[attr-defined]
                    if not bio:
                        continue
                    bio.seek(0)
                    yield name, bio.read()
        except ArchiveExtractionError:
            raise
        except Exception as exc:
            raise ArchiveExtractionError(f"corrupt 7z archive: {exc}", filename=filename) from exc

    def _iter_tar(self, data: bytes, filename: str | None) -> Iterator[tuple[str, bytes]]:
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
                for member in tf.getmembers():
                    if not member.isfile():
                        continue
                    handle = tf.extractfile(member)
                    if handle is None:
                        continue
                    payload = handle.read()
                    if payload:
                        yield member.name, payload
        except tarfile.TarError as exc:
            raise ArchiveExtractionError(f"corrupt tar: {exc}", filename=filename) from exc

    def _iter_gz(self, data: bytes, filename: str | None) -> Iterator[tuple[str, bytes]]:
        try:
            decompressed = gzip.decompress(data)
        except (OSError, EOFError) as exc:
            raise ArchiveExtractionError(f"corrupt gzip: {exc}", filename=filename) from exc
        # A gzip stream is single-member by definition. Recover the
        # inner filename when present; fall back to the archive name
        # without the ``.gz`` suffix.
        inner_name = (filename or "payload").removesuffix(".gz").removesuffix(".tgz")
        yield inner_name, decompressed
