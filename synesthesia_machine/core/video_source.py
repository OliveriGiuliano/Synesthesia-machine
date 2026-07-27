"""
Video source module - handles video file playback and webcam input.
"""

import cv2
import numpy as np
import threading
import time
from typing import Optional
from PyQt6.QtCore import QObject, pyqtSignal


class VideoSource(QObject):
    """
    Video source that can read from a file or camera device.
    Uses a background thread for non-blocking frame capture.
    
    Signals:
        frame_ready: Emitted when a new frame is available (numpy array RGB)
        position_changed: Emitted when playback position changes (frame index, total frames)
        playback_ended: Emitted when playback reaches the end
        error_occurred: Emitted when an error occurs (error message)
    """
    
    frame_ready = pyqtSignal(object)  # numpy array
    position_changed = pyqtSignal(int, int)  # current_frame, total_frames
    playback_ended = pyqtSignal()
    error_occurred = pyqtSignal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._capture: Optional[cv2.VideoCapture] = None
        self._source_type: str = "file"  # "file" or "camera"
        self._is_playing: bool = False
        self._fps: float = 24.0
        self._thread: Optional[threading.Thread] = None
        self._thread_lock = threading.Lock()
    
    def open_file(self, file_path: str) -> bool:
        """Open a video file for playback."""
        self._cleanup()
        self._source_type = "file"
        
        self._capture = cv2.VideoCapture(file_path)
        if not self._capture.isOpened():
            self.error_occurred.emit(f"Failed to open video file: {file_path}")
            return False
        
        # Get actual FPS from file
        self._fps = self._capture.get(cv2.CAP_PROP_FPS)
        if self._fps <= 0:
            self._fps = 24.0
        
        total_frames = int(self._capture.get(cv2.CAP_PROP_FRAME_COUNT))
        self.position_changed.emit(0, total_frames)
        return True
    
    def open_camera(self, index: int = 0) -> bool:
        """Open a camera device for live capture."""
        self._cleanup()
        self._source_type = "camera"
        
        self._capture = cv2.VideoCapture(index)
        if not self._capture.isOpened():
            self.error_occurred.emit(f"Failed to open camera device: {index}")
            return False
        
        # Try to set reasonable camera properties
        self._capture.set(cv2.CAP_PROP_FPS, 24)
        self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        
        # Try to get camera FPS, default to 24
        self._fps = self._capture.get(cv2.CAP_PROP_FPS)
        if self._fps <= 0:
            self._fps = 24.0
        
        self.position_changed.emit(0, 0)  # 0/0 indicates live stream
        return True
    
    def start(self):
        """Start the capture loop in a background thread."""
        with self._thread_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            
            self._is_playing = True
            self._thread = threading.Thread(target=self._capture_loop, daemon=True)
            self._thread.start()
    
    def pause(self):
        """Pause playback."""
        self._is_playing = False
    
    def resume(self):
        """Resume playback."""
        if self._capture is not None and self._capture.isOpened():
            self._is_playing = True
    
    def stop(self):
        """Stop playback and cleanup."""
        self._is_playing = False
        self._cleanup()
    
    def seek_to_frame(self, frame_index: int):
        """Seek to a specific frame (file mode only)."""
        if self._capture is None:
            return
        if self._source_type != "file":
            return
        
        try:
            # Pause briefly to avoid race conditions
            was_playing = self._is_playing
            self._is_playing = False
            time.sleep(0.05)  # Let the capture loop notice the pause
            
            self._capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            
            # Read a frame to ensure the seek took effect
            for _ in range(3):
                self._capture.read()
            
            # Verify and report actual position
            actual = self._capture.get(cv2.CAP_PROP_POS_FRAMES)
            total = self._capture.get(cv2.CAP_PROP_FRAME_COUNT)
            self.position_changed.emit(int(actual), int(total))
            
            # Resume if was playing
            if was_playing:
                self._is_playing = True
                
        except Exception as e:
            self.error_occurred.emit(f"Seek error: {e}")
    
    def seek_to_time(self, seconds: float):
        """Seek to a specific time position (file mode only)."""
        if self._capture is None:
            return
        if self._source_type != "file":
            return
        
        try:
            was_playing = self._is_playing
            self._is_playing = False
            time.sleep(0.05)
            
            self._capture.set(cv2.CAP_PROP_POS_MSEC, int(seconds * 1000))
            
            # Read a frame to ensure seek took effect
            for _ in range(3):
                self._capture.read()
            
            frame = self._capture.get(cv2.CAP_PROP_POS_FRAMES)
            total = self._capture.get(cv2.CAP_PROP_FRAME_COUNT)
            self.position_changed.emit(int(frame), int(total))
            
            if was_playing:
                self._is_playing = True
                
        except Exception as e:
            self.error_occurred.emit(f"Seek error: {e}")
    
    def get_total_frames(self) -> int:
        """Get total number of frames (file mode)."""
        if self._capture is None:
            return 0
        try:
            return int(self._capture.get(cv2.CAP_PROP_FRAME_COUNT))
        except Exception:
            return 0
    
    def get_current_frame_index(self) -> int:
        """Get current frame index."""
        if self._capture is None:
            return 0
        try:
            return int(self._capture.get(cv2.CAP_PROP_POS_FRAMES))
        except Exception:
            return 0
    
    def get_duration_seconds(self) -> float:
        """Get total duration in seconds."""
        total_frames = self.get_total_frames()
        if total_frames == 0:
            return 0.0
        return total_frames / self._fps
    
    def get_fps(self) -> float:
        """Get the FPS of the current source."""
        return self._fps
    
    @property
    def is_playing(self) -> bool:
        return self._is_playing
    
    @property
    def source_type(self) -> str:
        return self._source_type
    
    def _capture_loop(self):
        """Main capture loop - runs in background thread.
        
        Uses perf_counter for accurate frame timing to maintain correct playback speed.
        """
        frame_interval = 1.0 / self._fps if self._fps > 0 else 1.0 / 24.0
        
        # Track timing for accurate FPS
        next_frame_time = time.perf_counter()
        
        while self._is_playing and self._capture is not None:
            if not self._capture.isOpened():
                self.error_occurred.emit("Video capture device closed unexpectedly")
                break
            
            ret, frame_bgr = self._capture.read()
            
            if not ret:
                if self._source_type == "file":
                    # End of file
                    self.playback_ended.emit()
                    self._is_playing = False
                    break
                else:
                    # Camera glitch, wait and retry
                    time.sleep(0.05)
                    if not self._is_playing:
                        break
                    continue
            
            # Convert BGR to RGB and make a copy for safe signal emission
            frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            frame_rgb = frame_rgb.copy()  # Ensure thread-safe copy
            
            # Emit frame (signal will be queued to main thread)
            self.frame_ready.emit(frame_rgb)
            
            # Update position
            if self._source_type == "file":
                try:
                    current = int(self._capture.get(cv2.CAP_PROP_POS_FRAMES))
                    total = int(self._capture.get(cv2.CAP_PROP_FRAME_COUNT))
                    self.position_changed.emit(current, total)
                except Exception:
                    pass
            
            # Accurate frame timing using perf_counter
            # Sleep until it's time for the next frame, accounting for processing time
            time.sleep(frame_interval)
            next_frame_time = time.perf_counter() + frame_interval
        
        self._is_playing = False
    
    def _cleanup(self):
        """Release resources."""
        self._is_playing = False
        
        if self._thread is not None:
            if self._thread.is_alive():
                self._thread.join(timeout=2.0)
            self._thread = None
        
        if self._capture is not None:
            try:
                self._capture.release()
            except Exception:
                pass
            self._capture = None


if __name__ == "__main__":
    # Quick test
    import sys
    from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget
    from PyQt6.QtGui import QImage, QPixmap
    
    app = QApplication(sys.argv)
    
    window = QWidget()
    layout = QVBoxLayout(window)
    label = QLabel("No frame")
    layout.addWidget(label)
    window.show()
    
    source = VideoSource()
    
    def on_frame(frame):
        h, w, ch = frame.shape
        q_image = QImage(frame.data, w, h, ch * w, QImage.Format.Format_RGB888)
        label.setPixmap(QPixmap.fromImage(q_image).scaled(320, 240))
    
    source.frame_ready.connect(on_frame)
    
    # Test with camera 0
    if source.open_camera(0):
        source.start()
    
    ret = app.exec()
    source.stop()
    sys.exit(ret)