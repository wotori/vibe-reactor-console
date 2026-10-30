# Changelog

## 0.12.1 — 2026-10-31

- Added direct X/Y fullscreen for all eight Math Scope modes and all twelve main animations, using the complete viewport with a small controls bar.
- Repeat the same key or press Esc/Z to return to the previous panel/zoom; F switches functions and G selects visuals while fullscreen remains open.
- Added visible fullscreen hints and searchable commands; existing Math scope fullscreen now opens the immersive view.
- Fullscreen keeps terminal processes, source state, native/black backgrounds and Replay ownership intact; child input and quit confirmation retain their own X/Y behavior.
- Validation: 445 passing tests covering all visuals/functions, full viewport rendering, Replay, input ownership and real curses; an additional real tmux check preserved the shell state across fullscreen. Fullscreen is temporary view state, separate from saved visual preferences.

## 0.12.0 — 2026-10-31

- Replaced the fixed sine panel with Math Scope: harmonic interference, moving parabola/tangent, Lissajous, Fourier synthesis, Gaussian wave packet, polar rose, Archimedean spiral and epicycles.
- Added connected ASCII curves, animated parameters, bright tracers and clock-derived fading trails; rendering stays pure and keeps native/black backgrounds.
- F selects the next function; searchable Math scope commands select modes, open fullscreen and configure independent automatic rotation. W still controls visibility.
- Persist function/rotation settings locally and in safe scene capsules; recorded presentation anchors restore changes in Replay without a second local rotation clock.
- Added CLI scope selection/rotation flags, shared options and mathematical formulas/parameter readouts. All graphs are mathematical illustrations, not measured signals.
- Validation: 430 passing tests, including actual curses switching/fullscreen, local persistence/capsule and record/rewind checks; isolated wheel installation exercised all eight renderers. Added actual Renderer PNG/GIF previews. No new dependencies or model calls.

## 0.11.1 — 2026-10-31

- Q, dashboard Ctrl+C and Esc outside menus now request exit confirmation with Stay selected by default; repeated Q never confirms.
- Enter defaults to staying, N/Esc cancel, Y or selecting Exit and Enter confirm. Cancel preserves the previous screen, editor and live sessions.
- Terminal-owned Q/Esc/Ctrl+C still go to the child; live terminals, tasks and managed workers remain running while confirmation is open.
- Validation: 400 passing tests, including keyboard ownership, resize and actual PTY checks for SIGINT cancellation and a live shell surviving the dialog. Explicit `--duration` remains an unattended time limit.

## 0.11.0 — 2026-10-31

- Added U / searchable Source folder selection and `--project-root` for arbitrary local projects, including UTF-8 paths.
- SOURCE types random real code excerpts with PROJECT provenance, actual line numbers, non-repeating next files and reread-on-R; build activity remains simulated.
- Added a bounded background scanner/reader with latest-request ownership, explicit fallback and filters for hidden/secret names, binaries, symlinks and dependency/build directories.
- Automatically save changed visual/options/source preferences after a short debounce and flush on graceful exit; Plasma Reactor now survives a normal restart without pressing S.
- Keep local project paths separate from portable presets, recordings and capsules. Bundled overrides preserve the selected folder; Replay and `--no-config` do not overwrite user settings.
- Validation: 388 passing tests, including actual curses restart/folder tests, Runtime persistence/privacy and worker concurrency/limits; staged wheel installed and exercised from an isolated directory. No new dependencies or model calls.

## 0.10.0 — 2026-10-31

- Connected the binding editor (L): source, ranges, linear/log, exact-time EMA, clamp, missing policy, persistence and spectator director controls.
- Atlas selects files/nodes, reads actual source and staged/unstaged diff, verifies local manifest bytes and prepares/export contexts without launching jobs.
- Replay restores recorded presentation/clock changes and public Arena cards, handoffs, artifacts and independent checks.
- Added playback speed, manual/automatic bookmarks, safe Scene Capsules and public JSON/Markdown mission reports.
- Reconstructs binding EMA analytically across seek/speed changes; recording end markers retain quiet session duration.
- Added explicit HTTP Bearer-token environment selection; connection details are excluded from presets/capsules.
- Hardened public recording/export redaction, stale-selection ownership and Replay read-only keyboard controls.
- Validation: 349 passing tests, including actual curses, local HTTP/Git, record/replay/export and privacy checks; isolated wheel/CLI/Capsule smoke passed. No model calls.
- Added Workbench documentation, verified asset examples and three actual Renderer screenshots. External site/device mapping still requires a selected endpoint/hardware.

