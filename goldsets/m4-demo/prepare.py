"""Prepare the separate M4 demonstration pack using pinned Section 508 documents."""

import argparse
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from urllib.request import urlopen
import xml.etree.ElementTree as ET
from zipfile import ZipFile


GOLDSETS = Path(__file__).resolve().parent.parent
WORD_ID = "section508-word"
SCOPE_ID = "508-applicability-checklist"
SIMULATED_NAME = "section508-word-SIMULATED-REVISION.docx"
DOCUMENT_XML = "word/document.xml"
WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
OLD_RULE = "Tables repeat their header row."
NEW_RULE = "Only data tables that span multiple pages must repeat their header row."
OLD_RUN = b"<w:t>Tables repeat their</w:t>"
NEW_RUN = b"<w:t>Only data tables that span multiple pages must repeat their</w:t>"


def pinned_source(collection: str, source_id: str) -> dict:
    manifest = json.loads((GOLDSETS / collection / "manifest.json").read_text(encoding="utf-8"))
    return next(source for source in manifest["sources"] if source["id"] == source_id)


def read_original(source: dict, local: Path | None) -> bytes:
    if local is None:
        with urlopen(source["url"], timeout=30) as response:
            data = response.read()
    else:
        data = local.read_bytes()
    actual = sha256(data).hexdigest()
    if actual != source["sha256"]:
        raise ValueError(
            f"{source['file']} SHA-256 mismatch: expected {source['sha256']}, got {actual}. "
            "Provide a local copy of the pinned original."
        )
    return data


def paragraphs(xml: bytes) -> list[str]:
    return [
        "".join(text.text or "" for text in paragraph.iter(WORD_NS + "t"))
        for paragraph in ET.fromstring(xml).iter(WORD_NS + "p")
    ]


def simulate_revision(original: bytes) -> bytes:
    result = BytesIO()
    with ZipFile(BytesIO(original)) as source, ZipFile(result, "w") as revised:
        document = source.read(DOCUMENT_XML)
        before = paragraphs(document)
        if sum(text.count(OLD_RULE) for text in before) != 1 or document.count(OLD_RUN) != 1:
            raise ValueError("Could not find the expected repeat-header rule in the pinned original.")
        changed = document.replace(OLD_RUN, NEW_RUN)
        if paragraphs(changed) != [text.replace(OLD_RULE, NEW_RULE) for text in before]:
            raise ValueError("The simulated revision changed unexpected document text.")
        for item in source.infolist():
            revised.writestr(item, changed if item.filename == DOCUMENT_XML else source.read(item))
    return result.getvalue()


def prepare(output: Path, *, applicability: Path | None, word: Path | None) -> None:
    originals = [
        (pinned_source("m1-batch-samples", SCOPE_ID), applicability),
        (pinned_source("m1-public-samples", WORD_ID), word),
    ]
    inputs = [(source, read_original(source, local)) for source, local in originals]
    revised = simulate_revision(inputs[1][1])
    output.mkdir(parents=True, exist_ok=True)
    (output / "originals").mkdir(exist_ok=True)
    (output / "simulated").mkdir(exist_ok=True)
    for source, data in inputs:
        (output / "originals" / source["file"]).write_bytes(data)
    (output / "simulated" / SIMULATED_NAME).write_bytes(revised)
    manifest = {
        "originals": [
            {
                "file": "originals/" + source["file"],
                **{field: source[field] for field in ("authority", "url", "reuse_policy", "sha256")},
            }
            for source, _ in inputs
        ],
        "simulated_revision": {
            "file": "simulated/" + SIMULATED_NAME,
            "source": "originals/" + inputs[1][0]["file"],
            "label": "Simulated revision for acceptance testing",
            "official_revision": False,
            "sha256": sha256(revised).hexdigest(),
            "old_rule": OLD_RULE,
            "new_rule": NEW_RULE,
        },
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--applicability", type=Path, help="Reuse an existing pinned applicability DOCX.")
    parser.add_argument("--word", type=Path, help="Reuse an existing pinned Word checklist DOCX.")
    args = parser.parse_args()
    try:
        prepare(args.output, applicability=args.applicability, word=args.word)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    print(f"Prepared M4 demo pack in {args.output.resolve()}")


if __name__ == "__main__":
    main()
