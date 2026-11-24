import io
import os
import unittest
from pathlib import Path
from zipfile import ZipFile
from contextlib import redirect_stderr

from test_ingest_cli import _IngestHarness


WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _write_word(path: Path, update: str) -> None:
    """Write a small Word package with a custom heading style and merged table."""
    document = f"""<w:document xmlns:w="{WORD}"><w:body>
      <w:p><w:pPr><w:pStyle w:val="Guia"/></w:pPr><w:r><w:t>验收指南</w:t></w:r></w:p>
      <w:p/>
      <w:p><w:r><w:t>只处理已确认的资料。</w:t></w:r></w:p>
      <w:tbl><w:tblGrid><w:gridCol/><w:gridCol/><w:gridCol/></w:tblGrid>
        <w:tr>
          <w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge w:val="restart"/></w:tcPr>
            <w:p><w:r><w:t>合并类别</w:t></w:r></w:p></w:tc>
          <w:tc><w:p><w:r><w:t>保留说明</w:t></w:r></w:p></w:tc>
        </w:tr>
        <w:tr>
          <w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge/></w:tcPr><w:p/></w:tc>
          <w:tc><w:p><w:r><w:t>{update}</w:t></w:r></w:p></w:tc>
        </w:tr>
      </w:tbl>
    </w:body></w:document>"""
    styles = f"""<w:styles xmlns:w="{WORD}">
      <w:style w:type="paragraph" w:styleId="Base">
        <w:pPr><w:outlineLvl w:val="0"/></w:pPr>
      </w:style>
      <w:style w:type="paragraph" w:styleId="Guia">
        <w:name w:val="Encabezado personalizado"/><w:basedOn w:val="Base"/>
      </w:style>
    </w:styles>"""
    types = """<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
      <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
      <Override PartName="/word/document.xml"
        ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
      <Override PartName="/word/styles.xml"
        ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
    </Types>"""
    relations = """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="root" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
        Target="word/document.xml"/>
    </Relationships>"""
    style_relation = relations.replace("officeDocument\"", "styles\"").replace(
        'Target="word/document.xml"', 'Target="styles.xml"',
    )
    with ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", types)
        archive.writestr("_rels/.rels", relations)
        archive.writestr("word/_rels/document.xml.rels", style_relation)
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", styles)


class DocxIngestTests(_IngestHarness):
    def test_merges_and_original_versions_remain_verifiable_through_cli(self):
        word = self.root / "guide.docx"
        _write_word(word, "起始说明")
        original = word.read_bytes()
        before = self.add(word, "acceptance-guide")
        self.assertEqual(self.add(word, "acceptance-guide"), before)
        _write_word(word, "新版说明")
        after = self.add(word, "acceptance-guide")
        self.assertNotEqual(before, after)

        blocks = self.call("show", before)["blocks"]
        heading, paragraph, first, second = blocks
        self.assertEqual(heading["kind"], "heading")
        self.assertEqual(heading["heading_path"], ["验收指南"])
        self.assertEqual(paragraph["locator"]["paragraph"], 3)
        self.assertEqual(paragraph["heading_path"], ["验收指南"])
        self.assertEqual(first["text"], "合并类别 | 保留说明")
        self.assertEqual(second["text"], " | 起始说明")
        self.assertEqual([(row["kind"], row["locator"]["table"], row["locator"]["row"])
                          for row in (first, second)], [("table_row", 1, 1), ("table_row", 1, 2)])
        for row in (first, second):
            merged, unmerged = row["locator"]["table_cells"]
            self.assertEqual((merged["column"], merged["column_span"], merged["vertical_start_row"]),
                             (1, 2, 1))
            self.assertEqual((unmerged["column"], unmerged["column_span"], unmerged["vertical_start_row"]),
                             (3, 1, None))

        retained = self.root / "retained.docx"
        self.call("original", before, "--out", retained)
        self.assertEqual(retained.read_bytes(), original)
        self.assertIn("新版说明", self.call("show", after)["blocks"][-1]["text"])

    def test_evidence_cannot_include_the_synthetic_cell_separator(self):
        word = self.root / "guide.docx"
        _write_word(word, "续行")
        version = self.add(word, "guide")
        row = next(block for block in self.call("show", version)["blocks"] if block["kind"] == "table_row")
        with redirect_stderr(io.StringIO()) as error, self.assertRaises(SystemExit):
            self.call("evidence-add", version, row["id"], "--quote", row["text"])
        self.assertIn("single source table cell", error.getvalue())
        evidence = self.call("evidence-add", version, row["id"], "--quote", "保留说明")
        self.assertEqual(evidence["quote_start"], 7)

    @unittest.skipUnless(os.getenv("OPENKB_M1_SAMPLES"), "pinned M1 acceptance files are opt-in")
    def test_section508_native_docx_preserves_headings_paragraphs_and_physical_table_rows(self):
        source = Path(os.environ["OPENKB_M1_SAMPLES"]) / "section508-word.docx"
        blocks = self.call("show", self.add(source, "section508-checklist"))["blocks"]

        self.assertTrue(any(block["kind"] == "heading" and block["text"] == "Headings" for block in blocks))
        self.assertTrue(any(block["locator"]["paragraph"] == 2
                            and "Accessibility results" in block["text"] for block in blocks))
        rows = {(block["locator"]["table"], block["locator"]["row"]): block
                 for block in blocks if block["kind"] == "table_row" }
        self.assertEqual({table for table, _ in rows}, set(range(1, 18)))
        self.assertIn("descriptive filename", rows[1, 1]["text"])
        self.assertIn("Headings", rows[2, 1]["heading_path"])