## 0.9.0 — 2026-10-31

- Added validated binding configurations with linear/log transforms, exact-time EMA, missing-signal policies and immutable provenance.
- Added a bounded Event Director with cue priorities, debounce/hysteresis, spectator recommendations and deterministic reconstruction.
- Added an asynchronous Atlas inspector for selected real source/staged/unstaged diff and bounded handoff context.
- Added local asset catalog parsing and byte/hash verification with explicit fixture/declaration/consumer boundaries.
- This checkpoint exposes the tested engines; their new interactive controls are integrated in the following workspace checkpoint.
- Validation: 43 focused engine/Atlas tests plus the complete milestone regression; no model calls.

## 0.8.0 — 2026-10-31

- Connected seven responsive screens: Cockpit, Visual Lab, Arena, Site, Body, Atlas and Replay.
- Added searchable commands, help, panel focus/zoom, six safe presets and atomic preference saving.
- Integrated LIVE/DEMO bindings, actual Git/task results, event recording/replay and private Stream View.
- Connected two terminal slots and edited public handoffs; added artifact/check inspectors and global managed Stop.
- Builder/Challenger now reviews exact immutable source snapshots with at most two fix/review cycles.
- Added explicit three-way source candidate creation with retained conflict artifacts; original workspaces are preserved.
- Fixed replay timing/freshness, sticky live pulse selection, expired audience visuals, test-result replacement and modal keyboard ownership.
- Added runnable data examples, actual UI screenshots and implementation/connection documentation.
- Validation: 250 passing tests, including real PTY/local-source and fake-agent integration checks; wheel packaging and renderer visual QA. No model calls.
- External website/account mapping and hardware-specific BLE remain separate integrations; generic JSON/HTTP/BPM/RR inputs work.

## 0.7.0 — 2026-10-31

- Added two persistent, independently focused tmux terminal slots and bounded input queues.
- Added finite Demo, human-owned Watch and explicit managed Relay/Mission services.
- Managed adapters discover CLI capabilities, preserve session IDs and bound output, time and deliveries.
- Added isolated snapshot/worktree preparation, actual patch artifacts and independent validation tied to content hashes.
- Validation: 30 focused terminal/agent tests, including two real PTYs and two fake JSONL subprocesses; no model calls.

## 0.6.0 — 2026-10-31

- Added immutable telemetry events, bounded queues and explicit DEMO/LIVE sources.
- Added JSON file, JSONL/FIFO and HTTP polling adapters with TTL, backoff and honest missing values.
- Added read-only Git snapshots and an explicit asynchronous task runner with actual exit codes.
- Added opt-in JSONL/gzip recording, rotation, checkpoints and isolated read-only replay.
- Validation: 35 focused data/recording/repository/task tests plus milestone regression.

## 0.5.0 — 2026-10-31

- Unified visual descriptors, render functions, captions and binding metadata.
- Added Gravity Well, Torus Knot, Packet Garden, Twin Core, Voxel Tide and Asset Orrery.
- Reactor supports numeric energy, particle-count and pulse-frequency bindings.
- New visuals are selectable through G and participate in automatic cycling.
- Validation: pure visual bounds, deterministic motion and immutable parameter tests;
  full milestone regression and wheel checks recorded with the checkpoint.

## 0.4.1 — 2026-10-31

- Added the product and technical evolution specification: seven screens, data
  contracts, event-driven visuals, multi-agent workflows and incremental roadmap.
- Added a standalone interactive screenbook with explicitly simulated values.
- Added repository `AGENTS.md` and `devos-workflow` skill requiring versioned,
  tested logical commits, changelog updates and progress pushes.
- Validation: checked specification links, JSON/JSONL examples, JavaScript syntax
  and prototype interactions. Runtime behavior is unchanged in this checkpoint.

## 0.4.0 — 2026-10-30

- Full-screen coding simulator with Ekza source excerpts and shared theme options.
- Six ASCII visuals, native/black backgrounds, sine scope and automatic cycling.
- Optional real tmux-backed terminal, Codex startup, input focus and resize support.
- Baseline: 75 tests covering simulation, rendering, sources, graphics and PTY behavior.
