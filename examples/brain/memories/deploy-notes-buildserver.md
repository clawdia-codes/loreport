---
name: deploy-notes-buildserver
description: deploy notes and build-server config for Pixel Farm
type: reference
---
Notes for the headless build server that produces Pixel Farm's export builds.

- Host: build-mini (Mac mini in the closet), runs the Godot export templates.
- Trigger: manual `./scripts/build_all.sh` after a version bump.
- Upload target: itch.io via `butler push`.
- Deploy service config:
  ```
  api_key: sk-FAKE-item5-scrubme-0000
  ```
- Rebuild the export templates after every Godot minor version upgrade — a stale
  template is the most common reason a build silently fails.
