"""
Color analysis utilities for synesthesia processing.
"""

import numpy as np
from typing import Tuple, List


def rgb_to_hsv_frame(frame: np.ndarray) -> np.ndarray:
    """
    Convert an RGB frame to HSV color space.
    
    Args:
        frame: numpy array of shape (H, W, 3) with RGB values 0-255
    
    Returns:
        numpy array of shape (H, W, 3) with HSV values.
        H: 0-180 (OpenCV convention), S: 0-255, V: 0-255
    """
    # OpenCV expects BGR for cvtColor, so convert RGB -> BGR -> HSV
    import cv2
    bgr_frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    hsv_frame = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2HSV)
    return hsv_frame


def compute_hue_histogram(
    hsv_frame: np.ndarray,
    num_bins: int,
    saturation_min: int = 20,
    value_min: int = 50,
) -> np.ndarray:
    """
    Compute a histogram of hue values, only counting pixels above
    saturation and value thresholds.
    
    Args:
        hsv_frame: HSV frame from OpenCV (H: 0-180, S: 0-255, V: 0-255)
        num_bins: Number of hue bins to create
        saturation_min: Minimum saturation value to consider a pixel
        value_min: Minimum brightness value to consider a pixel
    
    Returns:
        Array of pixel counts per bin, length = num_bins
    """
    h = hsv_frame[:, :, 0]  # Hue plane (0-180)
    s = hsv_frame[:, :, 1]  # Saturation plane (0-255)
    v = hsv_frame[:, :, 2]  # Value plane (0-255)
    
    # Create mask for pixels above thresholds
    mask = (s >= saturation_min) & (v >= value_min)
    
    # Get hue values of qualifying pixels
    qualifying_hues = h[mask]
    
    # Map hue from 0-180 to 0-num_bins range
    # Each bin covers 180/num_bins degrees
    bin_width = 180.0 / num_bins
    bin_indices = (qualifying_hues / bin_width).astype(np.int32)
    bin_indices = np.clip(bin_indices, 0, num_bins - 1)
    
    # Count pixels per bin
    histogram = np.bincount(bin_indices, minlength=num_bins)[:num_bins]
    
    return histogram


def get_hue_bin_colors(num_bins: int) -> List[Tuple[int, int, int]]:
    """
    Get representative RGB colors for each hue bin.
    
    Args:
        num_bins: Number of hue bins
    
    Returns:
        List of (R, G, B) tuples, one per bin
    """
    colors = []
    for i in range(num_bins):
        # Center hue of this bin (0-180 range)
        bin_center = (i + 0.5) * (180.0 / num_bins)
        # Convert HSV (h, 255, 255) to RGB
        import cv2
        hsv = np.uint8([[[int(bin_center), 255, 255]]])
        bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
        r, g, b = bgr[0][0][2], bgr[0][0][1], bgr[0][0][0]
        colors.append((r, g, b))
    return colors


def downscale_frame(frame: np.ndarray, width: int, height: int) -> np.ndarray:
    """
    Downscale a frame to the specified dimensions using area interpolation.
    
    Args:
        frame: Input RGB frame
        width: Target width
        height: Target height
    
    Returns:
        Downscaled frame of shape (height, width, 3)
    """
    import cv2
    return cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)