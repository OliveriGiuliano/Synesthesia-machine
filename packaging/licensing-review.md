# Distribution and third-party licensing gate

Status: **blocked for external distribution**.

The repository owner has not selected an application/source licence or distribution model.
`LICENSE-or-NOTICE.md` therefore retains copyright and grants no distribution permission. This file
is an engineering inventory, not legal advice; competent legal review is required before any
commercial, proprietary, public, or third-party distribution.

The generated `dependency-inventory.json` is authoritative for exact locked versions, declared
metadata, bundled native-file hashes, FFmpeg library versions, and codec names. The artifact also
contains generated `THIRD_PARTY_NOTICES.md` plus verbatim licence files under `licenses/`.

| Component | Engineering checkpoint | Release status |
| --- | --- | --- |
| Application code/assets | Choose licence and public/internal/proprietary distribution model; confirm rights to the generated icon and all shipped fixtures. | Blocked |
| Qt / PySide6 / Shiboken | Review LGPLv3/GPL/commercial choice, relinking/replacement requirements, notices, source-offer obligations, and whether every shipped Qt module/plugin is permitted under the chosen model. | Legal review required |
| PyAV / FFmpeg | Inventory captures exact runtime library versions, codec names, and native DLL hashes, but the wheel does not expose its complete FFmpeg configure flags. The locked wheel includes `libx264`, `libx265`, LAME, OpenCORE AMR, dav1d, SVT-AV1, VPL, VPX, WebP, and other DLLs. Obtain the exact wheel build provenance and review LGPL/GPL/nonfree and patent implications. | Blocked |
| OpenCV | Review the wheel's Apache-2.0 licence text and bundled third-party native notices/codecs. | Legal review required |
| Mido / python-rtmidi / RtMidi | Review MIT notices and the exact bundled `rtmidi` native binary. Confirm the selected MIDI backend does not add undisclosed runtime components. | Legal review required |
| sounddevice / PortAudio / CFFI | Review MIT/BSD notices and bundled PortAudio binaries. The wheel's notice describes default and ASIO-enabled variants; confirm Nuitka output contents and Steinberg ASIO SDK distribution terms. | Legal review required |
| NumPy and transitives | Preserve the generated BSD/MIT/other licence texts and review the complete inventory. | Legal review required |

No installer, signing, upload, publication, or external handoff is authorized while this gate is
blocked. Internal build verification may continue using the standalone directory.
