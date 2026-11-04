#!/usr/bin/env python3
"""Build a Linux x86_64 Python 3.13 Lambda ZIP; never publish it."""
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parent
destination = root / ".build" / "collector.zip"
destination.parent.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix="delta-gcp-lambda-") as directory:
    subprocess.run([
        sys.executable, "-m", "pip", "install", "--only-binary=:all:",
        "--platform", "manylinux_2_28_x86_64", "--platform", "manylinux2014_x86_64",
        "--implementation", "cp", "--python-version", "3.13", "--target", directory,
        "-r", str(root / "requirements.lock"),
    ], check=True)
    shutil.copy2(root / "collector.py", directory)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(Path(directory).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and not path.name.endswith(".pyc"):
                archive.write(path, path.relative_to(directory))
with zipfile.ZipFile(destination) as archive:
    assert archive.testzip() is None, "ZIP integrity failure"
    assert archive.read("collector.py") == (root / "collector.py").read_bytes(), "Stale collector source"
    for name in archive.namelist():
        if name.endswith(".so"):
            header = archive.read(name)[:20]
            assert header[:4] == b"\x7fELF" and struct.unpack("<H", header[18:20])[0] == 62, "Expected Linux x86_64 native library"
print(f"Verified {destination}: {destination.stat().st_size} bytes, current source, Linux x86_64")
