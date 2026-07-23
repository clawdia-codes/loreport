---
name: godot-export-pipeline
description: Godot 4 export pipeline for Windows/Mac/Linux builds
type: knowledge
---
How Godot 4 turns a project into shippable builds for the three desktop targets.

- Install export templates matching your exact Godot version (Editor → Manage Export
  Templates) — a version mismatch is the #1 cause of a build that runs in-editor but
  fails on export.
- Each platform needs its own export preset (Project → Export): Windows Desktop, macOS,
  Linux/X11. Set the executable name and icon per preset.
- macOS builds need code signing / notarization for distribution outside the App Store,
  or players get a Gatekeeper warning on first launch.
- Linux builds ship as a single binary + `.pck` data file; no extra runtime needed on
  the player's machine.
- Automate the three exports with `godot --headless --export-release "<preset>" <path>`
  per platform, driven from one build script.

Used by [[shipping-pixel-farm-game]] for release builds.
