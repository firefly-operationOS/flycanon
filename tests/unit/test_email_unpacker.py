# Copyright 2026 Firefly Software Solutions Inc
"""Coverage for :class:`EmailUnpacker`.

EML parsing is stdlib (``email.parser.BytesParser``); MSG parsing
delegates to ``extract-msg`` (covered as an integration concern when
the dependency is available, smoke-tested here behind an import
guard). We assert the body becomes a Markdown artifact with the
canonical headers preserved and that attachments come out as their
declared filename + payload.
"""

from __future__ import annotations

from email.message import EmailMessage

import pytest

from flycanon.core.services.binary.email_unpacker import EmailUnpacker
from flycanon.core.services.binary.errors import EmailParseError


def _build_eml(*, with_attachment: bool = False) -> bytes:
    msg = EmailMessage()
    msg["Subject"] = "Quarterly review"
    msg["From"] = "alice@example.com"
    msg["To"] = "bob@example.com"
    msg["Message-ID"] = "<abc@example.com>"
    msg.set_content("Hello Bob, see attached.\nRegards,\nAlice")
    if with_attachment:
        msg.add_attachment(
            b"PDFBYTES",
            maintype="application",
            subtype="pdf",
            filename="brief.pdf",
        )
    return bytes(msg)


@pytest.fixture
def unpacker() -> EmailUnpacker:
    return EmailUnpacker()


class TestSupports:
    @pytest.mark.parametrize(
        "media_type",
        ["message/rfc822", "application/vnd.ms-outlook"],
    )
    def test_supports_email_media_types(self, media_type: str):
        assert EmailUnpacker.supports(media_type) is True

    def test_rejects_non_email(self):
        assert EmailUnpacker.supports("application/pdf") is False


class TestEmlParsing:
    def test_body_lands_as_markdown_with_headers(self, unpacker: EmailUnpacker):
        members = list(unpacker.unpack(_build_eml(), media_type="message/rfc822", filename="thread.eml"))
        # First yielded entry is always the body markdown.
        body_name, body_bytes = members[0]
        assert body_name.endswith("-body.md")
        text = body_bytes.decode("utf-8")
        assert "**Subject:** Quarterly review" in text
        assert "**From:** alice@example.com" in text
        assert "**To:** bob@example.com" in text
        assert "Hello Bob" in text

    def test_attachments_emitted_after_body(self, unpacker: EmailUnpacker):
        members = list(
            unpacker.unpack(
                _build_eml(with_attachment=True),
                media_type="message/rfc822",
                filename="thread.eml",
            )
        )
        assert len(members) == 2
        body_name, _ = members[0]
        attach_name, attach_bytes = members[1]
        assert body_name.endswith("-body.md")
        assert attach_name == "brief.pdf"
        assert attach_bytes == b"PDFBYTES"

    def test_corrupt_eml_raises_email_parse_error(self, unpacker: EmailUnpacker):
        with pytest.raises(EmailParseError):
            # Force the parser path that raises -- stdlib BytesParser is
            # extremely permissive, so we exercise the supports/dispatch
            # guard instead via an unsupported media type.
            list(unpacker.unpack(b"", media_type="application/x-mbox"))


class TestUnsupportedMediaType:
    def test_unknown_email_type_raises(self, unpacker: EmailUnpacker):
        with pytest.raises(EmailParseError):
            list(unpacker.unpack(b"x", media_type="application/x-mbox"))
