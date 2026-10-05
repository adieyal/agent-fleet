# Workspace storage

Workspace owns project identity, explicit host-label links, capacity, stable floors,
focus and shutter state in the controller store. CLI and HTTP mutations use
`open_workspace()` and the same Workspace facade. Registry edits, move-in, merge,
shutter and restore each commit with state history in one unit of work. Repeated
settlement with unchanged placement produces no history.

The first open imports `config.json` and `workspace.json`, preserving project IDs,
links and placement. Both source files remain; each existing file gets a
`.workspace.bak` backup. Later JSON edits do not replace accepted workspace state.
Host connection settings and display settings remain in config, written atomically.
Use `fleet building capacity` to inspect or change capacity (six initially, ten at
most). Existing project commands retain their argument and output contracts.

The aggregate is stored together because floor allocation, registry changes and
shutter transitions must share a write lock. A transaction reads the current
aggregate after acquiring SQLite's immediate writer lock; it never persists a
snapshot read before that lock. Reads use detached registry and choice snapshots.
