"""Video encoders OBS can record with, and the settings Laqta gives each.

OBS has no request that lists encoders, so Laqta tries them in ORDER and remembers the first
one that actually starts. Settings go into recordEncoder.json in the OBS profile folder,
which OBS reads when the profile loads (verified on OBS 33 with NVENC and x264).
"""

from __future__ import annotations

# Hardware first: a software encoder competes with the game for CPU.
ORDER = ("obs_nvenc_h264_tex", "h264_texture_amf", "obs_qsv11_v2", "obs_x264")
NAMES = {
    "obs_nvenc_h264_tex": "NVIDIA NVENC",
    "h264_texture_amf": "AMD AMF",
    "obs_qsv11_v2": "Intel Quick Sync",
    "obs_x264": "x264 (CPU)",
}
# Constant-quality level per quality setting: lower = better picture, bigger file.
LEVEL = {"Stream": 28, "Small": 23, "HQ": 18}


def settings(encoder: str, quality: str = "Small", bitrate_kbps: int | None = None) -> dict:
    """recordEncoder.json contents. A bitrate switches from constant quality to constant bitrate.
    Presets are deliberately light: a heavier NVENC preset with lookahead skipped 4.3 % of
    frames in a real 3440x1440, 60 fps match."""
    level = LEVEL.get(quality, LEVEL["Small"])
    common: dict = {"keyint_sec": 2}  # a keyframe every 2 s keeps trims close to where you asked
    if bitrate_kbps:
        common |= {"rate_control": "CBR", "bitrate": int(bitrate_kbps)}
    if encoder == "obs_nvenc_h264_tex":
        rate = {} if bitrate_kbps else {"rate_control": "CQP", "cqp": level}
        return common | rate | {"preset": "p4", "tune": "hq", "multipass": "disabled", "lookahead": False}
    if encoder == "h264_texture_amf":  # unverified: no AMD card to test on
        rate = {} if bitrate_kbps else {"rate_control": "CQP", "cqp": level}
        return common | rate | {"preset": "balanced"}
    if encoder == "obs_qsv11_v2":  # unverified: no Intel GPU to test on
        rate = {} if bitrate_kbps else {"rate_control": "CQP", "qpi": level, "qpp": level, "qpb": level}
        return common | rate | {"target_usage": "TU4"}
    rate = {} if bitrate_kbps else {"rate_control": "CRF", "crf": level}
    return common | rate | {"preset": "veryfast"}
