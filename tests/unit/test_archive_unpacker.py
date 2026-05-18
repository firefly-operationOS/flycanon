# Copyright 2026 Firefly Software Solutions Inc
"""Coverage for :class:`ArchiveUnpacker`.

The unpacker is one of the highest-risk surfaces in the binary
normaliser: corrupt archives, encrypted entries, and zip bombs would
all crash the intake worker if propagated. We assert every error
branch lands as :class:`ArchiveExtractionError` with the original
filename context preserved.
"""

from __future__ import annotations

import gzip
import io
import tarfile
import zipfile

import pytest

from flycanon.core.services.binary.archive_unpacker import ArchiveUnpacker
from flycanon.core.services.binary.errors import ArchiveExtractionError


def _zip_bytes(members: dict[str, bytes], *, encrypted: bool = False) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, payload in members.items():
            zf.writestr(name, payload)
    data = bytearray(buf.getvalue())
    if encrypted:
        # Python's stdlib zipfile resets ``flag_bits`` on write, so we
        # post-process the bytes to flip bit 0 (encryption) in every
        # local file header (PK\x03\x04, flag at offset 6) and every
        # central directory entry (PK\x01\x02, flag at offset 8). This
        # is enough for the unpacker's ``info.flag_bits & 0x1`` guard
        # to trip.
        for sig, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            start = 0
            while True:
                idx = data.find(sig, start)
                if idx < 0:
                    break
                data[idx + offset] |= 0x01
                start = idx + len(sig)
    return bytes(data)


def _tar_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, payload in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


@pytest.fixture
def unpacker() -> ArchiveUnpacker:
    return ArchiveUnpacker()


class TestSupports:
    @pytest.mark.parametrize(
        "media_type",
        [
            "application/zip",
            "application/x-7z-compressed",
            "application/x-tar",
            "application/gzip",
            "application/epub+zip",
        ],
    )
    def test_supports_known_archives(self, media_type: str):
        assert ArchiveUnpacker.supports(media_type) is True

    def test_rejects_non_archive(self):
        assert ArchiveUnpacker.supports("application/pdf") is False


class TestZip:
    def test_iterates_all_members_in_order(self, unpacker: ArchiveUnpacker):
        data = _zip_bytes({"a.txt": b"A", "nested/b.txt": b"B"})
        members = list(unpacker.unpack(data, media_type="application/zip"))
        assert dict(members) == {"a.txt": b"A", "nested/b.txt": b"B"}

    def test_encrypted_entries_raise_archive_extraction_error(self, unpacker: ArchiveUnpacker):
        data = _zip_bytes({"secret.txt": b"hidden"}, encrypted=True)
        with pytest.raises(ArchiveExtractionError) as excinfo:
            list(unpacker.unpack(data, media_type="application/zip", filename="x.zip"))
        assert "encrypted" in str(excinfo.value).lower()
        assert excinfo.value.filename == "x.zip"
        assert excinfo.value.code == "archive_extraction_failed"

    def test_corrupt_zip_raises_archive_extraction_error(self, unpacker: ArchiveUnpacker):
        with pytest.raises(ArchiveExtractionError):
            list(unpacker.unpack(b"not a zip", media_type="application/zip"))

    def test_directories_are_skipped(self, unpacker: ArchiveUnpacker):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("folder/", b"")
            zf.writestr("folder/file.txt", b"payload")
        members = list(unpacker.unpack(buf.getvalue(), media_type="application/zip"))
        assert members == [("folder/file.txt", b"payload")]

    def test_epub_routes_through_zip(self, unpacker: ArchiveUnpacker):
        data = _zip_bytes({"OEBPS/chap1.html": b"<html/>"})
        members = list(unpacker.unpack(data, media_type="application/epub+zip"))
        assert members == [("OEBPS/chap1.html", b"<html/>")]


class TestTar:
    def test_iterates_all_file_members(self, unpacker: ArchiveUnpacker):
        data = _tar_bytes({"docs/spec.txt": b"hello", "img.png": b"\x89PNG"})
        members = list(unpacker.unpack(data, media_type="application/x-tar"))
        assert dict(members) == {"docs/spec.txt": b"hello", "img.png": b"\x89PNG"}

    def test_corrupt_tar_raises_archive_extraction_error(self, unpacker: ArchiveUnpacker):
        with pytest.raises(ArchiveExtractionError):
            list(unpacker.unpack(b"not-a-tar" * 100, media_type="application/x-tar"))


class TestGzip:
    def test_single_member_decompresses(self, unpacker: ArchiveUnpacker):
        raw = b"a happy little payload"
        data = gzip.compress(raw)
        members = list(unpacker.unpack(data, media_type="application/gzip", filename="payload.txt.gz"))
        assert members == [("payload.txt", raw)]

    def test_strips_tgz_suffix_for_inner_name(self, unpacker: ArchiveUnpacker):
        raw = b"x"
        data = gzip.compress(raw)
        members = list(unpacker.unpack(data, media_type="application/gzip", filename="bundle.tgz"))
        assert members[0][0] == "bundle"

    def test_corrupt_gzip_raises(self, unpacker: ArchiveUnpacker):
        with pytest.raises(ArchiveExtractionError):
            list(unpacker.unpack(b"\x1f\x8b\x00garbage", media_type="application/gzip"))


class TestUnsupportedMediaType:
    def test_unknown_archive_raises(self, unpacker: ArchiveUnpacker):
        with pytest.raises(ArchiveExtractionError):
            list(unpacker.unpack(b"x", media_type="application/x-cpio"))
