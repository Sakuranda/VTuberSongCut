<p align="center">
  <img src="assets/readme-banner.png" alt="拾歌 · VTuber SongCut — 蓝白色猫娘主题横幅" width="100%" />
</p>

<h1 align="center">拾歌 · VTuber SongCut</h1>

<p align="center">
  <strong>简体中文</strong> · <a href="README.en.md">English</a>
</p>

<p align="center">从一场直播，拾起一首歌。</p>

<p align="center">
  <a href="https://github.com/Sakuranda/VTuberSongCut/releases">下载</a> ·
  <a href="#quick-start">快速开始</a> ·
  <a href="docs/FAQ.md">常见问题</a> ·
  <a href="CONTRIBUTING.md">参与贡献</a> ·
  <a href="LICENSE">MIT License</a>
</p>

## 项目介绍

拾歌是一款面向 VTuber 录播的歌切工作台：找到目标主播的演唱片段，调整切点、添加歌词，再导出视频与音频。默认进入**录播切歌**，**模型训练**拥有独立工作区，支持用自己的素材训练主播模型。

**模型训练与演唱检测基于 [AutoSlicer-v2](https://github.com/anAtheist987/AutoSlicer-v2)。** 拾歌在其基础上提供可视化标注、训练管理、剪辑与导出流程，并维护独立的 Apple Silicon 兼容补丁。

## 项目特性

| 特性 | 说明 |
| --- | --- |
| 自动寻找演唱片段 | 选择对应主播的模型，从录播中检测演唱区间 |
| 独立模型训练 | 上传训练与验证素材、标注、复核、训练，再将模型用于切歌 |
| 精细调整切点 | 时间轴拖动、时间戳输入、I/O 快捷键与方向键微调 |
| 多种歌词样式 | 导入 LRC，使用单行、经典双行或左右双行 KTV 布局 |
| 灵活导出 | MP4 / MP3、H.264 / HEVC、分辨率、帧率、画质和码率设置 |
| 两种使用方式 | 个人电脑桌面应用，或通过浏览器访问自托管服务器 |

支持 MP4、FLV、MKV、MOV、WebM 及常见音频格式。素材、模型与处理结果保存在运行拾歌的设备上。

> 歌名与歌手目前支持手动编辑，歌词支持本地 LRC 导入。自动识曲、在线歌词检索和自动逐字对齐仍在[路线图](docs/ROADMAP.md)中；KTV 逐字高亮需要已有词级时间信息。

## Quick Start

### 个人电脑

前往 **[Releases](https://github.com/Sakuranda/VTuberSongCut/releases)**，下载适合系统的安装包：

| 系统 | 安装包 |
| --- | --- |
| macOS 15+ · Apple Silicon | `.dmg` |
| Windows · x64 | `.exe` 安装程序 |
| Linux · x64 | `.AppImage` |

安装后打开拾歌，导入录播，在左侧选择主播模型即可开始。第一次使用时，可到**模型训练**工作区训练自己的模型，或导入已有兼容模型。安装包不附带主播模型权重；详见[模型使用说明](docs/MODELS.md)。

桌面发行包由 GitHub Actions 构建。发行版本、校验文件及签名状态以对应 Release 说明为准。

### 自托管服务器

服务器安装 Docker 与 Docker Compose 后，运行：

```sh
git clone https://github.com/Sakuranda/VTuberSongCut.git && cd VTuberSongCut
./scripts/deploy.sh
```

脚本会构建并启动服务，生成访问凭据并保存数据。需要通过域名访问时，先将域名解析到服务器，再运行：

```sh
./scripts/deploy.sh --domain songs.example.com
```

域名模式使用 Caddy 提供 HTTPS。配置、备份和升级方法见[服务器部署指南](docs/DEPLOYMENT.md)。服务器版适用于个人或可信团队共享工作区。

### 从源码开发

开发环境、桌面调试和测试命令见[开发指南](docs/DEVELOPMENT.md)。

## 文档

- [常见问题](docs/FAQ.md) — 模型选择、标注与训练入门
- [模型使用说明](docs/MODELS.md) — 模型来源、格式及 AutoSlicer 使用方法
- [服务器部署](docs/DEPLOYMENT.md) — Docker、域名与数据持久化
- [发行维护](docs/RELEASING.md) — 自动构建与发布流程
- [架构设计](docs/ARCHITECTURE.md) · [路线图](docs/ROADMAP.md)

## Contributing

欢迎提交问题、改进界面、完善模型适配与文档。开始前请阅读 [Contributing Guide](CONTRIBUTING.md)，并尽量让每个 Pull Request 聚焦一个清晰的改动。

遇到问题时，可在 [Issues](https://github.com/Sakuranda/VTuberSongCut/issues) 中附上系统信息、操作步骤和相关日志。

## License

本项目代码采用 [MIT License](LICENSE)。

感谢 [AutoSlicer-v2](https://github.com/anAtheist987/AutoSlicer-v2) 提供模型训练与演唱检测基础。第三方项目保留各自的版权与许可证；模型权重、字体和录播素材遵循其各自的使用条款。详见 [Third-party Notices](THIRD_PARTY_NOTICES.md)。
