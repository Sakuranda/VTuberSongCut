# B 站录制接入

拾歌通过独立的 **BililiveRecorder（录播姬）** 保存直播原始码流。录播姬关闭一个完整文件后，拾歌把它登记为共享录播，再通过自身的受限任务队列生成预览。文件登记与预览转码分开，不在 webhook 请求中执行转码。

本次适配固定录播姬 **2.20.0** 的 REST API 与 Webhook V2。该版本通过官方 GitHub stable release 核对，镜像为 `ghcr.io/bililiverecorder/bililiverecorder:2.20.0`。录播姬是独立第三方 GPL-3.0 项目，保留其许可证与来源，未把其源码复制进拾歌。

## Docker 部署

推荐使用部署脚本的一键入口：

```sh
./scripts/deploy.sh --recorder
```

脚本生成独立录播凭据并启动服务，然后执行 `python -m studio.recorder --configure-webhook` 自动配置回调。配置器只更新指向当前拾歌接收端的 URL，保留其他已有回调，并读取确认写入结果；重复执行不会重复添加。录播姬暂时未启动时有界重试，最终失败会明确报错，不会伪装已完成。

手工使用 Compose 时，先完成[服务器部署](DEPLOYMENT.md)，再在同一个 `.env.server` 中添加下列变量。账号、密码和 webhook 密钥独立于拾歌网页登录账号：

```dotenv
SONGCUT_RECORDER_USERNAME=songcut-recorder
SONGCUT_RECORDER_PASSWORD=替换为随机强密码
SONGCUT_RECORDER_WEBHOOK_SECRET=替换为至少32字符的随机密钥
SONGCUT_RECORDER_MAX_ROOMS=1
```

可使用 `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` 生成随机值。不要提交 `.env.server`。

```sh
docker compose --env-file .env.server -f compose.yaml -f deploy/compose.recorder.yaml up -d --build
```

录播数据位于持久化 `recorder-data` 卷。录播姬以读写方式挂载到 `/rec`，拾歌以**只读方式**挂载到 `/recordings`；拾歌不复制一份完整原片，也不删除录播姬源文件。录播姬管理接口默认只在 Docker 内网可见，没有公网管理端口。

手工 Compose 部署后执行自动配置命令：

```sh
docker compose --env-file .env.server -f compose.yaml -f deploy/compose.recorder.yaml exec -T songcut python -m studio.recorder --configure-webhook
```

若使用不兼容版本，配置器会拒绝猜测字段结构，此时可在录播姬全局设置中手工填写 **Webhook V2**：

```text
http://songcut:8765/api/recorder/webhook?token=<与SONGCUT_RECORDER_WEBHOOK_SECRET完全一致的值>
```

录播姬 v2.20.0 原生 webhook 只有 URL 设置，不提供请求签名或自定义请求头；这里采用独立随机 URL 密钥认证。不要把包含密钥的 URL 放到截图、公开日志、Git 或分享链接中。反向代理不要记录这个 webhook 的查询参数；默认应用已关闭访问日志。容器间使用内网地址；跨机器部署必须使用 HTTPS，并相应调整固定服务地址与挂载。

设置录播姬时，可以临时增加一个只绑定服务器回环地址的覆盖文件 `deploy/compose.recorder.local.yaml`（完成设置后移除该覆盖并重新启动）：

```yaml
services:
  recorder:
    ports:
      - "127.0.0.1:2356:2356"
```

使用三个 `-f` 启动，远程服务器通过 SSH 转发 `ssh -L 2356:127.0.0.1:2356 user@server` 后，在自己的浏览器打开 `http://127.0.0.1:2356` 登录录播姬。配置好 Webhook V2 后，用最初两个 `-f` 重新启动，恢复仅内网访问。房间添加、开关自动录制、手动开始/停止可从拾歌录制管理页面完成。

B 站可能要求有效账号 Cookie 才提供目标画质。Cookie 只在录播姬的设置中管理，不发送到拾歌前端，不写入仓库。能录到的清晰度以实际直播流和账号条件为准；默认 320p 是拾歌的网页预览，不是让录播姬只保存 320p。首版支持**已关闭的录制分段**编辑，不承诺对尚在写入的文件实时追播。

## 已有录播姬或源码部署

配置这些环境变量后启动拾歌：

