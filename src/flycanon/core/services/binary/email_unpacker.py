# Copyright 2026 Firefly Software Solutions Inc
"""``EmailUnpacker`` -- iterate the body + attachments of EML / MSG.

Yields ``(filename, bytes)`` pairs. The body lands as a Markdown
artifact named ``email-<message-id>.md`` so the loader pipeline can
treat it like any other text source; attachments come out as their
declared filename + bytes.

EML parsing is stdlib (``email.parser.BytesParser``); MSG parsing
uses ``extract-msg`` which is already a project dependency.
"""

from __future__ import annotations

import email
import email.parser
import email.policy
import logging
from collections.abc import Iterator

from pyfly.container import service

from flycanon.core.services.binary.errors import EmailParseError

logger = logging.getLogger(__name__)


_SUPPORTED = {"message/rfc822", "application/vnd.ms-outlook"}


@service
class EmailUnpacker:
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
        if media_type == "message/rfc822":
            yield from self._iter_eml(data, filename)
        elif media_type == "application/vnd.ms-outlook":
            yield from self._iter_msg(data, filename)
        else:
            raise EmailParseError(
                f"unsupported email media type {media_type!r}",
                filename=filename,
            )

    # ------------------------------------------------------------------

    def _iter_eml(self, data: bytes, filename: str | None) -> Iterator[tuple[str, bytes]]:
        try:
            msg = email.parser.BytesParser(policy=email.policy.default).parsebytes(data)
        except Exception as exc:
            raise EmailParseError(f"could not parse .eml: {exc}", filename=filename) from exc

        body_markdown = _render_eml_header(msg)
        text_parts: list[str] = []
        attachments: list[tuple[str, bytes]] = []

        for part in msg.walk():
            if part.is_multipart():
                continue
            disposition = (part.get_content_disposition() or "").lower()
            content_type = part.get_content_type()
            raw_payload = part.get_payload(decode=True) or b""
            payload: bytes = raw_payload if isinstance(raw_payload, bytes) else b""
            if disposition == "attachment" or part.get_filename():
                name = part.get_filename() or _default_name(content_type)
                if payload:
                    attachments.append((name, payload))
                continue
            if content_type == "text/plain":
                try:
                    text_parts.append(payload.decode(part.get_content_charset() or "utf-8", "replace"))
                except LookupError:
                    text_parts.append(payload.decode("utf-8", "replace"))
            elif content_type == "text/html":
                # Surface raw HTML; the loader pipeline knows what to do.
                attachments.append((_default_name("text/html"), payload))
        body_markdown += "\n\n" + "\n\n".join(text_parts) if text_parts else body_markdown
        yield _body_filename(filename), body_markdown.encode("utf-8")
        yield from attachments

    def _iter_msg(self, data: bytes, filename: str | None) -> Iterator[tuple[str, bytes]]:
        try:
            import extract_msg
        except ImportError as exc:
            raise EmailParseError(
                f"extract-msg is required for Outlook .msg parsing: {exc}",
                filename=filename,
            ) from exc
        try:
            msg = extract_msg.openMsg(data)
        except Exception as exc:
            raise EmailParseError(f"could not parse .msg: {exc}", filename=filename) from exc

        header = "\n".join(
            f"**{label}:** {value}"
            for label, value in (
                ("Subject", getattr(msg, "subject", "")),
                ("From", getattr(msg, "sender", "")),
                ("To", getattr(msg, "to", "")),
                ("Date", str(getattr(msg, "date", "") or "")),
            )
            if value
        )
        body = (getattr(msg, "body", "") or "").strip()
        markdown = f"{header}\n\n{body}".strip()
        yield _body_filename(filename), markdown.encode("utf-8")
        for attachment in getattr(msg, "attachments", []) or []:
            name = (
                getattr(attachment, "longFilename", None)
                or getattr(attachment, "shortFilename", None)
                or "attachment.bin"
            )
            payload = getattr(attachment, "data", None)
            if payload:
                yield name, payload


def _render_eml_header(msg) -> str:
    lines = []
    for label in ("Subject", "From", "To", "Cc", "Date", "Message-ID"):
        value = msg.get(label, "")
        if value:
            lines.append(f"**{label}:** {value}")
    return "\n".join(lines)


def _body_filename(filename: str | None) -> str:
    base = (filename or "email").rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0] or "email"
    return f"{stem}-body.md"


def _default_name(content_type: str) -> str:
    return {
        "text/html": "body.html",
        "text/plain": "body.txt",
        "application/pdf": "attachment.pdf",
    }.get(content_type, "attachment.bin")
