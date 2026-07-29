"""Open a video, report stream metadata/PTS, and convert one frame to RGB NumPy."""

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import av
import numpy as np


@dataclass(frozen=True, slots=True)
class VideoProbeResult:
    path: str
    stream_index: int
    codec_name: str
    width: int
    height: int
    average_rate: str | None
    time_base: str
    decoded_frames: int
    first_pts: int | None
    first_time_seconds: float | None
    first_rgb_shape: tuple[int, int, int]
    first_rgb_dtype: str


def probe_video(path: Path, *, max_frames: int | None = None) -> VideoProbeResult:
    """Decode video frames and return evidence that timestamps and RGB conversion work."""

    decoded_frames = 0
    first_pts: int | None = None
    first_time_seconds: float | None = None
    first_rgb_shape: tuple[int, int, int] | None = None
    first_rgb_dtype: str | None = None

    with av.open(str(path), mode="r") as container:
        if not container.streams.video:
            msg = f"No video stream found in {path}"
            raise ValueError(msg)
        stream = container.streams.video[0]

        # Decoding belongs to the container because it must demux packets before feeding the
        # selected codec stream. ``stream.decode()`` only decodes explicitly supplied packets.
        for frame in container.decode(stream):  # pyright: ignore[reportUnknownMemberType]
            decoded_frames += 1
            if first_rgb_shape is None:
                rgb = np.asarray(frame.to_ndarray(format="rgb24"), dtype=np.uint8)
                if rgb.ndim != 3 or rgb.shape[2] != 3:
                    msg = f"Unexpected RGB frame shape: {rgb.shape}"
                    raise ValueError(msg)
                first_pts = frame.pts
                first_time_seconds = float(frame.time)
                first_rgb_shape = (int(rgb.shape[0]), int(rgb.shape[1]), int(rgb.shape[2]))
                first_rgb_dtype = str(rgb.dtype)
            if max_frames is not None and decoded_frames >= max_frames:
                break

        if first_rgb_shape is None or first_rgb_dtype is None:
            msg = f"No decodable video frames found in {path}"
            raise ValueError(msg)

        codec_name = stream.codec_context.name or "unknown"
        average_rate = str(stream.average_rate) if stream.average_rate is not None else None
        return VideoProbeResult(
            path=str(path.resolve()),
            stream_index=stream.index,
            codec_name=codec_name,
            width=stream.width,
            height=stream.height,
            average_rate=average_rate,
            time_base=str(stream.time_base),
            decoded_frames=decoded_frames,
            first_pts=first_pts,
            first_time_seconds=first_time_seconds,
            first_rgb_shape=first_rgb_shape,
            first_rgb_dtype=first_rgb_dtype,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--max-frames", type=int)
    args = parser.parse_args()
    print(json.dumps(asdict(probe_video(args.video, max_frames=args.max_frames)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
