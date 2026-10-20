"""Cleanup must only remove reproducible build files, including linked libraries."""
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

spec = importlib.util.spec_from_file_location('songcut_build_cleanup', Path(__file__).resolve().parents[3] / 'scripts/clean.py')
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)


def put(root, relative, data=b'keep'):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_removes_only_allowlisted_copies_preserves_user_data_and_installers(tmp_path):
    for relative in cleanup.BUILD_WORK:
        put(tmp_path, relative if relative.endswith('.spec') else relative + '/input', b'work')
    for relative in cleanup.BACKEND_COPIES:
        put(tmp_path, relative + '/torch/lib/libtorch_cpu.dylib', b'lib')
    protected = ['.venv/torch/libtorch_cpu.dylib', 'var/models/best.pt', 'var/media/video.mp4',
                 'var/studio.sqlite3', 'upstream/src/train.py', 'work/release-media/ffmpeg',
                 'apps/web/dist/index.html', 'apps/desktop/src-tauri/target/release/bundle/app.dmg',
                 cleanup.PACKAGED_BACKEND + '/torch/libtorch_cpu.dylib']
    for relative in protected:
        put(tmp_path, relative)
    assert cleanup.remove_paths(tmp_path, [*cleanup.BUILD_WORK, *cleanup.BACKEND_COPIES]) == 22
    for relative in protected:
        assert (tmp_path / relative).read_bytes() == b'keep'
    assert cleanup.remove_paths(tmp_path, cleanup.BUILD_WORK) == 0
    with pytest.raises(ValueError):
        cleanup.remove_paths(tmp_path, ['var'])


def symlink_or_skip(link, target, is_dir=False):
    try:
        link.symlink_to(target, target_is_directory=is_dir)
    except OSError:
        pytest.skip('symlink creation unavailable on this host')


def test_rejects_linked_parent_before_removing_anything(tmp_path):
    first = put(tmp_path, cleanup.BUILD_WORK[0] + '/file')
    outside = tmp_path / 'user-data'
    outside.mkdir()
    link = tmp_path / 'apps'
    symlink_or_skip(link, outside, True)
    with pytest.raises(ValueError):
        cleanup.remove_paths(tmp_path, [cleanup.BUILD_WORK[0], cleanup.BACKEND_COPIES[0]])
    assert first.exists()


def test_nested_symlink_never_traverses_or_deletes_target(tmp_path):
    protected = put(tmp_path, 'var/models/best.pt', b'model')
    folder = tmp_path / cleanup.BACKEND_COPIES[0]
    folder.mkdir(parents=True)
    link = folder / 'linked-model'
    symlink_or_skip(link, protected)
    assert cleanup.size(folder) == link.lstat().st_size
    cleanup.remove_paths(tmp_path, [cleanup.BACKEND_COPIES[0]])
    assert protected.read_bytes() == b'model'


def test_preview_does_not_delete_or_scan_processes(tmp_path, monkeypatch, capsys):
    path = put(tmp_path, cleanup.BUILD_WORK[0] + '/input')
    monkeypatch.setattr(cleanup, 'ROOT', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['clean.py'])
    monkeypatch.setattr(cleanup, 'running_commands', lambda: pytest.fail('preview must not inspect processes'))
    cleanup.main()
    assert path.exists()
    assert 'Preview only' in capsys.readouterr().out


@pytest.mark.parametrize('process', ['python -m PyInstaller main.py', 'cargo build --release',
                                    'cargo.exe test', '/Applications/VTuber SongCut.app/Contents/MacOS/app',
                                    '/tmp/backend/songcut-backend'])
def test_active_desktop_or_build_blocks_cleanup(tmp_path, monkeypatch, process):
    path = put(tmp_path, cleanup.BUILD_WORK[0] + '/input')
    monkeypatch.setattr(cleanup, 'ROOT', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['clean.py', '--apply'])
    monkeypatch.setattr(cleanup, 'running_commands', lambda: [(-1, process)])
    with pytest.raises(SystemExit, match='no files removed'):
        cleanup.main()
    assert path.exists()


def test_process_scan_failure_fails_closed(tmp_path, monkeypatch):
    path = put(tmp_path, cleanup.BUILD_WORK[0] + '/input')
    monkeypatch.setattr(cleanup, 'ROOT', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['clean.py', '--apply'])
    def unavailable():
        raise subprocess.TimeoutExpired('ps', 10)
    monkeypatch.setattr(cleanup, 'running_commands', unavailable)
    with pytest.raises(SystemExit, match='no files removed'):
        cleanup.main()
    assert path.exists()


def test_apply_can_remove_opted_in_staging_but_keeps_source_runtime(tmp_path, monkeypatch):
    staging = put(tmp_path, cleanup.PACKAGED_BACKEND + '/lib')
    runtime = put(tmp_path, '.venv/lib')
    monkeypatch.setattr(cleanup, 'ROOT', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['clean.py', '--apply', '--include-packaged-backend'])
    monkeypatch.setattr(cleanup, 'running_commands', lambda: [(-1, 'python -m studio.api')])
    cleanup.main()
    assert not staging.exists()
    assert runtime.exists()
