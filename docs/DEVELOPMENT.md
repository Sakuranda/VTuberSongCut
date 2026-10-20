# 开发指南

本指南面向修改拾歌代码、调试模型环境或运行完整集成检查的开发者。日常使用入口见 [中文 README](../README.md)，贡献约定见 [CONTRIBUTING](../CONTRIBUTING.md)。以下命令均从仓库根目录运行，另有说明除外。

## 环境与安装

建议使用 Python 3.12、Node.js 22+ 和 Git。Apple Silicon 上使用原生 arm64 Python。后端核心支持 Python 3.11+；完整模型依赖与桌面运行需要另行验证相应平台环境。Windows 下将 `.venv/bin/python` 替换为 `.venv\Scripts\python.exe`。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e 'services/studio[dev]'
npm --prefix apps/web ci
```

核心测试和 Web 构建不需要下载模型。媒体探测需要系统可执行的 FFprobe；macOS 可运行 `brew install ffmpeg` 安装。导出默认使用 `imageio-ffmpeg` 提供的 FFmpeg，替换版本时需确认其包含所需编码器和字幕用的 libass。

检测与训练还需准备固定版本上游和模型依赖：

```sh
.venv/bin/python scripts/setup_model.py
.venv/bin/python -m pip install -e 'upstream/AutoSlicer-v2[train]'
```

安装脚本会克隆 [来源清单](../packages/contracts/model-source.json) 指定的提交、应用 [Apple Silicon 补丁](../patches/0001-apple-silicon.patch)，并下载、校验预配置模型。已有 `upstream/AutoSlicer-v2/` 时脚本拒绝覆盖；请先检查现有克隆及其修改。`requirements-macos-tested.lock` 记录已测 Mac 环境的依赖版本，不是跨平台锁文件。

## Web 开发

先构建界面并启动本机 API：

```sh
npm run web:build
.venv/bin/python scripts/dev.py
```

服务地址为 <http://127.0.0.1:8765>，默认仅监听本机。修改前端时，在另一个终端运行：

```sh
npm run web:dev
```

打开 Vite 输出的地址；Vite 将 `/api` 代理到本机服务。前端开发服务器和 API 需要同时运行。修改 Python 代码后重启 API 服务。

## 桌面开发

桌面壳使用 Tauri，需要 Rust 与操作系统对应的开发工具链；macOS 需要 Xcode Command Line Tools。先完成 Web 构建和本机服务依赖安装，再运行：

```sh
npm --prefix apps/desktop ci
npm run desktop:dev
```

桌面壳会通过健康检查复用已运行的本机服务，或用仓库 `.venv` 启动服务；退出时只清理自己启动的进程。修改前端后需重新构建 Web 资源。当前桌面配置依赖仓库环境，尚未完成独立安装包所需的 Python 侧车打包、签名、公证与更新流程；`desktop:build` 不代表可直接分发。

## 配置

[`.env.example`](../.env.example) 是配置参考；服务不会自动读取 `.env`。启动前通过终端导出变量，例如：

```sh
export SONGCUT_DEVICE=cpu
export SONGCUT_DATA="$PWD/var"
.venv/bin/python scripts/dev.py
```

| 变量 | 用途与默认行为 |
| --- | --- |
| `SONGCUT_DATA` | 运行数据目录，默认 `var/` |
| `SONGCUT_UPSTREAM` | AutoSlicer 克隆，默认 `upstream/AutoSlicer-v2/` |
| `SONGCUT_CHECKPOINT` | 预配置检查点，默认数据目录下 `models/best.pt` |
| `SONGCUT_DEVICE` | 默认设备，默认 `auto`；支持情况由模型环境决定 |
| `SONGCUT_MODEL_PYTHON` | 独立模型进程的解释器，默认使用服务当前解释器 |
| `SONGCUT_FFMPEG` | 覆盖默认 FFmpeg 可执行程序 |
| `SONGCUT_FFPROBE` | FFprobe 可执行程序，默认从 `PATH` 查找 `ffprobe` |
| `SONGCUT_FONT_FILE` | 自定义字幕字体文件 |
| `SONGCUT_FONT_FAMILY` | 与字体文件匹配的字体族名称 |

项目中选择的模型和设备用于该项目的检测任务。配置自定义路径时建议使用绝对路径，避免启动目录变化产生歧义。

### 字幕字体

macOS 默认寻找 `Hiragino Sans GB`；Windows/Linux 提供对应字体候选路径，但仍需在目标系统检查实际导出。若使用自定义字体，请同时设置 `SONGCUT_FONT_FILE` 和 `SONGCUT_FONT_FAMILY`，并确认字体覆盖所需字符。字体文件不随仓库分发。

浏览器字幕为行级预览；最终字体、布局和高亮以 FFmpeg 导出成片为准。修改字幕渲染后应查看真实成片，检查缺字、换行、边距和时间偏移。

## 核心检查

```sh
.venv/bin/python -m pytest services/studio/tests -q
npm --prefix apps/web test
npm run web:build
```

这些检查对应 [CI 配置](../.github/workflows/ci.yml) 中的后端与 Web 验证。桌面修改另行运行：

```sh
cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml
```

<a id="integration-checks"></a>
## 集成检查

以下检查需要 FFprobe、可用 FFmpeg、固定上游及模型依赖。媒体检查还需要已准备的 `var/models/best.pt`；结果写入被 Git 忽略的 `var/smoke/`。

```sh
.venv/bin/python scripts/smoke_media.py
.venv/bin/python scripts/smoke_training.py
```

媒体检查生成合成 MP4/FLV，验证上传、预览代理、检测、编辑、LRC、导出和下载，并保存字幕预览图。训练检查在临时工作区准备合成音频，执行短训练、验证、模型登记、下载及检测，结束后清理临时模型与数据。

还可单独启用默认跳过的真实训练 CLI 测试：

```sh
SONGCUT_TRAINING_SMOKE=1 .venv/bin/python -m pytest services/studio/tests/test_training.py -q
```

合成检查只证明程序链路和输出契约，不衡量真实主播的误切、漏歌或歌曲边界质量。模型质量验收需要独立真实录播与人工标注，且训练、验证与验收素材应分开。设备诊断、模型格式及直接训练命令见 [模型说明](MODELS.md)。

## 代码与数据边界

| 路径 | 职责 |
| --- | --- |
| `apps/web/` | React 工作台、切歌与训练界面 |
| `apps/desktop/` | Tauri 桌面壳及服务进程生命周期 |
| `services/studio/studio/` | 本机 HTTP API、领域规则、存储、媒体适配、模型与训练任务 |
| `services/studio/tests/` | 后端核心与任务回归测试 |
| `packages/contracts/` | HTTP 契约、固定模型来源与校验信息 |
| `patches/` | 对固定上游版本的可复现兼容补丁 |
| `scripts/` | 本机启动、模型准备与集成检查 |
| `upstream/` | 被忽略的独立上游克隆 |
| `var/` | 被忽略的项目、模型、训练数据与导出产物 |

时间统一使用源录播绝对毫秒；导出时再换算为片段相对时间。训练 GUI 和通用数据准备保存在拾歌主仓库，上游只承载兼容修改。设计说明见 [架构与模块契约](ARCHITECTURE.md)，规划及验收边界见 [路线图](ROADMAP.md)。

## 构建文件清理

`libtorch_cpu.dylib` 是 PyTorch 的运行库，不是主播模型。源码虚拟环境、打包暂存目录和桌面调试／发布目录各自可能有一份；符号链接会显示相同名字，但不额外占完整库的空间。不要按文件名全盘删除。

从仓库根目录执行（Windows 使用 `python` 替换 `python3`）：

```sh
# 只列出清理范围与文件大小，不修改文件
python3 scripts/clean.py

