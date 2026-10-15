# Desktop releases

The desktop release workflow builds three native installers:

| System | Installer | Runtime |
| --- | --- | --- |
| Apple Silicon, macOS 15+ | `.dmg` | Python, CPU/MPS PyTorch, media tools and CJK font |
| Windows 10/11 x64 | NSIS `.exe` | Python, CPU PyTorch, media tools and CJK font |
| Linux x64 | `.AppImage` and `.deb` | Python, CPU PyTorch, media tools and CJK font |

Users do not install Python, Node.js or FFmpeg. All builds include the web UI, help documents, original AutoSlicer source with the reviewed Apple patch, NumPy, SciPy, torchaudio and TensorBoard. Model weights are **not** included; import a compatible model from its documented source or train your own in the application. Model licenses and singer permissions remain separate from the application's MIT license.

Linux desktop integration requires the distribution's WebKitGTK 4.1/GTK runtime and an available desktop session. AppImage may additionally need FUSE or extraction (`--appimage-extract-and-run`). The `.deb` declares its system dependencies and is preferable on supported Debian/Ubuntu desktops. Windows installers bootstrap Microsoft WebView2 when needed; this can require an internet connection. CPU builds do not provide NVIDIA CUDA acceleration.

## Build and verify

On each target OS, install Python 3.12, Node.js 22, Rust stable, the Tauri build prerequisites and the source-build prerequisites in [MEDIA-SOURCES.md](MEDIA-SOURCES.md). Install the application and build packages:

```sh
python -m pip install -e 'services/studio[dev]' 'pyinstaller>=6.16,<7' 'numpy>=2,<3' 'scipy>=1.14,<2' 'tensorboard>=2.18,<3' 'requests>=2.32,<3'
```

Install matching `torch==2.8.0` and `torchaudio==2.8.0` wheels (use the official CPU wheel index on Windows/Linux, regular macOS wheels for MPS). Then:

```sh
python scripts/setup_model.py --skip-checkpoint
cd apps/web
npm ci
npm run build
cd ../..
git clone https://github.com/microsoft/vcpkg.git work/media/vcpkg
git -C work/media/vcpkg checkout 20ceb8adbf0504d4b0d5e4058a32f9d4fc896391
python scripts/build_release_media.py --triplet arm64-osx-songcut
python scripts/build_desktop_backend.py --ffmpeg work/media/installed/arm64-osx-songcut/tools/ffmpeg/ffmpeg --ffprobe work/media/installed/arm64-osx-songcut/tools/ffmpeg/ffprobe --media-build-record work/media/media-build.json
python scripts/smoke_desktop_backend.py apps/desktop/src-tauri/binaries/songcut-backend/songcut-backend
cd apps/desktop
npm ci
npm run build
```

Use `songcut-backend.exe` for the smoke check on Windows. Freezing is performed natively on each OS; cross-compiling the Rust shell does not cross-compile Python/native ML libraries. For official releases, both media executables come from the pinned vcpkg source build; select the native platform triplet and executable suffix. Both executables enter PyInstaller dependency collection, and the imageio wheel executable is excluded. The matching MediaSources companion is required; see [MEDIA-SOURCES.md](MEDIA-SOURCES.md). The development-only build without explicit media paths may use imageio/system binaries and must remain private until separately reviewed. The pinned Noto CJK font and its OFL license are downloaded during the build.

The standalone smoke check runs outside the checkout with a restricted PATH and no Python environment override. It checks real ML worker startup, synthetic audio feature extraction, two original training steps, checkpoint validation and inference, both media binaries, HTTP application identity, bundled frontend/help and graceful shutdown. Tauri also checks service identity before reusing port 8765, and only stops a backend it owns. Its private shutdown signal lets the service stop training jobs before exit.

## GitHub Actions

Run **Desktop release → Run workflow** with `release_tag` empty to build artifacts without publishing. A supplied version such as `v0.1.0` must match the desktop package/Cargo/Tauri versions and creates a prerelease at the tested commit only after the complete matrix passes. This can prepare and inspect a release while the repository remains private. Inspect all three build jobs and download their artifacts for native installation tests. Then push an approved `v*` version tag: the same matrix runs again, and only after all three builds and standalone checks pass does the publish job create a GitHub prerelease with installers, matching MediaSources archives and platform SHA256SUMS files. This workflow never changes repository visibility. Release assets are platform-specific; the Apple build targets `aarch64-apple-darwin`.

## Data and signatures

Application resources are immutable; user projects, imported models and training output live under:

- macOS: `~/Library/Application Support/VTuberSongCut`
- Windows: `%LOCALAPPDATA%/VTuberSongCut`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/VTuberSongCut`

`SONGCUT_DATA` overrides that location. Updating the application preserves this data. Source development keeps using the checkout's `var` directory.

The default macOS bundle uses ad hoc signing. No Apple Developer certificate/notarization or Windows Authenticode certificate is configured. Unsigned/ad hoc releases can trigger operating system security prompts. Test from an actual downloaded artifact before promoting a prerelease; the workflow does not disable Gatekeeper or SmartScreen. For trusted public distribution, configure Apple signing/notarization and Windows signing in a reviewed follow-up using repository secrets and the official Tauri signing guides.

Bundled `resources/THIRD_PARTY_LICENSES` contains package metadata, available dependency license files, AutoSlicer MIT, Noto OFL and exact FFmpeg/ffprobe version/license/build reports. `runtime-manifest.json` records every Python package version. FFmpeg includes GPL components, whose source and redistribution obligations apply independently. Each release ships its matching MediaSources archive with actual patched source, original source archives, recipes, patches, toolchain/build records and binary hashes. See [MEDIA-SOURCES.md](MEDIA-SOURCES.md); keep the notices and matching source companion available with redistributed installers.
