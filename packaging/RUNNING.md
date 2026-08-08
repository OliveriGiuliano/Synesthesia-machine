# Installed application paths

Synesthesia Machine is a portable standalone directory. Keep all files in the directory together;
launch `SynesthesiaMachine.exe`. Python, `uv`, and a source checkout are not required.

The application writes only per-user runtime state beneath:

| Data | Windows path |
| --- | --- |
| Application root | `%LOCALAPPDATA%\SynesthesiaMachine` |
| Session logs | `%LOCALAPPDATA%\SynesthesiaMachine\logs` |
| Autosave/recovery | `%LOCALAPPDATA%\SynesthesiaMachine\recovery` |
| Qt settings | Windows per-user Qt settings for organization `Synesthesia Machine` |
| User graph documents | Only paths explicitly chosen by the user, normally under Documents |
| Diagnostic exports | Only the destination explicitly chosen by the user |

Deleting the portable application directory does not remove user graph documents or the per-user
data root. To reset application-owned logs and recovery data, close the app and explicitly remove
`%LOCALAPPDATA%\SynesthesiaMachine`. Never remove a user's `.synmachine.json` documents as part of
uninstall or rollback.

If the engine child crashes, the UI remains open and the crash record is written to
`%LOCALAPPDATA%\SynesthesiaMachine\logs\engine-crash.log`. Diagnostic bundles omit frame pixels and
redact paths by default.