# 关闭桌面程序并停止构建后，清理可重新生成的中间文件
python3 scripts/clean.py --apply

# 可选：同时清理打包暂存的完整后端，下次打包前需要重新生成
python3 scripts/clean.py --apply --include-packaged-backend
```

默认仅清理 `work/desktop-build/` 内指定的 PyInstaller 中间文件、资源副本、媒体工具输入副本，以及 `target/debug/backend` 和 `target/release/backend`。执行前检查桌面／打包进程；无法检查或发现可能占用的进程时拒绝清理。使用固定路径白名单，不接受任意删除路径，也不沿链接路径进入其他目录。清理期间不要同时启动新的桌面程序或构建。

保留 `.venv`、`var`（录播、标注、模型、任务、导出）、`upstream`、Web 构建产物、安装包、Rust 编译缓存和受控 FFmpeg 编译缓存。大小按文件统计，实际释放空间可能受文件系统克隆、压缩等影响。

`build_desktop_backend.py` 成功完成打包后会自动清理自身中间文件，保留可分发的后端。排查构建问题可传 `--keep-build-work`；失败的构建保留现场，可在停止构建后用上述命令清理。`--skip-freeze` 专门准备构建输入，因此保留这些输入。

运行期间，上传失败目录、预览转换中间文件及正常退出的临时工作区已有异常清理。此工具仅处理构建文件，不自动清空用户任务、已生成预览或崩溃后无法确认归属的媒体；没有按文件名或文件年龄删除用户素材的机制。
