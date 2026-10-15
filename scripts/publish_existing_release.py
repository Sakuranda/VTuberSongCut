#!/usr/bin/env python3
"""Publish verified native CI artifacts without rebuilding their runtimes."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from verify_release_artifacts import verify


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def api(path):
    return json.loads(run('gh', 'api', path))


def main():
    repo = os.environ['GITHUB_REPOSITORY']
    target = os.environ['GITHUB_SHA']
    tag = os.environ['RELEASE_TAG']
    assert re.fullmatch(r'v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?', tag), 'Invalid release tag'
    config = json.loads(Path('apps/desktop/src-tauri/tauri.conf.json').read_text())
    package = json.loads(Path('apps/desktop/package.json').read_text())
    cargo = re.search(r'^version\s*=\s*"([^"]+)"', Path('apps/desktop/src-tauri/Cargo.toml').read_text(), re.M).group(1)
    assert tag[1:] == config['version'] == package['version'] == cargo, 'Version mismatch'
    destinations = [('macOS-AppleSilicon', 'MACOS_RUN'), ('Windows-x64', 'WINDOWS_RUN'), ('Linux-x64', 'LINUX_RUN')]
    output = Path('release-assets')
    output.mkdir(exist_ok=False)
    evidence = {'release_tag': tag, 'release_commit': target, 'platforms': []}
    # Packaging-only fixes can be combined without recompiling identical apps.
    # Every artifact retains its actual build commit and matching source bundle.
    application_paths = ['services/studio/studio', 'services/studio/pyproject.toml', 'apps/web',
                         'apps/desktop/src-tauri/src', 'apps/desktop/src-tauri/Cargo.toml',
                         'apps/desktop/src-tauri/Cargo.lock', 'apps/desktop/src-tauri/tauri.conf.json',
                         'apps/desktop/src-tauri/tauri.release.conf.json', 'apps/desktop/package.json',
                         'apps/desktop/package-lock.json', 'patches', 'packaging/vcpkg.json',
                         'packaging/ports', 'packaging/triplets']
    for label, variable in destinations:
        run_id = os.environ[variable]
        assert run_id.isdecimal(), 'Invalid workflow run ID'
        base = f'repos/{repo}/actions/runs/{run_id}'
        metadata = api(base)
        assert metadata['path'] == '.github/workflows/release.yml', 'Unexpected producing workflow'
        commit = metadata['head_sha']
        subprocess.run(['git', 'merge-base', '--is-ancestor', commit, target], check=True)
        subprocess.run(['git', 'diff', '--exit-code', commit, target, '--', *application_paths], check=True)
        jobs = api(base + '/jobs?per_page=100')['jobs']
        matching = [job for job in jobs if label in job['name'] and job['name'].startswith('build (')]
        assert len(matching) == 1 and matching[0]['conclusion'] == 'success', f'{label} build not successful'
        artifacts = api(base + '/artifacts?per_page=100')['artifacts']
        name = f'desktop-{label}'
        matches = [a for a in artifacts if a['name'] == name and not a['expired']]
        assert len(matches) == 1, f'Missing unique {name} artifact'
        directory = Path('work/publish') / label
        subprocess.run(['gh', 'run', 'download', run_id, '--repo', repo, '-n', name, '-D', str(directory)], check=True)
        verified = verify(directory, label)
        report = verified['installer_report']
        if isinstance(report, dict):
            assert report['commit'] == commit and str(report['run_id']) == run_id
        for asset in directory.iterdir():
            assert asset.is_file() and not (output / asset.name).exists(), 'Unexpected or duplicate release asset'
            shutil.copy2(asset, output / asset.name)
        evidence['platforms'].append({'platform': label, 'run_id': run_id, 'build_commit': commit,
                                     'workflow_url': metadata['html_url'], 'artifact_id': matches[0]['id'],
                                     'application_sources_match_release': True, 'assets': verified['assets']})
    provenance = output / 'RELEASE-PROVENANCE.json'
    provenance.write_text(json.dumps(evidence, indent=2) + '\n')
    (output / 'SHA256SUMS-PROVENANCE.txt').write_text(hashlib.sha256(provenance.read_bytes()).hexdigest() + '  ' + provenance.name + '\n')
    notes = Path('work/release-notes.md')
    lines = [f'# 拾歌 · VTuber SongCut {tag}', '',
             '首个预览版本：独立的录播切歌与模型训练工作区，支持模型导入、片段边界调整、LRC 歌词字幕及音视频导出。', '',
             '## 下载', '',
             '- macOS 15+ / Apple Silicon：下载 `.dmg`。',
             '- Windows x64：下载 `.exe` 安装器。',
             '- Linux x64：下载 `.AppImage` 或 `.deb`；Debian/Ubuntu 建议使用 `.deb`。', '',
             '安装包已包含 Python、模型运行环境、FFmpeg 和字体，无需单独安装。主播模型权重不随包发布，可在应用内训练或导入。', '',
             '模型训练及演唱检测基于 [AutoSlicer-v2](https://github.com/anAtheist987/AutoSlicer-v2)。歌名/歌手目前手动编辑，歌词支持本地 LRC 导入；自动识曲与在线歌词检索尚未实现。', '',
             '## 已验证与限制', '',
             '三个平台均通过安装包内的实际模型训练/推理、视频与字幕处理和本地服务检查。当前没有商业代码签名：macOS 使用 ad hoc 签名，尚未公证；Windows 未做 Authenticode 签名，系统可能提示未知发行者。', '',
             'Linux 需要桌面环境及 WebKitGTK/GTK 系统运行库；AppImage 可能需要 FUSE。Windows 缺少 WebView2 时安装器需要联网安装。Windows/Linux 模型运行使用 CPU，macOS 支持 CPU/MPS。', '',
             '服务器部署见 [README](https://github.com/' + repo + '#quick-start)。', '',
             '## 构建来源', '',
             '为避免重复编译，本次合并以下已验证的分平台构建。应用源码与发行提交一致，平台打包修复分别完成；真实构建提交保存在 RELEASE-PROVENANCE.json，未改写为同一构建提交。', '']
    for entry in evidence['platforms']:
        lines.append(f"- {entry['platform']}: [{entry['build_commit'][:7]}]({entry['workflow_url']})")
    lines += ['', 'SHA256SUMS 文件用于校验下载。MediaSources 附件包含对应 FFmpeg 及依赖的源码、补丁和构建记录，普通用户运行软件无需下载；再分发时请同时保留匹配源码与第三方许可证。', '']
    notes.write_text('\n'.join(lines))
    existing = subprocess.run(['gh', 'api', f'repos/{repo}/commits/{tag}', '--jq', '.sha'], capture_output=True, text=True)
    if existing.returncode == 0:
        assert existing.stdout.strip() == target, 'Existing tag has a different commit'
    elif 'HTTP 404' not in existing.stderr:
        raise RuntimeError('Could not safely determine release tag state: ' + existing.stderr)
    assets = [str(path) for path in sorted(output.iterdir())]
    found = subprocess.run(['gh', 'release', 'view', tag, '--repo', repo], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if found.returncode == 0:
        subprocess.run(['gh', 'release', 'upload', tag, '--repo', repo, '--clobber', *assets], check=True)
        subprocess.run(['gh', 'release', 'edit', tag, '--repo', repo, '--notes-file', str(notes)], check=True)
    else:
        subprocess.run(['gh', 'release', 'create', tag, '--repo', repo, '--target', target, '--prerelease',
                        '--title', f'拾歌 · VTuber SongCut {tag}', '--notes-file', str(notes), *assets], check=True)


if __name__ == '__main__':
    main()
