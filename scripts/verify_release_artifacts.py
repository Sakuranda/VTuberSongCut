"""Verify downloaded CI release assets without unpacking source archives."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile

def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for part in iter(lambda: f.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()

def verify(directory, label):
    sums = directory / f'SHA256SUMS-{label}.txt'
    checked = {}
    for line in sums.read_text().splitlines():
        expected, name = line.split('  ', 1)
        assert Path(name).name == name, name
        path = directory / name
        actual = sha(path)
        assert actual == expected, f'Checksum mismatch: {name}'
        checked[name] = actual
    source_name = f'MediaSources-{label}.tar.gz'
    assert source_name in checked
    with tarfile.open(directory / source_name, 'r:gz') as archive:
        manifest_path = f'MediaSources-{label}/SOURCE-AND-BINARY-MANIFEST.json'
        manifest = json.load(archive.extractfile(manifest_path))
        assert manifest['platform'] == label
        assert manifest['vcpkg_commit'] == '20ceb8adbf0504d4b0d5e4058a32f9d4fc896391'
        assert manifest['source_ports'] and manifest['source_archives']
        names = set(archive.getnames())
        for source in manifest['source_archives']:
            assert f'MediaSources-{label}/original-source-archives/{source}' in names
    report_name = {'macOS-AppleSilicon': 'macOS', 'Windows-x64': 'Windows', 'Linux-x64': 'Linux'}[label] + '-installer-smoke.json'
    assert report_name in checked
    report = json.loads((directory / report_name).read_text(encoding='utf-8-sig'))
    if label == 'Linux-x64':
        assert {Path(item['installer']).suffix for item in report} == {'.AppImage', '.deb'}
        for item in report:
            assert item['status'] == 'passed'
            assert checked[item['installer']] == item['installer_sha256']
        # linuxdeploy can adjust ELF files while making AppImage; report records
        # installed hashes, while source manifest identifies the frozen originals.
    else:
        assert report['platform'] == label and report['checks']
        if label == 'Windows-x64':
            assert report['passed'] is True
        assert checked[report['files']['installer']['name']] == report['files']['installer']['sha256']
        for name in ('ffmpeg', 'ffprobe'):
            assert report['files'][name]['sha256'] == manifest['binaries'][name]['frozen_sha256']
    return {'platform': label, 'assets': checked, 'source_ports': len(manifest['source_ports']),
            'original_source_archives': len(manifest['source_archives']),
            'installer_report': report, 'verified': True}

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('label')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = verify(args.directory, args.label)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'installer_report'}, indent=2))
