# 模型、原项目与训练

## 本机原项目在哪里

这台开发机器上的拾歌仓库是 `/path/to/VTuberSongCut`。原项目完整克隆在：

```text
/path/to/VTuberSongCut/upstream/AutoSlicer-v2
```

这是独立的 Git 仓库，当前分支为 `feat/apple-silicon`；保留原项目历史，并加入 Apple Silicon 兼容补丁。不是另一个带训练界面的应用。它的说明在 [原项目 README](https://github.com/anAtheist987/AutoSlicer-v2#readme)，训练入口是 `src/train.py`，推理入口是 `src/infer.py`。

当前共用拾歌根目录的 Python 环境 `.venv`，官方阿梓检查点放在根目录的 `var/models/best.pt`，不在上游目录内。主仓库保存 [固定来源与 SHA-256](../packages/contracts/model-source.json) 和 [兼容补丁](../patches/0001-apple-silicon.patch)；忽略原项目克隆、模型、录播及训练产物。

## 在拾歌里选择或导入模型

切歌工作区提供模型与运行设备选择；独立的模型训练工作区提供训练流程和已有模型导入入口。官方阿梓模型会列为预置模型；先选择模型与设备，再运行检测。选择保存在各自项目中；未选择模型时不会自动拿阿梓模型处理其他主播。

1. 打开或新建录播项目。
2. 在模型选择处选择预置阿梓模型，或先到“模型训练 → 我的模型”导入自己的兼容模型。
3. 选择运行设备并保存项目；这台 Apple Silicon Mac 可用 `mps`，也可用 `cpu`；`auto` 由运行环境选择可用设备。
4. 运行检测，检查并微调歌曲片段，再通过拾歌导出。

导入接受 `.pt` / `.pth`，但扩展名相同并不代表模型兼容。必须是 AutoSlicer 格式的检查点，包含模型参数和建模配置；目前支持 log-mel 输入的 CRNN / TCN，输出为二分类，或多分类中第 1 类代表“目标主播唱歌”。不支持 PANNs / embedding 输入、任意 PyTorch 模型或 RVC 音色模型。声线名称是人工说明，程序不会从权重可靠推断主播身份。

导入时，服务在独立模型 Python 进程中以 `weights_only=True` 读取检查点，检查配置、参数及模型是否可加载；失败会显示原因。可通过 `SONGCUT_MODEL_PYTHON` 指向另一个已安装模型依赖的 Python 环境，默认使用当前环境。每次检测任务记录模型名称、文件 SHA-256 与运行设备，便于追溯结果。

**导入是加载已训练好的检测器，不是训练或录入声纹。** 上传几段自己的声音或 RVC 权重不会把阿梓检测器变成自己的检测器。检测回答“这段时间是否是目标主播唱歌”，不会识别歌名、原唱或歌词，也不会改变声音。

## 直接使用原项目检测

下面命令从拾歌根目录运行，使用已准备好的环境、模型和本地录播。把输入路径改成自己的真实文件：

```sh
cd /path/to/VTuberSongCut
.venv/bin/python upstream/AutoSlicer-v2/src/infer.py \
  --device mps \
  --input "/绝对路径/录播.flv" \
  --checkpoint var/models/best.pt \
  --out-dir var/manual-inference
```

输出包括 `录播.segments.json` 的时间区间和 `录播.probs.npy` 的逐帧概率。输入是阿梓以外的主播时，应换用针对该主播训练的兼容检查点；官方阿梓检测器不代表通用主播模型。

末尾加 `--cut-video` 可让原项目同时切视频。它调用 FFmpeg 流复制，切点受关键帧影响，不能保证逐帧精确；需要校正边界、字幕和重新编码导出时，使用拾歌工作台。`--device cpu` 可改为 CPU；明确请求不可用设备会报错。原项目还支持 B 站链接，参数与下载说明见其 README。

## 在拾歌训练自己的主播检测器

训练界面和通用数据准备都在拾歌；底层调用 AutoSlicer 原有训练程序。原项目本身没有训练 GUI，其独立 Git 提交只承担 Apple Silicon 兼容。新手先看 [训练常见问题](FAQ.md#training)。

1. 从默认的切歌工作区进入独立训练工作区，创建数据集，填写目标演唱者。
2. 上传真实录播，分别分到训练集和验证集。至少各有一个独立素材，且每组都包含目标演唱和非目标演唱。按整场录播分组；不要把同一场剪成两份分别放入两组。
3. 标出“目标演唱”以及“其他人演唱/原唱播放”的区间，完整复核每个素材后确认。确认后未标注部分也作为非目标演唱；漏标的目标歌会被错误地用于训练。
4. 选择设备并开始训练。拾歌生成特征、逐帧标签与清单，再运行原版 CRNN 的二分类训练。可查看进度、日志和验证结果，或取消任务。
5. 成功训练并通过权重检查后，模型登记进模型库，来源为“训练”。回到切歌工作区，在项目中选择该模型，再检测一场未参与训练或验证的录播。

模型库区分预配置、导入、训练三种来源。阿梓预配置模型只适合相应目标，用作现成参考，不会自动适配任意主播。自己的训练结果也只反映给定素材和标注：验证分数不等于真实歌切质量保证，仍需检查误切、漏歌与边界。

没有自己的真实素材与标注，不能完成自己的主播模型训练。**当前尚未用用户真实标注录播训练或评估新主播模型**；合成输入的两步 MPS 训练与完整 HTTP 流程检查只验证执行链路。

训练数据和任务产物保存在根目录 `var/training/`；任务保存数据集与设置快照。服务重启后运行中的任务标为中断，需要重新发起。当前按“目标演唱 / 其余”训练，标记其他歌手用于明确负例，不另行训练歌手身份分类或歌名识别。

## 高级：直接使用原项目训练

不通过拾歌界面时，需要自行准备特征、标签与训练/验证清单。原项目 `scripts/cache_fullband.py`、`scripts/gold_azusa.py` 和 `scripts/build_v2_labels.py` 是参考脚本，包含写死的主播目录、录播编号和既有缓存假设，直接运行不会生成自己的可用数据集。`gold_azusa.py` 读取带 `Name` / `Start` / `Duration` 列的制表符分隔标注，并依赖已有负例清单。拾歌训练工作区已经在主仓库完成通用数据准备，无需修改这些原项目脚本。

准备好四类标签及 `data/processed/v2_train.json`、`data/processed/v2_val.json` 后，下面是本机 MPS 训练与验证命令。注意先进入原项目，再使用根目录的共享环境：

```sh
cd /path/to/VTuberSongCut/upstream/AutoSlicer-v2
../../.venv/bin/python src/train.py \
  --device mps --model crnn --run-name my_streamer_mps \
  --classes 4 --batch-size 2 --num-workers 0 \
  --manifest data/processed/v2_train.json \
  --val-manifest data/processed/v2_val.json \
  --steps 8000 --val-every 1000

../../.venv/bin/python scripts/eval_dump.py \
  --device mps --checkpoint runs/my_streamer_mps/best.pt \
  --val-manifest data/processed/v2_val.json \
  --out-dir runs/my_streamer_mps/val_probs
```

该示例要求第 1 类为目标主播唱歌，其他类别沿用原项目四类定义；只有二分类标签时应采用相应二分类配置，不能仅把 `--classes` 改成 4。训练完成后，将 `upstream/AutoSlicer-v2/runs/my_streamer_mps/best.pt` 导入拾歌，再用未参与训练的真实录播检查。

MPS 训练的神经网络使用 FP32；频谱特征在 CPU 计算后传入 MPS。先用小 batch 检查内存和训练行为，再按素材增加规模。原项目的 Demucs 自监督数据工厂与 PANNs 实验路径尚未完成 MPS 适配，不能据此承诺在这台 Mac 上一键冷启动训练。
