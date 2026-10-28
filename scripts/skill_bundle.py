#!/usr/bin/env python3
"""Stage the skill bundle shared by publishing and compliance checks."""
from pathlib import Path
import shutil
import sys


def stage_bundle(source: Path, destination: Path) -> None:
    """Copy, prune tests/caches/bytecode, and remove root dotfiles."""
    shutil.copytree(source, destination, symlinks=True)
    for path in sorted(destination.rglob('*'), key=lambda p: len(p.parts)):
        if path.is_symlink() or not path.exists():
            continue
        if path.is_dir() and path.name in {'tests', '__pycache__'}:
            shutil.rmtree(path)
        elif path.is_file() and path.name.endswith(('.pyc', '.pyo')):
            path.unlink()
    for path in destination.glob('.*'):
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


if __name__ == '__main__':
    stage_bundle(Path(sys.argv[1]), Path(sys.argv[2]))
