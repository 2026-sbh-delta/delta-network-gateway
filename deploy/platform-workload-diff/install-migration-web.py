"""Append the comparison entry to the verified release-handoff web shell."""

import hashlib
import re
import shutil
import sys
from pathlib import Path

DIST = Path("/app/web/dist")
SHELL_SHA = "ea0978120c6dea96dc745f21243cd68cbb7b89cd93873f64563ca4517d59afee"
BUNDLE = Path("/opt/delta-release/migration-diff-entry.js")
STYLE = Path("/opt/delta-release/migration-diff.css")
ENTRY = b'<script type="module" src="/assets/migration-diff-c263ebec80fe1b9b.js"></script>\n'


def install_style(index, shell):
    if shell.count(ENTRY) != 1 or shell.count(b"</head>") != 1:
        raise ValueError("The comparison web entry is missing")
    if hashlib.sha256(shell.replace(ENTRY, b"", 1)).hexdigest() != SHELL_SHA:
        raise ValueError("The operating web shell does not match the reviewed release")
    fingerprint = hashlib.sha256(STYLE.read_bytes()).hexdigest()[:16]
    name = f"migration-diff-{fingerprint}.css"
    asset = DIST / "assets" / name
    if asset.exists():
        raise ValueError("The comparison stylesheet is already installed")
    shutil.copyfile(STYLE, asset)
    link = f'<link rel="stylesheet" href="/assets/{name}">\n'.encode()
    index.write_bytes(shell.replace(b"</head>", link + b"</head>", 1))


def main(style_only=False):
    index = DIST / "index.html"
    shell = index.read_bytes()
    if style_only:
        install_style(index, shell)
        return
    if hashlib.sha256(shell).hexdigest() != SHELL_SHA or shell.count(b"</body>") != 1:
        raise ValueError("The operating web shell does not match the reviewed release")
    bundle = BUNDLE.read_bytes()
    fingerprint = hashlib.sha256(bundle).hexdigest()[:16]
    name = f"migration-diff-{fingerprint}.js"
    asset = DIST / "assets" / name
    if asset.exists() or not re.search(rb"/assets/index-CkBRm7ux\.js", shell):
        raise ValueError("The operating web entry does not match the reviewed release")
    shutil.copyfile(BUNDLE, asset)
    script = f'<script type="module" src="/assets/{name}"></script>\n'.encode()
    index.write_bytes(shell.replace(b"</body>", script + b"</body>", 1))


if __name__ == "__main__":
    main(sys.argv[1:] == ["--style-only"])
