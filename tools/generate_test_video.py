"""Generate a tiny deterministic CFR MP4 fixture with PyAV."""

import argparse
import colorsys
from collections.abc import Callable
from fractions import Fraction
from itertools import pairwise
from pathlib import Path

import av
import numpy as np
from numpy.typing import NDArray

DEFAULT_WIDTH = 64
DEFAULT_HEIGHT = 48
DEFAULT_FRAME_COUNT = 6
DEFAULT_FPS = 12
HUE_FRAME_COUNT = 7

type FramePixelFactory = Callable[[int], NDArray[np.uint8]]


def frame_pixels(index: int, *, width: int, height: int) -> NDArray[np.uint8]:
    """Return deterministic RGB bars whose red value identifies the frame."""

    image = np.empty((height, width, 3), dtype=np.uint8)
    image[..., 0] = (index * 31) % 256
    image[..., 1] = np.arange(width, dtype=np.uint8)[np.newaxis, :]
    image[..., 2] = np.arange(height, dtype=np.uint8)[:, np.newaxis]
    return image


def generate_test_video(
    output_path: Path,
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    frame_count: int = DEFAULT_FRAME_COUNT,
    fps: int = DEFAULT_FPS,
) -> Path:
    """Create an MP4 fixture and close all container/codec handles before returning."""

    if width <= 0 or height <= 0 or frame_count <= 0 or fps <= 0:
        msg = "width, height, frame_count, and fps must be positive"
        raise ValueError(msg)

    return _generate_cfr_video(
        output_path,
        width=width,
        height=height,
        frame_count=frame_count,
        fps=fps,
        pixels=lambda index: frame_pixels(index, width=width, height=height),
    )


def hue_frame_pixels(index: int, *, width: int, height: int) -> NDArray[np.uint8]:
    """Return a solid colour centered in one of seven equal hue bins."""

    hue = ((index % HUE_FRAME_COUNT) + 0.5) / HUE_FRAME_COUNT
    red, green, blue = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
    colour = np.asarray(
        (round(red * 255.0), round(green * 255.0), round(blue * 255.0)),
        dtype=np.uint8,
    )
    image = np.empty((height, width, 3), dtype=np.uint8)
    image[...] = colour
    return image


def generate_hue_test_video(
    output_path: Path,
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    frame_count: int = HUE_FRAME_COUNT,
    fps: int = DEFAULT_FPS,
) -> Path:
    """Create a CFR hue sequence with one deterministic scale-bin colour per frame."""

    if width <= 0 or height <= 0 or frame_count <= 0 or fps <= 0:
        msg = "width, height, frame_count, and fps must be positive"
        raise ValueError(msg)
    return _generate_cfr_video(
        output_path,
        width=width,
        height=height,
        frame_count=frame_count,
        fps=fps,
        pixels=lambda index: hue_frame_pixels(index, width=width, height=height),
    )


def _generate_cfr_video(
    output_path: Path,
    *,
    width: int,
    height: int,
    frame_count: int,
    fps: int,
    pixels: FramePixelFactory,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with av.open(str(output_path), mode="w") as container:
        # PyAV's overload includes dynamically selected stream types. The literal codec fixes the
        # runtime type, but its current stub leaves generic packet parameters unknown.
        stream = container.add_stream(  # pyright: ignore[reportUnknownMemberType]
            "mpeg4", rate=fps
        )
        stream.width = width
        stream.height = height
        stream.pix_fmt = "yuv420p"
        stream.time_base = Fraction(1, fps)

        for index in range(frame_count):
            frame = av.VideoFrame.from_ndarray(pixels(index), format="rgb24")
            frame.pts = index
            frame.time_base = Fraction(1, fps)
            for packet in stream.encode(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                frame
            ):
                container.mux(packet)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]

        for packet in stream.encode():  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            container.mux(packet)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]

    return output_path


def generate_vfr_test_video(
    output_path: Path,
    *,
    pts_milliseconds: tuple[int, ...] = (0, 40, 120, 150, 300),
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> Path:
    """Create a deterministic MPEG-4 fixture with explicit variable PTS intervals."""

    if width <= 0 or height <= 0 or not pts_milliseconds:
        msg = "width, height, and pts_milliseconds must be non-empty and positive"
        raise ValueError(msg)
    if pts_milliseconds[0] < 0 or any(
        current <= previous for previous, current in pairwise(pts_milliseconds)
    ):
        msg = "pts_milliseconds must be non-negative and strictly increasing"
        raise ValueError(msg)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    time_base = Fraction(1, 1000)
    with av.open(str(output_path), mode="w") as container:
        stream = container.add_stream(  # pyright: ignore[reportUnknownMemberType]
            "mpeg4", rate=25
        )
        stream.width = width
        stream.height = height
        stream.pix_fmt = "yuv420p"
        stream.time_base = time_base
        stream.codec_context.time_base = time_base

        for index, pts in enumerate(pts_milliseconds):
            frame = av.VideoFrame.from_ndarray(
                frame_pixels(index, width=width, height=height), format="rgb24"
            )
            frame.pts = pts
            frame.time_base = time_base
            for packet in stream.encode(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                frame
            ):
                container.mux(packet)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]

        for packet in stream.encode():  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            container.mux(packet)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]

    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = generate_test_video(args.output)
    print(output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