| 变量 | 含义 |
| --- | --- |
| `SONGCUT_RECORDER_URL` | 固定录播姬 HTTP(S) 根地址，例如 `http://127.0.0.1:2356`；不设置则完全关闭接入 |
| `SONGCUT_RECORDER_USERNAME` / `SONGCUT_RECORDER_PASSWORD` | 录播姬 REST 账号密码，仅存服务端 |
| `SONGCUT_RECORDER_WEBHOOK_SECRET` | 32–256 字符独立随机密钥 |
| `SONGCUT_RECORDER_ROOT` | 拾歌可读取的录播姬工作目录绝对路径；对应 webhook 的 RelativePath |
| `SONGCUT_RECORDER_MAX_ROOMS` | 经拾歌启用的录制房间上限，默认 1 |
| `SONGCUT_RECORDER_QUEUE_LIMIT` | 完成文件的待导入队列上限，默认 32 |
| `SONGCUT_RECORDER_DISK_RESERVE_MB` | 录播目录所在磁盘保留空间，默认 1024 MiB |
| `SONGCUT_RECORDER_CALLBACK_ORIGIN` | 自动配置器回调根地址，Docker 默认 `http://songcut:8765`；不含路径、账号、查询参数 |

配置变更需重启拾歌。使用固定的后台地址而非接受前端任意 URL；REST 请求不跟随跳转、不使用系统 HTTP 代理。多个 API 进程共用同一数据目录暂不支持；使用一份拾歌应用进程，后台重任务由应用队列管理。

## 可靠性与权限边界

- 只处理 Webhook V2 `FileClosed`。开始、结束等其他事件安全忽略，事件乱序不会造成提前读取未完成文件。
- 先检查独立密钥，再读取最多 64 KiB 的事件。只放行精确的 webhook POST 路径；其余录制控制走拾歌管理员认证和会话保护。
- `EventId` 去重落入 SQLite；相同 ID 改变内容会被拒绝。相同文件重复通知只生成一个录播，录播 ID 同时作为确定的项目 ID，重启后导入回调复用已有项目。
- 拒绝绝对路径、`..`、目录联接与符号链接。仅接受指定根目录内的 FLV/MP4/MKV/TS，校验完成事件的文件大小，并在导入前再次确认大小、修改时间与文件身份稳定。
- 相同相对路径不能作为另一场新录像再次登记。必须让录播姬使用包含时间/场次的唯一文件名；不要覆盖或修改已归档的文件。
- 导入队列有上限；满时返回 429。录播姬自身最多重试三次，持续满队列可能漏掉通知，源文件仍在录播目录。需要修复资源问题后重发该完成事件或从原文件手动导入，不宣称无限可靠投递。
- 暂时无法读取的文件自动最多尝试 3 次；失败后可从拾歌重试，总尝试最多 6 次。重启会继续处理已登记但未完成的导入。
- 不删除源录播。启用自动录制、手动开始前检查录制目录所在磁盘，而不只检查应用数据盘；后台周期检查，低于保留空间或磁盘不可读时关闭自动录制、尝试正常停止正在录制的文件。持久状态提示暂停原因；恢复空间后必须由管理员主动启用，不会静默重启录制。原文件始终保留，源文件保留策略仍需配置。
- 磁盘检查间隔目标为 30 秒，由后台导入循环调度；磁盘突然写满、挂载失效或控制接口不可用仍可能导致当前文件不完整，界面会显示无法停止的告警，不能保证毫无丢帧。
- 经拾歌的房间操作受上限限制；直接操作录播姬管理界面不经过这个限制，因此管理接口只供受信任管理员使用。

## 验证范围

已增加真实 FFmpeg 录制文件完成事件 → 共享项目 → 自动 320p 预览 → 有权限用户创建自己的草稿链路测试，验证原始文件 SHA-256 未改变、重复事件不会重复项目和预览。录制模块与流程测试共 18 项通过；训练测试也在允许进程检查的环境中复核通过。测试覆盖官方 REST 请求格式（真实本机 HTTP 模拟服务）、Optional 配置结构与回调保留、重复配置、低磁盘禁止开始和停止自动录制、账号传递、房间数限制、事件认证、请求大小、重复事件、乱序、路径逃逸、符号链接、文件不完整、重启恢复、队列满及有界重试。未使用用户 Cookie，也未声称完成真实 B 站房间的长时间直播录制测试。部署后应使用有权限访问的一间真实直播间验收断流重连、文件完成通知和预览生成。

官方参考：[项目及许可证](https://github.com/BililiveRecorder/BililiveRecorder)、[2.20.0](https://github.com/BililiveRecorder/BililiveRecorder/releases/tag/v2.20.0)、[Docker](https://rec.bilibili.stream/install/container/)、[REST](https://rec.bilibili.stream/reference/api/rest/)、[Webhook V2](https://rec.bilibili.stream/reference/webhook/)。
