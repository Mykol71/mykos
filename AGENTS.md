# Production documentation and operations

- Before every batch of changes, check both production documentation folders: `/home/mgreen/mykos/docs` and `/usr/local/share/mykos/docs`. Compare their file hashes with local sources. Fetch and preserve the user's latest edits, especially `KEY_ISSUES.md`, before editing, packaging, or deploying.
- Recheck production docs immediately before deployment. Do not overwrite edits made since the last check; reconcile and rebuild first. Keep user-authored documentation verbatim unless the user requests an edit.
- Preserve the user's production `pos/pos` fixes. Compare the live source before changing it.
- Terminology: an image is a staged OS plus any startup device/memory state needed. A container is a code-specific running instance with its own IP/hostname. A snapshot captures live container state and leaves that container running. Avoid the former user-facing term template.
- Only retain automatic startup captures needed to start an image/container. Intentional snapshots are separate and must not be automatically pruned.
- Never force-remove a shared/in-use image. Preserve running containers and their disks during updates.
- Follow changes with installation instructions, or state that production was already updated.
