# 服务器部署（Docker）

拾歌提供独立的浏览器服务器模式。服务器安装 Docker Engine 与 Docker Compose v2 后，在项目源码目录执行一条命令即可构建并启动；宿主机无需安装 Python、Node.js、FFmpeg 或 PyTorch。桌面安装包另见项目首页的 Releases 入口。

```sh
./scripts/deploy.sh
```

首次构建需要联网下载基础镜像、前端依赖、固定版本的 AutoSlicer 源码及 CPU 版 PyTorch，可能耗时数分钟。服务启动并通过健康检查后，命令显示 `http://localhost:8765` 和随机生成的初始账户。浏览器打开网址后使用该账户登录。

默认仅映射服务器的 `127.0.0.1:8765`。远程试用可使用 SSH 转发，然后在自己电脑打开同一网址：

```sh
ssh -L 8765:127.0.0.1:8765 user@your-server
```

## 域名与自动 HTTPS

将域名的 A/AAAA 记录指向服务器，允许外部访问 80/443 端口，然后执行：

```sh
./scripts/deploy.sh --domain songs.example.com
```

也可设置环境变量 `SONGCUT_DOMAIN=songs.example.com` 后运行脚本。脚本会启动 Caddy 反向代理，自动申请和续期 HTTPS 证书，工作台地址为 `https://songs.example.com`。应用的 8765 端口仍仅映射本机。域名需要支持公开的证书验证；内网域名请自行配置已有反向代理及证书。

已经启动过的安装也可以运行上述命令添加或更换域名，现有账户与项目继续使用。Caddy 的配置位于 [`deploy/Caddyfile`](../deploy/Caddyfile)，域名服务的 Compose 配置位于 [`deploy/compose.domain.yaml`](../deploy/compose.domain.yaml)。

## 账户、访问边界与运行设备

这是**一个共享工作区**，适合自己或受信任的小团队使用同一账户。所有登录用户共用项目、素材、模型和训练任务；没有独立租户、角色隔离或按用户计费。

服务器模式为界面、会话接口和全部项目、媒体、下载、模型、训练接口启用 HTTP Basic 登录。浏览器原生的视频请求、分段加载和下载会沿用登录凭据。修改操作还需要工作台会话令牌；请求来源和 Host 必须位于明确的允许列表中。只有 `/api/health` 不要求登录，用于容器健康检查。

脚本将账户和域名配置保存在被 Git 忽略的 `.env.server`，文件权限为 `0600`。首次成功启动会显示初始密码，请保存在自己的密码管理器中。重新运行脚本会继续使用现有账户；修改密码请编辑该文件并重新运行部署命令。不要提交该文件，不要将带密码的网址或完整配置复制到公开问题中。HTTP Basic 凭据需通过 HTTPS 或上述 SSH 隧道传输。

镜像包含 FFmpeg、FFprobe、中日韩字体、AutoSlicer 检测引擎与训练所需的 CPU 依赖。默认不下载或附带任何声线权重：导入自己已有的兼容 `.pt/.pth` 模型，或创建训练集训练自己的模型。上游官方模型的来源与适用范围见[模型说明](MODELS.md)。首次检测前必须在界面中选择声线模型。

默认构建使用 CPU 版 PyTorch，检测和训练均可使用 CPU；较长录播或大训练集会耗时较长。服务器镜像不包含 CUDA，不能通过选择界面中的 CUDA 直接获得 GPU 加速。此部署的自动验收目标为 Linux x86_64 Docker；其他架构需要检查对应 PyTorch CPU wheel 的可用性。

## 数据、维护与自建反向代理

项目数据库、素材、预览、导出、模型和训练数据均保存于 Compose 命名卷 `songcut_songcut-data`，挂载到容器 `/data`。替换镜像或重启服务保留这些数据。Caddy 的证书保存在独立命名卷中。

更新源码后再次运行同一条 `./scripts/deploy.sh` 即可重新构建并替换服务。停止服务：

```sh
# 默认部署
docker compose --env-file .env.server down
# 含域名代理的部署
docker compose --env-file .env.server -f compose.yaml -f deploy/compose.domain.yaml down
```

不要在上述命令后添加 `--volumes`，除非明确需要删除全部数据。备份时先停服务，再备份 `songcut_songcut-data` 命名卷和 `.env.server`；恢复至同一卷后启动即可。素材和训练缓存可能占用较多磁盘空间，应为该卷预留容量。

若已有 Nginx/Caddy，可仅运行默认部署并将其反向代理至 `127.0.0.1:8765`。在 `.env.server` 中设置 `SONGCUT_PUBLIC_ORIGIN=https://你的域名` 后重新运行部署命令；转发时保留原始 `Host` 和 `Authorization` 头，并保留媒体 Range 请求。不得设置通配来源或直接将无 HTTPS 的 Basic 登录暴露到公网。

需要修改本机映射端口时，同时修改 `.env.server` 中的 `SONGCUT_HTTP_PORT` 和 `SONGCUT_PUBLIC_ORIGIN`。应用内部端口固定为 `8765`。`SONGCUT_BIND` 默认 `127.0.0.1`，通常无需更改。

查看容器状态与日志：

```sh
docker compose --env-file .env.server ps
docker compose --env-file .env.server logs --tail 100 songcut
```

域名模式下的完整管理命令需附带 `-f compose.yaml -f deploy/compose.domain.yaml`。如果 Caddy 证书申请失败，检查域名解析、防火墙和 80/443 占用，再查看 Caddy 日志。应用服务拒绝启动时，检查账户是否为空、密码是否少于 16 字符，以及 `SONGCUT_PUBLIC_ORIGIN` 是否为一个明确的 HTTP(S) 来源。

GitHub 的 **Server Docker** 工作流会实际构建并启动容器，验证登录保护、会话与写入保护、前端页面、CPU 模型环境、FFmpeg、媒体上传与 Range 读取，以及重启后的项目持久化。它不发布服务器镜像；服务器通过源码构建使用。
