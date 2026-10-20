"""Train the ~200k-parameter sign CNN and export an int8 TFLite model.

Run on a laptop / Colab (needs TensorFlow), NOT on the Pi.

Dataset layout (one folder per class, names must match config.CLASSES,
including a "background" folder of non-sign crops):
    data/train/speed_limit_50/*.png
    data/train/stop/*.png
    ...
    data/val/<same folders>

Tip: start from GTSRB images for the shapes that match, then add Indian
IRC:67 photos (own dash-cam crops) for the classes GTSRB lacks.

    python tools/train_export.py --data data --out models/sign_cnn_int8.tflite
"""
import argparse
import sys

import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.config import CLASSES  # noqa: E402

SIZE = 32


def build_model(n_classes):
    import tensorflow as tf
    L = tf.keras.layers

    def block(x, f):
        x = L.Conv2D(f, 3, padding="same", use_bias=False)(x)
        x = L.BatchNormalization(momentum=0.9)(x)
        x = L.ReLU()(x)
        x = L.Conv2D(f, 3, padding="same", use_bias=False)(x)
        x = L.BatchNormalization(momentum=0.9)(x)
        x = L.ReLU()(x)
        return L.MaxPooling2D()(x)

    inp = L.Input((SIZE, SIZE, 3))
    x = block(inp, 32)          # 16x16
    x = block(x, 64)            # 8x8
    x = block(x, 96)            # 4x4
    x = L.GlobalAveragePooling2D()(x)
    x = L.Dropout(0.3)(x)
    out = L.Dense(n_classes, activation="softmax")(x)
    return tf.keras.Model(inp, out)


def main():
    import tensorflow as tf
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="models/sign_cnn_int8.tflite")
    ap.add_argument("--epochs", type=int, default=30)
    a = ap.parse_args()

    kw = dict(image_size=(SIZE, SIZE), batch_size=64, class_names=CLASSES,
              label_mode="int")
    train = tf.keras.utils.image_dataset_from_directory(f"{a.data}/train", shuffle=True, **kw)
    val = tf.keras.utils.image_dataset_from_directory(f"{a.data}/val", shuffle=False, **kw)

    # Road-realistic augmentation. NO horizontal flip: it turns
    # left_curve into right_curve.
    aug = tf.keras.Sequential([
        tf.keras.layers.RandomRotation(0.05),
        tf.keras.layers.RandomZoom(0.15),
        tf.keras.layers.RandomTranslation(0.1, 0.1),
        tf.keras.layers.RandomBrightness(0.3, value_range=(0, 1)),
        tf.keras.layers.RandomContrast(0.3),
    ])
    norm = lambda x, y: (x / 255.0, y)
    train = train.map(norm).map(lambda x, y: (aug(x, training=True), y)).prefetch(2)
    val = val.map(norm).prefetch(2)

    model = build_model(len(CLASSES))
    model.summary()
    print("parameters:", model.count_params())
    model.compile(optimizer=tf.keras.optimizers.Adam(1e-3),
                  loss="sparse_categorical_crossentropy", metrics=["accuracy"])
    model.fit(train, validation_data=val, epochs=a.epochs, callbacks=[
        tf.keras.callbacks.ReduceLROnPlateau(patience=3, factor=0.5),
        tf.keras.callbacks.EarlyStopping(patience=8, restore_best_weights=True)])
    print("val accuracy (REQ-01 needs >= 0.90):", model.evaluate(val)[1])

    # Full-integer quantisation: ~4x smaller, much faster on the Pi's CPU
    def rep_data():
        for x, _ in val.take(50):
            for i in range(x.shape[0]):
                yield [x[i:i + 1]]

    conv = tf.lite.TFLiteConverter.from_keras_model(model)
    conv.optimizations = [tf.lite.Optimize.DEFAULT]
    conv.representative_dataset = rep_data
    conv.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    conv.inference_input_type = tf.int8
    conv.inference_output_type = tf.int8
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_bytes(conv.convert())
    print("wrote", a.out)

    npz = str(Path(a.out).with_suffix(".npz"))
    export_npz(model, npz)
    print("wrote", npz, "(laptop demo, NumPy only)")


def export_npz(model, path):
    """Save weights for pipeline.classifier.NumpyClassifier, with each
    BatchNorm folded into the conv before it: w' = w*g/s, b' = beta - mean*g/s."""
    import tensorflow as tf
    L = tf.keras.layers
    out, convs, pending = {}, [], None
    for layer in model.layers:
        if isinstance(layer, L.Conv2D):
            pending = layer.get_weights()[0]
        elif isinstance(layer, L.BatchNormalization):
            g, beta, mean, var = layer.get_weights()
            s = g / np.sqrt(var + layer.epsilon)
            convs.append(((pending * s).astype(np.float32), (beta - mean * s).astype(np.float32)))
            pending = None
        elif isinstance(layer, L.Dense):
            out["dense_w"], out["dense_b"] = [w.astype(np.float32) for w in layer.get_weights()]
    for i, (w, b) in enumerate(convs):
        out[f"conv{i}_w"], out[f"conv{i}_b"] = w, b
    out["n_conv"] = np.array(len(convs))
    np.savez_compressed(path, **out)


if __name__ == "__main__":
    main()
