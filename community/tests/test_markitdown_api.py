# 來源：K-Dense scientific-agent-skills（https://github.com/K-Dense-AI/scientific-agent-skills）
#       tests/markitdown/test_current_api.py @ a1043ef，MIT License，Copyright (c) 2025 K-Dense Inc.（見同資料夾 LICENSE.md）
# skl 修改：只留本機文件轉換（PDF／DOCX／PPTX／XLSX／CSV／ZIP）與兩支批次腳本的測試；拿掉要裝 markitdown[all] 才跑得到的 OpenAI 圖片說明、OCR 外掛、MCP 伺服器、Azure 文件智慧、語音轉文字／YouTube 測試（都是假連線，但套件很重）；技能路徑改成 community/skills/；缺套件改成直接失敗（不 skip）。
"""Small synthetic conversions and offline provider contracts for MarkItDown 0.1.8."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import _skill_helpers  # 同資料夾；pytest 會把 community/tests 放進 sys.path

md = _skill_helpers.need("markitdown")
SKILL_ROOT = _skill_helpers.skill_root("markitdown")


@pytest.fixture(autouse=True)
def block_real_network(monkeypatch):
    import socket

    def denied(*args, **kwargs):
        raise AssertionError("Network is forbidden in synthetic conversion tests")

    monkeypatch.setattr(socket.socket, "connect", denied)


@pytest.fixture
def image_bytes():
    Image = _skill_helpers.need("PIL.Image")
    stream = io.BytesIO()
    Image.new("RGB", (48, 32), "white").save(stream, format="PNG")
    return stream.getvalue()


@pytest.fixture
def corpus(tmp_path, image_bytes):
    fitz = _skill_helpers.need("pymupdf")
    docx = _skill_helpers.need("docx")
    pptx = _skill_helpers.need("pptx")
    openpyxl = _skill_helpers.need("openpyxl")
    image = tmp_path / "figure.png"
    image.write_bytes(image_bytes)
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Synthetic PDF sentinel")
    pdf.save(tmp_path / "Smith_2026_Sentinel.pdf")
    pdf.close()
    scanned = fitz.open()
    scanned.new_page(width=72, height=72).insert_image(
        fitz.Rect(0, 0, 72, 72), stream=image_bytes
    )
    scanned.save(tmp_path / "scan.pdf")
    scanned.close()
    doc = docx.Document()
    doc.add_heading("DOCX sentinel", level=1)
    doc.add_paragraph("Native document content")
    doc.add_picture(str(image))
    doc.add_picture(str(image))
    doc.save(tmp_path / "doc.docx")
    prs = pptx.Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.shapes.add_textbox(0, 0, 5000000, 1000000).text_frame.text = "PPTX sentinel"
    slide.shapes.add_picture(str(image), 0, 1000000)
    slide.notes_slide.notes_text_frame.text = "Notes sentinel"
    prs.save(tmp_path / "slides.pptx")
    wb = openpyxl.Workbook()
    wb.active.title = "Visible"
    wb.active.append(["sample", "value"])
    wb.active.append(["control", 3])
    wb.active.add_image(openpyxl.drawing.image.Image(str(image)), "D1")
    hidden = wb.create_sheet("Hidden")
    hidden.sheet_state = "hidden"
    hidden.append(["hidden sentinel"])
    wb.save(tmp_path / "book.xlsx")
    return tmp_path


@pytest.mark.parametrize(
    "name,sentinels",
    [
        ("Smith_2026_Sentinel.pdf", ["Synthetic PDF sentinel"]),
        ("doc.docx", ["# DOCX sentinel", "Native document content"]),
        ("slides.pptx", ["PPTX sentinel", "Notes sentinel"]),
        ("book.xlsx", ["## Visible", "control", "## Hidden", "hidden sentinel"]),
    ],
)
def test_local_document_semantics(corpus, name, sentinels):
    result = md.MarkItDown().convert_local(corpus / name)
    for text in sentinels:
        assert text in result.markdown
    assert str(result) == result.markdown


def test_csv_ragged_rows_and_cr_lines():
    result = md.MarkItDown().convert_stream(
        io.BytesIO(b"a,b\r1,2,extra\r"), stream_info=md.StreamInfo(extension=".csv")
    )
    assert "extra" in result.markdown and "| a | b |" in result.markdown


def test_uppercase_data_uri_and_binary_stream_contract():
    converter = md.MarkItDown()
    assert (
        "sentinel"
        in converter.convert_uri("DATA:text/plain;BASE64,c2VudGluZWw=").markdown
    )
    with pytest.raises(TypeError):
        converter.convert(io.StringIO("not binary"))


def test_validated_response_and_zip_duplicate_entries():
    import requests

    response = requests.Response()
    response.status_code = 200
    response.url = "https://example.invalid/synthetic.html"
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    response._content = b"<h1>Response sentinel</h1>"
    response._content_consumed = True
    assert "# Response sentinel" in md.MarkItDown().convert_response(response).markdown
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("same.txt", "first sentinel")
        with pytest.warns(UserWarning):
            z.writestr("same.txt", "second sentinel")
    archive.seek(0)
    text = (
        md.MarkItDown()
        .convert_stream(archive, stream_info=md.StreamInfo(extension=".zip"))
        .markdown
    )
    assert "first sentinel" in text and "second sentinel" in text


def test_cli_stdin_and_plugin_flag_contract(tmp_path):
    output = tmp_path / "out.md"
    call = subprocess.run(
        [
            sys.executable,
            "-m",
            "markitdown",
            "-x",
            ".csv",
            "-m",
            "text/csv",
            "-o",
            str(output),
        ],
        input=b"x,y\n1,2\n",
        capture_output=True,
    )
    assert call.returncode == 0, call.stderr.decode()
    assert "| x | y |" in output.read_text()
    unsupported = subprocess.run(
        [sys.executable, "-m", "markitdown", "--llm-model", "synthetic"],
        capture_output=True,
    )
    assert unsupported.returncode == 2


def test_batch_and_literature_clis(corpus, tmp_path):
    out = tmp_path / "batch"
    manifest = out / "manifest.json"
    command = [
        sys.executable,
        str(SKILL_ROOT / "scripts/batch_convert.py"),
        str(corpus),
        str(out),
        "--extensions",
        ".docx",
        ".pptx",
        ".xlsx",
        "--manifest",
        str(manifest),
    ]
    first = subprocess.run(command, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    assert all(
        r["status"] == "converted" for r in json.loads(manifest.read_text())["records"]
    )
    assert (out / "doc.docx.md").is_file()
    second = subprocess.run(command, capture_output=True, text=True)
    assert second.returncode == 0
    assert all(
        r["status"] == "skipped" for r in json.loads(manifest.read_text())["records"]
    )
    papers = tmp_path / "papers"
    papers.mkdir()
    (papers / "Smith_2026_Sentinel.pdf").write_bytes(
        (corpus / "Smith_2026_Sentinel.pdf").read_bytes()
    )
    literature = tmp_path / "literature"
    run = subprocess.run(
        [
            sys.executable,
            str(SKILL_ROOT / "scripts/convert_literature.py"),
            str(papers),
            str(literature),
            "--create-index",
            "--organize-by-year",
        ],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr
    text = (literature / "2026/Smith_2026_Sentinel.md").read_text()
    assert "source_sha256:" in text and "Synthetic PDF sentinel" in text
    assert "2026/Smith_2026_Sentinel.md" in (literature / "INDEX.md").read_text()
