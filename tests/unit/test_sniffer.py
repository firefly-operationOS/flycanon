# Copyright 2026 Firefly Software Solutions Inc
"""Coverage for the magic-byte sniffer.

The sniffer is the routing key for the entire binary normaliser, so
we exercise every branch: pure-magic detections (PDF, PNG, JPEG,
TIFF, ZIP, 7Z, gzip, RTF), ZIP-disambiguation for OOXML and EPUB,
HEIF / AVIF brand peek, OLE compound disambiguation via the filename
hint, HTML / SVG / XML / EML text heuristics, and the filename-only
fallback path.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from flycanon.core.services.binary.sniffer import sniff_media_type


def _ooxml_zip(folder: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", b"<Types/>")
        zf.writestr(f"{folder}/document.xml", b"<x/>")
    return buf.getvalue()


def _odf_zip(mimetype: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", mimetype.encode("ascii"))
    return buf.getvalue()


class TestUnambiguousMagic:
    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            (b"%PDF-1.7\n%binary stuff", "application/pdf"),
            (b"\x89PNG\r\n\x1a\nimage bytes", "image/png"),
            (b"\xff\xd8\xff\xe0\x00", "image/jpeg"),
            (b"GIF87a...", "image/gif"),
            (b"GIF89a...", "image/gif"),
            (b"II*\x00" + b"\x00" * 20, "image/tiff"),
            (b"MM\x00*" + b"\x00" * 20, "image/tiff"),
            (b"BM" + b"\x00" * 32, "image/bmp"),
            (b"PK\x05\x06" + b"\x00" * 20, "application/zip"),
            (b"7z\xbc\xaf\x27\x1c" + b"\x00" * 4, "application/x-7z-compressed"),
            (b"\x1f\x8b" + b"\x00" * 6, "application/gzip"),
            (b"{\\rtf1 hello}", "application/rtf"),
        ],
    )
    def test_magic_bytes_route_to_expected_mime(self, data: bytes, expected: str):
        assert sniff_media_type(data) == expected

    def test_webp_riff_envelope_recognised(self):
        data = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 32
        assert sniff_media_type(data) == "image/webp"

    def test_tar_ustar_signature_at_offset_257(self):
        # tar.h: ustar magic lives at byte 257 of the 512-byte header.
        header = bytearray(512)
        header[257:262] = b"ustar"
        assert sniff_media_type(bytes(header)) == "application/x-tar"


class TestHeifFamily:
    @pytest.mark.parametrize(
        ("brand", "expected"),
        [
            (b"avif", "image/avif"),
            (b"heic", "image/heic"),
            (b"heix", "image/heic"),
            (b"mif1", "image/heic"),
        ],
    )
    def test_ftyp_brand_routes_heif_family(self, brand: bytes, expected: str):
        data = b"\x00\x00\x00\x20" + b"ftyp" + brand + b"\x00" * 16
        assert sniff_media_type(data) == expected


class TestZipDisambiguation:
    def test_docx_detected_via_word_folder(self):
        assert sniff_media_type(_ooxml_zip("word")) == (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

    def test_xlsx_detected_via_xl_folder(self):
        assert sniff_media_type(_ooxml_zip("xl")) == (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    def test_pptx_detected_via_ppt_folder(self):
        assert sniff_media_type(_ooxml_zip("ppt")) == (
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        )

    def test_odt_detected_via_mimetype_member(self):
        assert (
            sniff_media_type(_odf_zip("application/vnd.oasis.opendocument.text"))
            == "application/vnd.oasis.opendocument.text"
        )

    def test_epub_detected_via_mimetype_member(self):
        assert sniff_media_type(_odf_zip("application/epub+zip")) == "application/epub+zip"

    def test_plain_zip_falls_back_when_central_dir_has_no_hints(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("notes.txt", b"hello")
        assert sniff_media_type(buf.getvalue()) == "application/zip"


class TestOleCompound:
    def test_doc_resolved_via_filename_hint(self):
        ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32
        assert sniff_media_type(ole, filename="legacy.doc") == "application/msword"

    def test_msg_resolved_via_filename_hint(self):
        ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32
        assert sniff_media_type(ole, filename="thread.msg") == "application/vnd.ms-outlook"

    def test_unknown_ole_falls_back_to_cfb_umbrella(self):
        ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32
        assert sniff_media_type(ole, filename="unknown.bin") == "application/x-cfb"


class TestTextHeuristics:
    def test_html_doctype_routes_to_text_html(self):
        assert sniff_media_type(b"<!DOCTYPE html><html><body>...") == "text/html"
        assert sniff_media_type(b"<html><body>x</body></html>") == "text/html"

    def test_svg_is_recognised_via_root_or_xml_prologue(self):
        assert sniff_media_type(b'<svg xmlns="..."/>') == "image/svg+xml"
        assert sniff_media_type(b'<?xml version="1.0"?>\n<svg xmlns="..."/>') == "image/svg+xml"

    def test_xml_without_svg_root_is_application_xml(self):
        assert sniff_media_type(b'<?xml version="1.0"?>\n<root><a/></root>') == "application/xml"

    def test_eml_header_is_recognised(self):
        eml = b"From: a@b\r\nTo: c@d\r\nSubject: x\r\n\r\nhi"
        assert sniff_media_type(eml) == "message/rfc822"


class TestFallbacks:
    def test_extension_hint_drives_when_bytes_are_opaque(self):
        # Random bytes plus a .csv filename should resolve to text/csv.
        opaque = b"\x00\x01\x02\x03\x04opaque content"
        assert sniff_media_type(opaque, filename="report.csv") == "text/csv"

    def test_extension_map_handles_modern_office_extensions(self):
        opaque = b"\x00" * 32
        assert (
            sniff_media_type(opaque, filename="x.docx")
            == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

    def test_empty_payload_returns_default(self):
        assert sniff_media_type(b"", default="text/markdown") == "text/markdown"
        assert sniff_media_type(b"") == "application/octet-stream"

    def test_default_is_normalised_and_lowercased(self):
        opaque = b"\x00\x01\x02\x03"
        assert sniff_media_type(opaque, default="Text/Plain; charset=UTF-8") == "text/plain"

    def test_unknown_resolves_to_octet_stream(self):
        assert sniff_media_type(b"\x99\x99\x99\x99") == "application/octet-stream"
