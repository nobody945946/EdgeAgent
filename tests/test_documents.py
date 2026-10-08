"""edge_agent.documents: the one way a PDF becomes text, for attachments and the web tools."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

from edge_agent import documents
from edge_agent.runtime import CORE_ROOT, NODE_VERSION
from tests.pdf_fixture import pdf_bytes


def test_a_pdf_gives_its_text_and_a_broken_one_says_why() -> None:
    assert "HELLO_PDF" in documents.pdf_text(pdf_bytes("HELLO_PDF"))
    with pytest.raises(ValueError, match="invalid PDF"):
        documents.pdf_text(b"%PDF-1.4 not really")


def test_the_command_line_reads_a_pdf_from_stdin() -> None:
    done = subprocess.run([sys.executable, "-m", "edge_agent.documents", "pdf"], input=pdf_bytes("CLI_PDF"),
                          capture_output=True, check=False)
    assert done.returncode == 0 and b"CLI_PDF" in done.stdout


def test_web_fetch_reads_json_apis_and_pdfs(tmp_path) -> None:
    pinned = CORE_ROOT / f".runtime/node/node-v{NODE_VERSION}-win-x64/node.exe"
    node = str(pinned) if pinned.is_file() else shutil.which("node")
    if node is None:
        pytest.skip("requires Node")
    (tmp_path / "problems.pdf").write_bytes(pdf_bytes("ICPC_PROBLEM_A"))
    subprocess.run([node, str(CORE_ROOT / "tests/web_documents_check.mjs")], check=True,
                   env={**os.environ, "EDGE_TEST_PDF": str(tmp_path / "problems.pdf"), "EDGE_TEST_PYTHON": sys.executable})
