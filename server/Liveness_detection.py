# 2つのライブネスモデルをCPUで実行し、結果を統合します。
import importlib.util
from pathlib import Path
import numpy as np
import torch

class LivenessDetector:
    def __init__(self, threshold=0.8, model_dir=None):
        if not np.isfinite(threshold) or not 0 < threshold <= 1:
            raise ValueError("threshold must be in (0, 1].")
        self.threshold = float(threshold)
        self.model_dir = Path(model_dir) if model_dir is not None else Path(__file__).resolve().parent / "live_test_assets"
        # Load model definitions and crop utility without importing live_test.py.
        definitions = self._load_module("MiniFASNet")
        self.cropper = self._load_module("generate_patches").CropImage()
        self.models = []
        for filename, kind, scale in [
            ("2.7_80x80_MiniFASNetV2.pth", "MiniFASNetV2", 2.7),
            ("4_0_0_80x80_MiniFASNetV1SE.pth", "MiniFASNetV1SE", 4.0)
        ]:
            path = self.model_dir / filename
            if not path.is_file():
                raise FileNotFoundError(f"Liveness model not found: {path}")
            model = getattr(definitions, kind)(conv6_kernel=(5, 5))
            weights = torch.load(path, map_location="cpu", weights_only=True)
            weights = {key.removeprefix("module."): value for key, value in weights.items()}
            model.load_state_dict(weights)
            model.eval()
            self.models.append((model, scale))

    def _load_module(self, name):
        path = self.model_dir / (name + ".py")
        if not path.is_file():
            raise FileNotFoundError(f"Liveness helper not found: {path}")
        spec = importlib.util.spec_from_file_location("liveness_" + name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    # 入力画像を判定します。認証成功の条件はここで確認します。
    def check(self, frame, box):
        if not isinstance(frame, np.ndarray) or frame.ndim != 3:
            return False, 0.0
        if frame.shape[2] != 3 or frame.dtype != np.uint8 or min(frame.shape[:2]) < 2:
            return False, 0.0
        try:
            coordinates = np.asarray(box, dtype=np.float64)
        except (TypeError, ValueError):
            return False, 0.0
        if coordinates.shape != (4,) or not np.isfinite(coordinates).all():
            return False, 0.0
        x1, y1, x2, y2 = coordinates
        if x2 <= x1 or y2 <= y1:
            return False, 0.0
        height, width = frame.shape[:2]
        x1, x2 = np.clip([x1, x2], 0, width - 1).astype(int)
        y1, y2 = np.clip([y1, y2], 0, height - 1).astype(int)
        if x2 <= x1 or y2 <= y1:
            return False, 0.0
        # Convert InsightFace xyxy to the crop utility's xywh convention.
        bbox = (int(x1), int(y1), int(x2 - x1 + 1), int(y2 - y1 + 1))
        if min(bbox[2:]) < 60:
            return False, 0.0
        predictions = []
        with torch.inference_mode():
            for model, scale in self.models:
                patch = self.cropper.crop(frame, bbox, scale, 80, 80)
                # Match upstream preprocessing: BGR float32 in [0, 255].
                tensor = torch.from_numpy(np.ascontiguousarray(patch.transpose(2, 0, 1))).float().unsqueeze(0)
                predictions.append(torch.softmax(model(tensor), dim=1).numpy()[0])
        scores = np.mean(predictions, axis=0)
        if scores.shape != (3,) or not np.isfinite(scores).all():
            return False, 0.0
        # Class 1 is real; classes 0 and 2 are spoof.
        live_score = float(scores[1])
        is_live = int(np.argmax(scores)) == 1 and live_score >= self.threshold
        return bool(is_live), live_score
