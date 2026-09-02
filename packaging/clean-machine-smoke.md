# Clean-machine smoke procedure

Run this gate on a current Windows 11 x64 VM or newly created standard user account, or a clean
Linux x64 machine, with no Python, `uv`, compiler, repository checkout, or developer tools on
`PATH`. The Linux machine must provide the desktop base libraries (Qt GL/EGL, fontconfig,
xkbcommon); `libportaudio2` is required for the audio check and otherwise reported as skipped.

1. Verify the ZIP SHA-256 against `SHA256SUMS.txt`, extract it, and run the bundled
   `smoke\smoke_test.ps1` with Windows PowerShell (Windows) or `smoke/smoke_test.sh` with bash
   (Linux), passing the extracted application directory. Preserve its JSON report.
2. Confirm the automated report passes graph save/open, H.264/MP4 decode, real MIDI enumeration,
   mock send/panic, PortAudio loading and debug-synth rendering, engine crash/restart, autosave
   recovery, and diagnostic export. A missing camera is an explicit skip; a detected camera must
   return one frame.
3. Launch the app normally. Create a small graph, save it under Documents, close, relaunch, and open
   the graph from Recent Files.
4. If a camera is attached, select its exact enumerated ID and confirm live frames. If a MIDI
   loopback or hardware port is attached, select its exact name and confirm note-on, note-off, and
   panic. If an audio output is attached, enable Generate Audio briefly and confirm audible output.
5. Force an engine failure using the diagnostic workflow, confirm the document remains editable,
   restart the engine, and confirm the last valid graph is reactivated.
6. Make an unsaved edit, wait for autosave, terminate the UI, relaunch, and accept recovery. Export a
   diagnostic ZIP and inspect its manifest (`frames_included` must be false).
7. Delete the extracted application directory. Confirm the graph saved under Documents remains and
   can be opened after extracting the same or previous accepted release.

Record platform/OS build, VM image or base system, account type, hardware/driver presence, artifact
hash, report path, manual observations, and tester. Any failed required row blocks the release.
Hardware-absent rows are `skipped`, not silently passed.
