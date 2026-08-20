# Media package

Qt-free video decoding, camera capture, image conversion, and reusable OpenCV/PyAV algorithms.

Video and camera services own their worker threads and publish immutable, read-only frames with a
source clock. Live sources prefer bounded latency over retaining every frame. Camera identities use
the `opencv:<index>` compatibility form; discovery is requested through the engine device catalogue,
never directly from Qt.

Use the `synesthesia_machine.media` facade. Tests inject capture/container factories and deterministic
clocks; no automated test should require local media hardware.
