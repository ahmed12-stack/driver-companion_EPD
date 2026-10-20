"""Stage 4 - Which sign is it? (CNN classifier, REQ-01)

On the Pi we load the int8 TFLite model with tflite_runtime.
Off the Pi (laptop tests) a FakeClassifier stands in, so the rest of the
pipeline can be developed and tested without the trained model.
"""
import cv2
import numpy as np


def _load_interpreter(path, threads):
    try:                                      # Pi: pip install ai-edge-litert
        from ai_edge_litert.interpreter import Interpreter
    except ImportError:
        try:                                  # older Pi OS images
            from tflite_runtime.interpreter import Interpreter
        except ImportError:                   # laptop with full TensorFlow
            from tensorflow.lite.python.interpreter import Interpreter
    return Interpreter(model_path=str(path), num_threads=threads)


class TFLiteClassifier:
    def __init__(self, cfg, threads=4):
        self.cfg = cfg
        self.itp = _load_interpreter(cfg.model_path, threads)
        self.itp.allocate_tensors()
        self.inp = self.itp.get_input_details()[0]
        self.out = self.itp.get_output_details()[0]
        n_out = self.out["shape"][-1]
        if n_out != len(cfg.labels):
            raise ValueError(f"model has {n_out} outputs, labels list has "
                             f"{len(cfg.labels)}")

    def _prep(self, crop):
        s = self.cfg.input_size
        x = cv2.resize(crop, (s, s), interpolation=cv2.INTER_AREA)
        x = x.astype(np.float32) / 255.0
        if self.inp["dtype"] in (np.int8, np.uint8):   # quantised input
            scale, zero = self.inp["quantization"]
            x = np.round(x / scale + zero).astype(self.inp["dtype"])
        return x

    def classify(self, crops):
        """crops: list of RGB arrays. Returns list of (label, confidence)."""
        results = []
        for crop in crops:                    # batch=1 model: loop is fine
            self.itp.set_tensor(self.inp["index"], self._prep(crop)[None])
            self.itp.invoke()
            y = self.itp.get_tensor(self.out["index"])[0].astype(np.float32)
            if self.out["dtype"] in (np.int8, np.uint8):
                scale, zero = self.out["quantization"]
                y = (y - zero) * scale
            if y.min() < 0 or abs(y.sum() - 1) > 0.05:   # logits -> softmax
                y = np.exp(y - y.max()); y /= y.sum()
            k = int(np.argmax(y))
            second = float(np.sort(y)[-2]) if len(y) > 1 else 0.0
            results.append((self.cfg.labels[k], float(y[k]), float(y[k]) - second))
        return results


class NumpyClassifier:
    """Same CNN, run with plain NumPy from a .npz weights file.

    Used for the laptop demo so nobody has to install TensorFlow on Windows.
    BatchNorm is already folded into each conv's weights and bias, so the
    network is just: [conv3x3 + ReLU, conv3x3 + ReLU, maxpool] x 3,
    global average pool, dense, softmax.
    """

    def __init__(self, cfg, path=None):
        self.cfg = cfg
        w = np.load(path or cfg.model_path)
        n_conv = int(w["n_conv"])
        self.convs = [(w[f"conv{i}_w"], w[f"conv{i}_b"]) for i in range(n_conv)]
        self.dense_w, self.dense_b = w["dense_w"], w["dense_b"]
        if self.dense_w.shape[1] != len(cfg.labels):
            raise ValueError(f"model has {self.dense_w.shape[1]} outputs, labels list has "
                             f"{len(cfg.labels)}")

    @staticmethod
    def _conv3x3(x, k, b):
        # x: (N, H, W, C), k: (3, 3, C, F). 'same' padding via im2col + one matmul.
        n, h, w, c = x.shape
        p = np.pad(x, ((0, 0), (1, 1), (1, 1), (0, 0)))
        cols = np.lib.stride_tricks.sliding_window_view(p, (3, 3), axis=(1, 2))
        # cols: (N, H, W, C, 3, 3) -> (N*H*W, 3*3*C) in the kernel's (ky, kx, c) order
        cols = cols.transpose(0, 1, 2, 4, 5, 3).reshape(n * h * w, 9 * c)
        y = cols @ k.reshape(9 * c, -1) + b
        return np.maximum(y, 0).reshape(n, h, w, -1)

    @staticmethod
    def _pool(x):
        n, h, w, c = x.shape
        return x.reshape(n, h // 2, 2, w // 2, 2, c).max(axis=(2, 4))

    def probs(self, batch):
        x = batch
        for i, (k, b) in enumerate(self.convs):
            x = self._conv3x3(x, k, b)
            if i % 2 == 1:
                x = self._pool(x)
        x = x.mean(axis=(1, 2)) @ self.dense_w + self.dense_b
        x = np.exp(x - x.max(axis=1, keepdims=True))
        return x / x.sum(axis=1, keepdims=True)

    def classify(self, crops):
        if not crops:
            return []
        s = self.cfg.input_size
        batch = np.stack([cv2.resize(c, (s, s), interpolation=cv2.INTER_AREA)
                          for c in crops]).astype(np.float32) / 255.0
        p = self.probs(batch)
        k = p.argmax(axis=1)
        top2 = np.sort(p, axis=1)[:, -2]
        # (label, confidence, margin over the runner-up class)
        return [(self.cfg.labels[i], float(p[j, i]), float(p[j, i] - top2[j]))
                for j, i in enumerate(k)]


def load_classifier(cfg):
    """Pick the backend from the model file's extension."""
    if str(cfg.model_path).endswith(".npz"):
        return NumpyClassifier(cfg)
    return TFLiteClassifier(cfg)


class FakeClassifier:
    """Test double: returns whatever the test scripted for each frame."""

    def __init__(self, script=None):
        self.script = list(script or [])

    def classify(self, crops):
        if self.script:
            return self.script.pop(0)
        return [("stop", 0.95) for _ in crops]
