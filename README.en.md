<p align="center">
  <img src="assets/readme-banner.png" alt="拾歌 · VTuber SongCut — blue-and-white anime catgirl banner" width="100%" />
</p>

<h1 align="center">拾歌 · VTuber SongCut</h1>

<p align="center">
  <a href="README.md">简体中文</a> · <strong>English</strong>
</p>

<p align="center">Turn a livestream into songs worth keeping.</p>

<p align="center">
  <a href="https://github.com/Sakuranda/VTuberSongCut/releases">Download</a> ·
  <a href="#quick-start">Quick Start</a> ·
  <a href="docs/FAQ.md">FAQ</a> ·
  <a href="CONTRIBUTING.md">Contributing</a> ·
  <a href="LICENSE">MIT License</a>
</p>

## Introduction

VTuber SongCut is a workspace for turning VTuber recordings into individual song clips. Detect a target performer's singing, refine clip boundaries, add lyrics, and export video and audio. The app opens in the **clipping workspace**; a separate **model training workspace** lets you train a detector with your own recordings.

**Model training and singing detection are powered by [AutoSlicer-v2](https://github.com/anAtheist987/AutoSlicer-v2).** VTuber SongCut adds visual annotation, training management, editing, and export workflows, while maintaining a separate Apple Silicon compatibility patch.

## Features

| Feature | What it does |
| --- | --- |
| Singing detection | Find singing segments using a model trained for the target performer |
| Dedicated training workspace | Upload training and validation recordings, annotate, review, train, and use the resulting model |
| Precise clip editing | Drag timeline boundaries, enter timestamps, and adjust with I/O and arrow-key shortcuts |
| Lyric layouts | Import LRC lyrics with single-line, classic two-line, or alternating KTV layouts |
| Flexible exports | MP4 / MP3, H.264 / HEVC, resolution, frame rate, quality, and bitrate controls |
| Desktop or server | Use a personal desktop app or access a self-hosted workspace in a browser |

Supports MP4, FLV, MKV, MOV, WebM, and common audio formats. Recordings, models, and outputs stay on the machine running the application.

> Song titles and artists are currently edited manually, and lyrics are imported from local LRC files. Automatic song identification, online lyric lookup, and automatic word alignment are on the [roadmap](docs/ROADMAP.md). Word-level KTV highlighting requires existing word timestamps.

## Quick Start

### Desktop

Visit **[Releases](https://github.com/Sakuranda/VTuberSongCut/releases)** and download the package for your platform:

| Platform | Package |
| --- | --- |
| macOS 15+ · Apple Silicon | [Download DMG](https://github.com/Sakuranda/VTuberSongCut/releases/download/v0.1.0/VTuber.SongCut_0.1.0_aarch64.dmg) |
| Windows · x64 | [Download EXE](https://github.com/Sakuranda/VTuberSongCut/releases/download/v0.1.0/VTuber.SongCut_0.1.0_x64-setup.exe) |
| Linux · x64 | [Download AppImage](https://github.com/Sakuranda/VTuberSongCut/releases/download/v0.1.0/VTuber.SongCut_0.1.0_amd64.AppImage) / [Download DEB](https://github.com/Sakuranda/VTuberSongCut/releases/download/v0.1.0/VTuber.SongCut_0.1.0_amd64.deb) |

This preview is not Apple-notarized or Windows publisher-signed, so the first launch may show a system warning. Use the steps below only for installers downloaded from **this repository's Releases** and checked against the corresponding `SHA256SUMS` file.

#### First installation on macOS

1. Open the DMG, drag **VTuber SongCut.app** into **Applications**, then open the app.
2. For an unidentified-developer or Apple-cannot-check warning, go to **System Settings → Privacy & Security → Open Anyway** and confirm. See [Apple's instructions](https://support.apple.com/en-gb/102445).

Alternatively, after verifying the download's source, open Terminal, run this single command, and open SongCut again:

```sh
xattr -dr com.apple.quarantine "/Applications/VTuber SongCut.app"
```

This removes the download quarantine attribute only from **SongCut and its bundled files**; it does not sign or notarize the app. Replace the quoted path if you installed elsewhere. If permissions prevent the command, use the System Settings method above.

#### First installation on Windows

1. Double-click the downloaded `.exe` installer.
2. If SmartScreen displays **Windows protected your PC**, select **More info → Run anyway**, then follow the installer. See [Microsoft's documentation](https://github.com/MicrosoftDocs/windows-dev-docs/blob/docs/hub/apps/package-and-deploy/smartscreen-reputation.md).
3. If prompted to install **Microsoft Edge WebView2 Runtime**, stay online and complete that step.

If **Run anyway** is unavailable, or **Smart App Control** or organization policy blocks the app, the SmartScreen steps above do not apply. Contact your device administrator or access a [self-hosted server](#self-hosted-server) through your browser. See [Microsoft's Smart App Control FAQ](https://support.microsoft.com/en-us/windows/security/threat-malware-protection/smart-app-control-frequently-asked-questions).

> Stop and report the issue if there is an explicit malware detection or a checksum mismatch. These steps do not require globally disabling Gatekeeper, SmartScreen, or antivirus protection.

#### Start using SongCut

Install the app, import a recording, and select the performer's model. On first use, open **Model Training** to train your own detector or import a compatible model. Performer weights are not included in the installers; see the [model guide](docs/MODELS.md).

Desktop packages are built by GitHub Actions. Check the corresponding release notes for available versions, checksums, and signing status.

### Self-hosted server

With Docker, Docker Compose, and Git installed, run:

```sh
git clone https://github.com/Sakuranda/VTuberSongCut.git && cd VTuberSongCut
./scripts/deploy.sh
```

The script builds and starts the service, generates access credentials, and configures persistent storage. To use a domain, point its DNS record at the server, then run:

```sh
./scripts/deploy.sh --domain songs.example.com
```

Domain mode uses Caddy for HTTPS. See the [deployment guide](docs/DEPLOYMENT.md) for configuration, backups, and upgrades. The server provides a shared workspace for an individual or trusted team.

### Bilibili recording and low-resource servers

```sh
./scripts/deploy.sh --recorder
```

Add a room in the recording library. Completed recordings are imported automatically and open with a shared 320p preview; HD playback supports up to 1080p. Named accounts, personal editing copies, CPU inference and bounded export queues are available. See the [recording guide](docs/RECORDING.md) and [server configuration](docs/LOW-RESOURCE-SERVER.md).

### Development from source

See the [development guide](docs/DEVELOPMENT.md) for setup, desktop debugging, and test commands.

## Documentation

- [FAQ](docs/FAQ.md) — choosing models, annotation, and training basics
- [Model guide](docs/MODELS.md) — model sources, formats, and AutoSlicer usage
- [Server deployment](docs/DEPLOYMENT.md) — Docker, domains, and persistent data
- [Release maintenance](docs/RELEASING.md) — automated builds and publishing
- [Architecture](docs/ARCHITECTURE.md) · [Roadmap](docs/ROADMAP.md)

The detailed guides are currently written in Chinese. Documentation translations are welcome.

## Contributing

Contributions to the interface, model integrations, documentation, and bug fixes are welcome. Read the [Contributing Guide](CONTRIBUTING.md) before opening a pull request, and keep each change focused.

For bug reports, include your operating system, reproduction steps, and relevant logs in [Issues](https://github.com/Sakuranda/VTuberSongCut/issues).

## License

The project code is licensed under the [MIT License](LICENSE).

Thanks to [AutoSlicer-v2](https://github.com/anAtheist987/AutoSlicer-v2) for the training and singing-detection foundation. Third-party projects retain their copyrights and licenses; model weights, fonts, and recordings remain subject to their respective terms. See [Third-party Notices](THIRD_PARTY_NOTICES.md).
