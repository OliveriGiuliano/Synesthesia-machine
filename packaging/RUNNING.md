# Installed application paths

Synesthesia Machine is a portable standalone directory. Keep all files in the directory together;
launch `SynesthesiaMachine.exe` on Windows or `SynesthesiaMachine.bin` on Linux. Python, `uv`, and a
source checkout are not required. On Linux the standalone build also expects the desktop base
libraries (Qt GL/EGL, fontconfig, xkbcommon) and, for the optional debug-audio feature, the system
PortAudio (`libportaudio2`).

The application writes only per-user runtime state beneath:

| Data | Windows path | Linux path |
| --- | --- | --- |
| Application root | `%LOCALAPPDATA%\SynesthesiaMachine` | `$XDG_DATA_HOME/SynesthesiaMachine` (default `~/.local/share/SynesthesiaMachine`) |
| Session logs | `%LOCALAPPDATA%\SynesthesiaMachine\logs` | `<application root>/logs` |
| Autosave/recovery | `%LOCALAPPDATA%\SynesthesiaMachine\recovery` | `<application root>/recovery` |
| Qt settings | Windows per-user Qt settings for organization `Synesthesia Machine` | Per-user Qt settings for organization `Synesthesia Machine` |
| User graph documents | Only paths explicitly chosen by the user, normally under Documents | Only paths explicitly chosen by the user, normally under `~/Documents` |
| Diagnostic exports | Only the destination explicitly chosen by the user | Only the destination explicitly chosen by the user |

Deleting the portable application directory does not remove user graph documents or the per-user
data root. To reset application-owned logs and recovery data, close the app and explicitly remove
`%LOCALAPPDATA%\SynesthesiaMachine` (Windows) or `~/.local/share/SynesthesiaMachine` (Linux). Never
remove a user's `.synmachine.json` documents as part of uninstall or rollback.

If the engine child crashes, the UI remains open and the crash record is written to the
`logs/engine-crash.log` file beneath the platform application root. Diagnostic bundles omit frame
pixels and redact paths by default.
