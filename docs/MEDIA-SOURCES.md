# Release media sources and build records

Official desktop releases build FFmpeg and ffprobe from source using the checked-in `packaging/vcpkg.json`, the selected `packaging/triplets/*-songcut.cmake`, and Microsoft vcpkg commit `20ceb8adbf0504d4b0d5e4058a32f9d4fc896391`. The pinned port selects FFmpeg 9.0.2. The manifest enables the core libraries, FFmpeg/ffprobe applications, libass, LAME MP3, GPL, x264 and x265. It does not enable the `all` or `nonfree` features. The build checks required subtitle/H.264/HEVC/MP3 capabilities and rejects `--enable-nonfree`.

The application remains MIT licensed. These FFmpeg executables include GPL components and retain their own licenses. The release includes a separate `MediaSources-{platform}.tar.gz` next to each platform installer; keep that companion available whenever redistributing the matching installer. The previous local prototype used prebuilt media tools and is not an approved public binary release.

Each source companion contains:

- The actual extracted, patched source trees retained under vcpkg `buildtrees/<port>/src`, including FFmpeg and its installed library dependencies. Compiled objects and build output directories are excluded.
- Original source archives named in vcpkg's source-extraction logs, with their SHA-256 values. Downloaded bootstrap/tool binaries are not copied wholesale.
- An exact `git archive` snapshot of the pinned vcpkg revision, including ports, applied patch files, scripts and builtin triplets, plus this project's manifest/custom triplets and build/export scripts.
- The build invocation, environment/toolchain records, configure/build logs, installed package status and installed `share` metadata/copyright files.
- `SOURCE-AND-BINARY-MANIFEST.json`, which records both the originally built and frozen FFmpeg/ffprobe SHA-256 values and complete version/configuration output. Freezing can rewrite loader paths or ad hoc signatures, so both hashes are retained.

The source export fails if a required library source tree, an installed library's source coverage, a recorded original source archive or either frozen binary is missing. Recipe-only vcpkg helper packages are identified separately. The release publisher requires all three platform source companions and validates the platform artifact checksums before creating a prerelease.

## Rebuild the media tools

Use the same native platform/toolchain recorded in the companion. Windows requires Visual Studio C++ build tools; Linux requires a C/C++ toolchain, autoconf/automake/libtool/pkg-config and NASM; macOS requires Xcode command line tools plus autoconf/automake/libtool/pkg-config and NASM. vcpkg downloads its pinned auxiliary tools when necessary.

From a VTuberSongCut source checkout, or from the extracted companion's `recipes/project` directory (which preserves the `scripts`/`packaging` layout needed by the media builder):

```sh
git clone https://github.com/microsoft/vcpkg.git work/media/vcpkg
git -C work/media/vcpkg checkout 20ceb8adbf0504d4b0d5e4058a32f9d4fc896391
python scripts/build_release_media.py --triplet arm64-osx-songcut
```

Select `x64-windows-songcut` or `x64-linux-songcut` on the corresponding system. All custom triplets build static media libraries and release configurations; Windows uses the dynamic Microsoft CRT. The source trees and toolchain records support rebuilding and auditing the exact inputs. This does not promise byte-identical output across different compilers, SDKs or signing operations, and the rebuild may require network access for the pinned vcpkg Git objects/bootstrap tools. The archive also supplies the patched preferred source and original archives directly for inspection or an independently configured build.

The resulting executables are under `work/media/installed/<triplet>/tools/ffmpeg`. Freeze the backend with both explicit executable paths and its build record:

```sh
python scripts/build_desktop_backend.py --ffmpeg PATH/ffmpeg --ffprobe PATH/ffprobe --media-build-record work/media/media-build.json
python scripts/smoke_desktop_backend.py apps/desktop/src-tauri/binaries/songcut-backend/songcut-backend
python scripts/export_media_sources.py --label macOS-AppleSilicon
```

On Windows the executable names end in `.exe`. The release freeze overrides the imageio-ffmpeg hook so the wheel's precompiled executable is excluded, while retaining its Python wrapper. `IMAGEIO_FFMPEG_EXE` points the wrapper at the controlled bundled FFmpeg. Both controlled executables are submitted to PyInstaller's native dependency collection.

The CI cache restores installed media only together with its matching patched source trees and extraction logs. Standalone vcpkg binary caches are disabled. Build output directories are pruned only after the source companion has been written. License/source packaging is a concrete engineering record; distributors remain responsible for reviewing applicable component terms and their own distribution requirements.
