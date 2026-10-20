#!/usr/bin/env python3
"""Preview/remove disposable desktop build copies, never user work or dependencies."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BUILD_WORK = (
    'work/desktop-build/pyinstaller',
    'work/desktop-build/resources',
    'work/desktop-build/media-inputs',
    'work/desktop-build/songcut-backend.spec',
)
BACKEND_COPIES = (
    'apps/desktop/src-tauri/target/debug/backend',
    'apps/desktop/src-tauri/target/release/backend',
)
PACKAGED_BACKEND = 'apps/desktop/src-tauri/binaries/songcut-backend'
ALLOWED = {*BUILD_WORK, *BACKEND_COPIES, PACKAGED_BACKEND}


def is_link(path: Path) -> bool:
    return path.is_symlink() or getattr(path, 'is_junction', lambda: False)()


def checked_path(root: Path, relative: str) -> Path:
    if relative not in ALLOWED:
        raise ValueError(f'Not a disposable build path: {relative}')
    root = root.resolve()
    path = root
    for part in Path(relative).parts:
        path /= part
        if is_link(path):
            raise ValueError(f'Refusing a linked cleanup path: {path}')
    return path


def size(path: Path) -> int:
    # lstat counts a link itself, never its target or a second full library copy.
    if is_link(path) or path.is_file():
        return path.lstat().st_size
    if not path.exists():
        return 0
    return sum(size(child) for child in path.iterdir())


def remove_paths(root: Path, relatives) -> int:
    # Validate the entire plan before changing any files.
    paths = [checked_path(root, relative) for relative in relatives]
    total = 0
    for path in paths:
        total += size(path)
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    return total


def running_commands():
    if os.name == 'nt':
        result = subprocess.run([
            'powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
            'Get-CimInstance Win32_Process | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress',
        ], check=True, capture_output=True, text=True, timeout=30)
        rows = json.loads(result.stdout or '[]')
        if isinstance(rows, dict):
            rows = [rows]
        return [(int(row['ProcessId']), row.get('CommandLine') or '') for row in rows]
    result = subprocess.run(['ps', '-axo', 'pid=,command='], check=True,
                            capture_output=True, text=True, timeout=10)
    return [(int(parts[0]), parts[1]) for line in result.stdout.splitlines()
            if len(parts := line.strip().split(None, 1)) == 2]


def busy_processes(rows):
    pattern = re.compile(
        r'PyInstaller|build_desktop_backend\.py|(?:cargo|tauri)(?:\.exe)?\s+(?:build|run|test|dev)\b'
        r'|songcut-backend|VTuber SongCut\.app|vtuber-songcut-desktop', re.I)
    return [pid for pid, command in rows if pid != os.getpid() and pattern.search(command)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Actually remove the displayed paths (default: preview)')
    parser.add_argument('--include-packaged-backend', action='store_true',
                        help='Also remove the rebuildable PyInstaller staging runtime; requires repackaging before desktop build')
    args = parser.parse_args()
    relatives = [*BUILD_WORK, *BACKEND_COPIES]
    if args.include_packaged_backend:
        relatives.append(PACKAGED_BACKEND)
    total = 0
    for relative in relatives:
        path = checked_path(ROOT, relative)
        if path.exists():
            count = size(path)
            total += count
            print(f'{count / 1024**2:10.1f} MiB  {relative}')
    print(f'Total file size: {total / 1024**2:.1f} MiB (filesystem space recovered may differ).')
    print('Preserved: .venv, models, recordings, var, upstream, web/dist, installers and Rust compilation cache.')
    if not args.apply:
        print('Preview only. Add --apply to clean; close desktop apps and stop builds first.')
        return
    try:
        busy = busy_processes(running_commands())
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise SystemExit(f'Cannot verify running processes; no files removed: {exc}')
    if busy:
        raise SystemExit(f'Desktop runtime/build processes may be using these files; no files removed. PIDs: {busy}')
    remove_paths(ROOT, relatives)
    print('Cleanup complete.')


if __name__ == '__main__':
    main()
