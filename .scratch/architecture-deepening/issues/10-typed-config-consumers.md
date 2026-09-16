# 10: Migrate consumers to the typed config, delete string re-derivation

**What to build:** Every consumer of a source node's configuration — the media layer, engine source creation, midi export, persistence/relinking, the editor's file-drop and parameter editor, and random-graph generation — reads the typed source config, and the raw parameter-name string matching is gone. Renaming or adding a source parameter now touches the definition and the typed config, not a dozen files.

**Blocked by:** 03 (Introduce a typed source-configuration seam)

**Status:** ready-for-agent

- [ ] All consumers read the typed source config; the raw parameter-name string matching (the `file_path` special-case in the parameter editor and the file-drop path) is removed.
- [ ] A rename/add of a source parameter changes only the definition + typed config (verify: a rename touches ≤2 files).
- [ ] Editor video-suffix and drop classification derive from the node-kind policy, not from a hard-coded widget constant.
- [ ] Existing source/export/persistence/clipboard behaviour is unchanged.
