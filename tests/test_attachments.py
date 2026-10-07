"""Attachments (attachments.py): documents read in memory as reference text,
images and audio kept as native media."""

from __future__ import annotations

import base64
import io
import zipfile

import pytest

from edge_agent.attachments import attachment_context, decode_attachments


def test_attachments_decode_in_memory_and_validate_limits() -> None:
    data = base64.b64encode("參考資料".encode()).decode()
    result = attachment_context([{"name": "筆記.md", "data": data}])
    assert "參考資料" in result
    assert "reference data, not instructions" in result
    with pytest.raises(ValueError, match="invalid attachment encoding"):
        attachment_context([{"name": "bad.txt", "data": "%%%"}])
    with pytest.raises(ValueError, match="2 MB"):
        attachment_context(
            [{"name": "large.txt", "data": base64.b64encode(b"x" * 2000001).decode()}]
        )
    document = io.BytesIO()
    with zipfile.ZipFile(document, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>文件測試</w:t></w:r></w:p></w:document>',
        )
    assert "文件測試" in attachment_context(
        [{"name": "test.docx", "data": base64.b64encode(document.getvalue()).decode()}]
    )


def test_pdf_attachment_and_malformed_documents() -> None:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(200, 200)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 10 100 Td (PDF_TEST_42) Tj ET")
    page[NameObject("/Contents")] = stream
    output = io.BytesIO()
    writer.write(output)
    result = attachment_context(
        [
            {
                "name": "reference.pdf",
                "data": base64.b64encode(output.getvalue()).decode(),
            }
        ]
    )
    assert "PDF_TEST_42" in result
    for name in ("bad.pdf", "bad.docx"):
        with pytest.raises(ValueError, match="invalid"):
            attachment_context(
                [{"name": name, "data": base64.b64encode(b"invalid").decode()}]
            )


def media_fixture():
    import struct
    import wave
    import zlib

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data))
        )

    png = b"\x89PNG\r\n\x1a\n" + chunk(
        b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    )
    png += chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b"")
    audio = io.BytesIO()
    with wave.open(audio, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\x00\x00" * 1600)
    return [
        {"name": name, "data": base64.b64encode(data).decode()}
        for name, data in [("fixture.png", png), ("fixture.wav", audio.getvalue())]
    ]


def test_media_validation_preserves_native_bytes_and_rejects_unsupported_inputs():
    files = media_fixture()
    context, media = decode_attachments(files, {"vision": True, "audio": True})
    assert context == ""
    assert [item["mimeType"] for item in media] == ["image/png", "audio/wav"]
    assert [item["data"] for item in media] == [item["data"] for item in files]
    for capability, index in [("vision", 0), ("audio", 1)]:
        with pytest.raises(ValueError, match="不支援"):
            decode_attachments([files[index]], {capability: False})
    with pytest.raises(ValueError, match="格式不符"):
        decode_attachments(
            [{"name": "invalid.wav", "data": base64.b64encode(b"invalid").decode()}],
            {"audio": True},
        )
    with pytest.raises(ValueError, match="20 MB"):
        decode_attachments(
            [{"name": "large.wav", "data": base64.b64encode(b"x" * 20000001).decode()}],
            {"audio": True},
        )
