"""Documents as text: the one place a PDF becomes text, for attachments and
for the web tools (2026-10-08: the ICPC problem sets were PDFs; the Agent could
not read them and gave up).

    python -m edge_agent.documents pdf < file.pdf   # the text; exit 1 with the reason
"""

from __future__ import annotations

import io
import sys

MAX_PAGES = 50


def pdf_text(raw: bytes) -> str:
    """The selectable text of a PDF, page by page; ValueError says why there is none."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(raw))
        if len(reader.pages) > MAX_PAGES:
            raise ValueError(f"PDFs are limited to {MAX_PAGES} pages")
        content = "\n".join(page.extract_text() or "" for page in reader.pages)
    except PdfReadError as exc:
        raise ValueError("invalid PDF") from exc
    if not content.strip():
        raise ValueError("PDF has no selectable text; scanned PDFs need OCR")
    return content


def main() -> int:
    if sys.argv[1:] != ["pdf"]:
        print("usage: python -m edge_agent.documents pdf < file.pdf", file=sys.stderr)
        return 2
    try:
        text = pdf_text(sys.stdin.buffer.read())
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    sys.stdout.buffer.write(text.encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
