This macOS-only libass overlay copies the port, manifest and patch from Microsoft vcpkg commit 20ceb8adbf0504d4b0d5e4058a32f9d4fc896391.
It disables fontconfig and optional libunibreak auto-discovery, and explicitly enables native CoreText.
The change prevents preinstalled Homebrew fontconfig from leaking into libass metadata and the controlled FFmpeg dependency graph.
