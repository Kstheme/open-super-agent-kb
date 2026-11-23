"""Fetch or verify the 100 pinned M1 batch inputs."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from urllib.request import urlopen


MANIFEST = Path(__file__).with_name("manifest.json")


def load_sources() -> list[dict[str, str]]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    sources = manifest["sources"]
    formats = Counter(source["format"] for source in sources)
    if manifest["format_version"] != 1 or len(sources) != 100:
        raise ValueError("Expected 100 batch inputs in manifest format version 1")
    if set(formats) != {"pdf", "docx"} or min(formats.values()) < 10:
        raise ValueError("Expected at least ten native files of each format")

    for source in sources:
        name = source["id"] + "." + source["format"]
        if source["file"] != name or not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.(pdf|docx)", name):
            raise ValueError(f"Invalid source file name: {source['file']}")
        for field in ("url", "source_page", "reuse_policy"):
            if not source[field].startswith("https://"):
                raise ValueError(f"{name}: expected an HTTPS {field}")
        for field in ("sha256", "text_sha256"):
            if not re.fullmatch(r"[a-f0-9]{64}", source[field]):
                raise ValueError(f"{name}: invalid {field}")
        if not source["version"].strip() or not source["authority"].strip():
            raise ValueError(f"{name}: missing version or publisher")

    for field in ("id", "url", "sha256", "text_sha256"):
        if len({source[field] for source in sources}) != len(sources):
            raise ValueError(f"Duplicate {field} in the batch manifest")
    return sources


def prepare_source(source: dict[str, str], output: Path, *, verify_only: bool) -> None:
    path = output / source["file"]
    cached = path.is_file()
    if cached or verify_only:
        data = path.read_bytes()
    else:
        with urlopen(source["url"], timeout=45) as response:
            data = response.read()

    digest = sha256(data).hexdigest()
    if digest != source["sha256"]:
        raise ValueError(f"{path}: SHA-256 mismatch (expected {source['sha256']}, got {digest})")
    if cached or verify_only:
        return

    with NamedTemporaryFile(dir=output, prefix=".download-", delete=False) as temporary:
        staged = Path(temporary.name)
        temporary.write(data)
    try:
        staged.replace(path)
    finally:
        staged.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Dedicated directory for the 100 documents")
    parser.add_argument("--verify-only", action="store_true", help="Verify local files without network access")
    args = parser.parse_args()

    try:
        sources = load_sources()
        if not args.verify_only:
            args.output.mkdir(parents=True, exist_ok=True)

        def prepare(source: dict[str, str]) -> None:
            prepare_source(source, args.output, verify_only=args.verify_only)

        with ThreadPoolExecutor(max_workers=6) as pool:
            for _ in pool.map(prepare, sources):
                pass
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"{error}\n")

    counts = Counter(source["format"] for source in sources)
    print(f"Verified {counts['docx']} DOCX and {counts['pdf']} PDF in {args.output.resolve()}")


if __name__ == "__main__":
    main()
