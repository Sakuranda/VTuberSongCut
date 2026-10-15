# Media component release policy

Public desktop releases must build FFmpeg and ffprobe from controlled sources and publish the matching media source archive with the installers. This document defines the required workflow and release checks; it does not certify that a particular CI run or release has passed them.

Private prototype packages using imageio's bundled FFmpeg or a build host's FFprobe are not public release artifacts. They must not be promoted to a public release in place of the controlled build described here.

## Controlled build

The release workflow must use Microsoft vcpkg at commit `20ceb8adbf0504d4b0d5e4058a32f9d4fc896391`. The [pinned vcpkg tree](https://github.com/microsoft/vcpkg/tree/20ceb8adbf0504d4b0d5e4058a32f9d4fc896391) supplies the port recipes and patches. Record the resolved versions and source checksums for every selected port; a vcpkg commit alone is not a complete source archive.

FFmpeg's selected features must explicitly include GPL components, x264, x265, libass and LAME MP3 support, together with the required default codec and resampling features. Nonfree components must remain disabled. Retain the exact feature selection, target and host triplets, toolchain configuration and build commands used for each platform.

Both FFmpeg and ffprobe must come from this build. Packaging must select these files explicitly and avoid silently substituting an imageio wheel executable or a tool found on the build host's `PATH`. Audit the final application bundle for unintended extra copies of media executables and libraries.

The application's MIT license does not replace the media components' licenses. Include each component's applicable license and notices, and derive the media license record from the actual build's `-L` and configuration output. Review changes against [FFmpeg's legal guidance](https://ffmpeg.org/legal.html).

## Required source archive

Each platform's installer must be accompanied by a `MediaSources-<platform>` archive for that same release build. The archive must contain:

- The actual source trees used to build FFmpeg and its bundled dependencies, after all patches have been applied. Preserve original source archives and their checksums where required to reproduce or explain the patched trees.
- The exact vcpkg recipes, scripts, patches, triplets, manifests, configuration files and relevant toolchain files used by the build. Preserve the vcpkg revision and any local modifications.
- Build and installation instructions identifying required tools, versions, commands, selected features and configuration. Record any required external inputs explicitly.
- Applicable license texts, copyright notices and the dependency inventory corresponding to the bundled media components.
- A manifest connecting the release version and target platform to source versions, source checksums, build configuration and binary SHA-256 checksums.
- Build records, including FFmpeg and ffprobe `-version`, `-buildconf` and `-L` output, plus checksums for the final packaged media executables and collected libraries.

Hash the completed source archive and installer assets, and publish their SHA-256 values alongside the files. A link to a moving source branch, a generic download page or only the unmodified FFmpeg tarball does not satisfy this archive requirement.

## Release verification

Before publishing a platform's installer, the workflow and maintainer review must establish that:

1. The media build used the pinned vcpkg revision and recorded feature configuration, with no nonfree components or unrecorded source substitutions.
2. The source archive includes the patched source trees for all bundled media dependencies and the recipes, patches and instructions needed to rebuild them. Missing trees or required build inputs are release failures.
3. The packaged application uses the controlled FFmpeg and ffprobe builds, and its media functionality passes the platform's smoke checks. Recompute checksums after packaging, relocation or signing changes the files.
4. The archive manifest identifies the exact packaged media files and libraries; license texts and notices match those components.
5. The matching `MediaSources-<platform>` archive, checksums and build records are available with the installer in the same public release. Retain them together for future access.

Passing a media smoke check establishes execution behavior; it does not establish source completeness. Publishing the source archive establishes availability; its contents still require the checks above. A platform with missing sources, mismatched provenance or failed checks must remain unpublished until those issues are resolved.
