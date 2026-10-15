"""Isolated model-environment probe. Never enable unrestricted checkpoint loading."""
import argparse
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--checkpoint")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.repository) / "src"))
    import torch
    from devices import resolve_device
    from models import build_model
    from features import LogMel  # verify audio frontend dependencies too
    devices = [{"id": "auto", "label": "自动（" + {"mps": "Apple GPU", "cuda": "NVIDIA GPU", "cpu": "CPU"}[resolve_device().type] + "）", "available": True},
               {"id": "cpu", "label": "CPU", "available": True},
               {"id": "mps", "label": "Apple GPU", "available": torch.backends.mps.is_available()},
               {"id": "cuda", "label": "NVIDIA GPU", "available": torch.cuda.is_available()}]
    result = {"devices": devices, "automatic_device": str(resolve_device())}
    if args.checkpoint:
        checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=True)
        if not isinstance(checkpoint, dict) or not isinstance(checkpoint.get("args"), dict):
            raise ValueError("需要包含 args 和 model 的 AutoSlicer 训练检查点")
        config = checkpoint["args"]
        architecture = config.get("model")
        if architecture not in {"crnn", "tcn"} or config.get("input_type", "mel") != "mel":
            raise ValueError("目前支持 AutoSlicer 的 mel/CRNN 或 mel/TCN 检测模型")
        # Match infer.py's actual contract rather than accepting formats it cannot execute.
        raw_kwargs = config.get("model_kwargs", "{}")
        if not isinstance(raw_kwargs, str):
            raise ValueError("model_kwargs 必须为 JSON 字符串")
        kwargs = json.loads(raw_kwargs)
        if not isinstance(kwargs, dict):
            raise ValueError("模型结构参数无效")
        model = build_model(architecture, **kwargs)
        model.load_state_dict(checkpoint["model"], strict=True)
        if any(not torch.isfinite(t).all() for t in model.state_dict().values() if t.is_floating_point()):
            raise ValueError("模型权重包含无效数值")
        model.eval()
        with torch.no_grad():
            prediction = model(torch.zeros(1, 80, 160))
        if not torch.isfinite(prediction).all() or prediction.ndim not in (2, 3):
            raise ValueError("模型无法完成有效的检测计算")
        if prediction.shape[0] != 1 or prediction.shape[1] != 10:
            raise ValueError("模型时间分辨率不兼容，需要 16 倍时间降采样的 AutoSlicer 模型")
        if config.get("out_fps") not in (None, 3.125):
            raise ValueError("模型帧率不兼容，当前支持每秒 3.125 帧的检测模型")
        classes = prediction.shape[-1] if prediction.ndim == 3 else 1
        if prediction.ndim == 3 and classes < 2:
            raise ValueError("模型分类输出不兼容")
        result.update(architecture=architecture, classes=classes)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        message = str(error) if isinstance(error, ValueError) else "缺少模型环境依赖，请按安装说明准备训练环境" if isinstance(error, ImportError) else "无法读取兼容模型，请选择 AutoSlicer 训练生成的完整模型文件；不支持 RVC 等音色转换模型"
        print(json.dumps({"error": message[:1000]}, ensure_ascii=False))
        raise SystemExit(1)
