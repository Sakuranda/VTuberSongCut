# Contributing to VTuber SongCut

Thank you for helping improve 拾歌. Contributions are welcome in English or Chinese. For detailed development instructions in Chinese, see the [development guide](docs/DEVELOPMENT.md).

## Before you start

For bug reports, include reproduction steps, expected and actual behavior, your operating system, and relevant logs with private information removed. For a substantial feature or architecture change, open an issue first so its scope and acceptance criteria can be discussed.

Useful starting points:

- [Architecture and module contracts](docs/ARCHITECTURE.md)
- [Roadmap and acceptance criteria](docs/ROADMAP.md)
- [Model formats and training](docs/MODELS.md)
- [HTTP API contract](packages/contracts/openapi.json)

## Set up a development environment

Use Python 3.12 and Node.js 22 or later. Run these commands from the repository root. On Windows, use `.venv\Scripts\python.exe` in place of `.venv/bin/python`.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e 'services/studio[dev]'
npm --prefix apps/web ci
```

This is sufficient for the core backend checks and web development. To run the application, build the web interface and start the local service:

```sh
npm run web:build
.venv/bin/python scripts/dev.py
```

Open <http://127.0.0.1:8765>. Media processing requires FFprobe; detection and training additionally require the upstream model environment. See the [development guide](docs/DEVELOPMENT.md) for installation, desktop development, environment variables, and integration checks.

## Validate your changes

Run the checks relevant to your change. The core checks used in CI are:

```sh
.venv/bin/python -m pytest services/studio/tests -q
npm --prefix apps/web test
npm run web:build
```

For desktop changes, install the desktop dependencies and run:

```sh
npm --prefix apps/desktop ci
cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml
```

Changes affecting media processing or model training should also run the corresponding [integration checks](docs/DEVELOPMENT.md#integration-checks). Add focused regression coverage when behavior changes. Synthetic test inputs validate execution paths; they do not establish detection quality on real recordings.

## Keep changes reviewable

- Keep each pull request focused on one problem. Describe the user-visible result, the checks you ran, and any remaining limitations.
- Preserve the distinction between singing detection, song recognition, lyrics, and alignment. Describe implemented capabilities accurately and retain manual correction where results are uncertain.
- Keep the training workspace, dataset preparation, and model library in the main repository. Upstream AutoSlicer compatibility changes belong in a reproducible patch with the pinned source recorded in `packages/contracts/model-source.json`.
- Update relevant documentation and API contracts when behavior changes. Keep examples portable and remove local machine paths and session-specific reports.
- Do not commit virtual environments, `upstream/` checkouts, `var/` data, model weights, recordings, generated builds, credentials, or copyrighted fonts. Use small synthetic fixtures when possible and document any separately obtained assets.

## License

By submitting a contribution, you agree that it is provided under this repository's [MIT License](LICENSE). Only contribute code and assets you have the right to share. Third-party models, fonts, recordings, and other assets retain their own applicable terms.
