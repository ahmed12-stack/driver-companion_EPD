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
            results.append((self.cfg.labels[k], float(y[k])))
        return results


class FakeClassifier:
    """Test double: returns whatever the test scripted for each frame."""

    def __init__(self, script=None):
        self.script = list(script or [])

    def classify(self, crops):
        if self.script:
            return self.script.pop(0)
        return [("stop", 0.95) for _ in crops]
