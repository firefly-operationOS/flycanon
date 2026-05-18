# Copyright 2026 Firefly Software Solutions Inc
"""Office conversion -- DOC / DOCX / XLS / XLSX / PPT / PPTX / RTF / ODT / ODS / ODP / HTML -> PDF.

Two implementations live behind the :class:`OfficeConverter`
protocol:

* :class:`GotenbergConverter` -- HTTP client against a Gotenberg
  sidecar. Distroless-friendly: no ``soffice`` binary required in
  the runtime image. Default selection.
* :class:`LibreOfficeConverter` -- in-container subprocess wrapper.
  Slower, fatter image, but useful for slim/dev variants that
  bundle ``soffice``.

The converter is **optional**. Most flycanon deployments rely on
MarkItDown for Office formats (pure-Python, in-process). The
converter is only wired when ``FLYCANON_OFFICE_CONVERTER`` is set to
something other than ``none``; the routing in
:class:`BinaryNormalizer` skips the converter for the formats
MarkItDown can already handle.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from flycanon.config import CanonSettings
from flycanon.core.services.binary.errors import OfficeConversionError

logger = logging.getLogger(__name__)


_DEFAULT_SUPPORTED = {
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/rtf",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.presentation",
    "text/html",
}


@dataclass(slots=True, frozen=True)
class ConvertedOffice:
    bytes: bytes
    media_type: str  # always ``application/pdf`` for the supported converters


class OfficeConverter(Protocol):
    """Bytes in, PDF bytes out."""

    @staticmethod
    def supports(media_type: str) -> bool: ...

    async def convert(
        self,
        data: bytes,
        *,
        media_type: str,
        filename: str | None = None,
    ) -> ConvertedOffice: ...


def _supports(media_type: str) -> bool:
    return media_type in _DEFAULT_SUPPORTED


class GotenbergConverter:
    """POST the bytes to a Gotenberg sidecar; receive a PDF back.

    Not autoscanned -- :class:`CanonCoreConfiguration` registers the
    concrete bean against the :class:`OfficeConverter` protocol so
    the routing in :class:`BinaryNormalizer` is provider-agnostic.
    """

    def __init__(self, *, settings: CanonSettings) -> None:
        self._settings = settings

    @staticmethod
    def supports(media_type: str) -> bool:
        return _supports(media_type)

    async def convert(
        self,
        data: bytes,
        *,
        media_type: str,
        filename: str | None = None,
    ) -> ConvertedOffice:
        if not _supports(media_type):
            raise OfficeConversionError(
                f"unsupported office media type {media_type!r}",
                filename=filename,
            )
        url = self._settings.gotenberg_url.rstrip("/") + "/forms/libreoffice/convert"
        timeout = self._settings.gotenberg_timeout_s
        files = {
            "files": (filename or "document", data, media_type),
        }
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, files=files)
        except (httpx.NetworkError, httpx.TimeoutException) as exc:
            raise OfficeConversionError(
                f"Gotenberg request failed: {exc}",
                filename=filename,
            ) from exc
        if response.status_code >= 300:
            raise OfficeConversionError(
                f"Gotenberg returned {response.status_code}: {response.text[:200]}",
                filename=filename,
            )
        return ConvertedOffice(bytes=response.content, media_type="application/pdf")


class LibreOfficeConverter:
    """Subprocess wrapper around ``soffice --headless``.

    Used when the runtime image bundles LibreOffice (slim/dev
    variants). Not autoscanned -- registered against the
    :class:`OfficeConverter` protocol in the core configuration.
    """

    def __init__(self, *, settings: CanonSettings) -> None:
        self._settings = settings

    @staticmethod
    def supports(media_type: str) -> bool:
        return _supports(media_type)

    async def convert(
        self,
        data: bytes,
        *,
        media_type: str,
        filename: str | None = None,
    ) -> ConvertedOffice:
        if not _supports(media_type):
            raise OfficeConversionError(
                f"unsupported office media type {media_type!r}",
                filename=filename,
            )
        binary = self._settings.binary_libreoffice_path
        if shutil.which(binary) is None:
            raise OfficeConversionError(
                f"LibreOffice binary {binary!r} not found on PATH",
                filename=filename,
            )
        timeout = self._settings.binary_libreoffice_timeout_s

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._convert_sync, data, filename, binary, timeout)

    @staticmethod
    def _convert_sync(
        data: bytes,
        filename: str | None,
        binary: str,
        timeout: int,
    ) -> ConvertedOffice:
        import subprocess

        with tempfile.TemporaryDirectory() as workdir:
            in_path = Path(workdir) / (filename or "document.bin")
            in_path.write_bytes(data)
            cmd = [
                binary,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                workdir,
                str(in_path),
            ]
            try:
                completed = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise OfficeConversionError(
                    f"LibreOffice timed out after {timeout}s",
                    filename=filename,
                ) from exc
            if completed.returncode != 0:
                stderr_tail = completed.stderr.decode("utf-8", "replace")[:200]
                raise OfficeConversionError(
                    f"LibreOffice exited {completed.returncode}: {stderr_tail}",
                    filename=filename,
                )
            pdfs = list(Path(workdir).glob("*.pdf"))
            if not pdfs:
                raise OfficeConversionError(
                    "LibreOffice produced no PDF output",
                    filename=filename,
                )
            return ConvertedOffice(bytes=pdfs[0].read_bytes(), media_type="application/pdf")


class NoOpOfficeConverter:
    """Default converter when none is configured.

    ``supports`` returns False unconditionally; the
    :class:`BinaryNormalizer` falls back to the MarkItDown loader
    for the formats it can handle in-process.
    """

    @staticmethod
    def supports(media_type: str) -> bool:
        return False

    async def convert(
        self,
        data: bytes,
        *,
        media_type: str,
        filename: str | None = None,
    ) -> ConvertedOffice:
        raise OfficeConversionError(
            "office converter is disabled (set FLYCANON_OFFICE_CONVERTER to enable)",
            filename=filename,
        )
