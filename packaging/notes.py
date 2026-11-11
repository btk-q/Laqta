"""notes.py <version> <zip> <out file>: this version's section of CHANGELOG.md plus the zip's
SHA-256, written as UTF-8 (the release's patch notes). Exits 1 if the section is missing."""

import hashlib
import re
import sys
from pathlib import Path


def section(changelog: str, version: str) -> str:
    found = re.search(r"(?ms)^## +v?" + re.escape(version) + r"\b[^\n]*\n(.*?)(?=^## |\Z)", changelog)
    return found.group(1).strip() if found else ""


if __name__ == "__main__":
    version, archive, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    notes = section(Path("CHANGELOG.md").read_text(encoding="utf-8"), version)
    if not notes:
        sys.exit(f"CHANGELOG.md has no '## {version}' section. Write the patch notes first.")
    if archive.is_file():  # the updater refuses a download whose hash it cannot check; GitHub lists one too
        notes += f"\n\nSHA256: {hashlib.sha256(archive.read_bytes()).hexdigest()}"
    out.write_text(notes + "\n", encoding="utf-8", newline="\n")
