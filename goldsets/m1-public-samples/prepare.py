"""Download pinned M1 samples and generate the controlled insertion variants."""

import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen


MANIFEST = Path(__file__).with_name("manifest.json")
PDF_NOTE = "Fixture-only note: This page was inserted to verify document alignment."
MARKDOWN_ANCHOR = "## Node 组件  {#node-components}\n".encode()
MARKDOWN_NOTE = "验收样本附注：这一段是为验证块对齐而插入的无关文本。\n\n".encode()


def download_samples(output: Path) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for source in manifest["sources"]:
        path = output / source["file"]
        if path.is_file():
            data = path.read_bytes()
        else:
            with urlopen(source["url"], timeout=30) as response:
                data = response.read()
        digest = hashlib.sha256(data).hexdigest()
        if digest != source["sha256"]:
            raise ValueError(f"{source['id']}: SHA-256 mismatch ({digest})")
        if not path.exists():
            path.write_bytes(data)
        print(f"{source['id']}  {digest}")


def make_insertions(output: Path) -> None:
    import pymupdf

    original = output / "cfr-255-2022.pdf"
    with pymupdf.open(original) as document:
        page = document.new_page(pno=1)
        page.insert_text((72, 72), PDF_NOTE)
        document.save(output / "cfr-255-controlled-insertion.pdf", no_new_id=True)

    source = (output / "k8s-components-before.md").read_bytes()
    if source.count(MARKDOWN_ANCHOR) != 1:
        raise ValueError("Expected a unique Markdown insertion anchor")
    derivative = source.replace(MARKDOWN_ANCHOR, MARKDOWN_NOTE + MARKDOWN_ANCHOR, 1)
    (output / "k8s-controlled-insertion.md").write_bytes(derivative)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Directory for original and derived inputs")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    download_samples(args.output)
    make_insertions(args.output)


if __name__ == "__main__":
    main()
