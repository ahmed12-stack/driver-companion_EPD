"""Central settings for the vision pipeline (Group 7, Driver Companion).

Every number here is traced to a requirement in PDR Rev 1.1 so the
Requirements Traceability Matrix can point at one file.
"""
from dataclasses import dataclass, field
from pathlib import Path

# 20 IRC:67 sign classes (REQ-01). Order must match the CNN output layer.
LABELS = [
    "speed_limit_20", "speed_limit_30", "speed_limit_40", "speed_limit_50",
    "speed_limit_60", "speed_limit_80", "speed_limit_100",
    "stop", "give_way", "no_entry", "no_overtaking", "no_u_turn",
    "no_horn", "no_parking", "school_ahead", "pedestrian_crossing",
    "speed_breaker", "left_curve", "right_curve", "men_at_work",
]

# The CNN has one extra output, "background", for crops that are red/blue
# but not a sign (a red car, a shop board, a face). A background answer
# never counts as a vote. CNN output order = CLASSES.
BACKGROUND = "background"
CLASSES = LABELS + [BACKGROUND]

# Speed value for speed-limit classes; used by Amrit's speed logic.
SPEED_LIMIT_KMH = {
    "speed_limit_20": 20, "speed_limit_30": 30, "speed_limit_40": 40,
    "speed_limit_50": 50, "speed_limit_60": 60, "speed_limit_80": 80,
    "speed_limit_100": 100,
}


@dataclass
class Config:
    # Camera
    frame_width: int = 640
    frame_height: int = 480
    target_fps: int = 15            # nominal rate (latency maths assumes 15)
    throttled_fps: int = 10         # REQ-24: drop to 10 fps if SoC throttles

    # Region of interest: signs sit on the left/right verge and above the road.
    # Fractions of the frame (x0, y0, x1, y1). Full width, top 65 %.
    roi: tuple = (0.0, 0.0, 1.0, 0.65)

    # Candidate proposals (colour + shape)
    min_box_px: int = 14            # smaller than this is too far to classify
    max_box_frac: float = 0.45      # larger than this is not a sign
    max_candidates: int = 4         # keep inference time bounded on Pi 3B+
    # Far-sign pass (proposals.py): gentler colour mask, small blobs only,
    # with slots of its own on top of max_candidates. 0 turns it off.
    far_candidates: int = 2
    far_min_px: int = 11            # ~57 m for a 75 cm sign at 1280 px, 70 deg FOV
    far_max_px: int = 28            # bigger signs are caught by the normal pass

    # Classifier
    model_path: Path = Path("models/sign_cnn_int8.tflite")
    input_size: int = 32            # CNN input is 32x32x3
    min_confidence: float = 0.85    # below this a frame "vote" is not cast
    min_margin: float = 0.60        # top class must beat the runner-up by this much
    verify_shape: bool = True       # colour + outline shape must agree with the CNN (verify.py)

    # Temporal voting (REQ-03): confirm when a class wins 3 of the last 5 frames
    vote_window: int = 5
    vote_needed: int = 3
    # A "sighting" ends once the class is missing for this many frames
    # (REQ-17: each sign spoken once per sighting)
    sighting_gap_frames: int = 15   # 1 s at 15 fps

    # Camera health (REQ-18): blinded if dark/flat for 10 s
    blind_window_s: float = 10.0
    blind_mean_below: float = 20.0      # mean grey level 0-255
    blind_sharpness_below: float = 15.0  # variance of Laplacian
    blind_mean_above: float = 245.0      # washed out / sun straight in

    # Output to other modules (REQ-30: socket 0600)
    socket_path: Path = Path("/run/driver_companion/vision.sock")
    log_dir: Path = Path("/var/lib/driver_companion/logs")

    labels: list = field(default_factory=lambda: list(CLASSES))
