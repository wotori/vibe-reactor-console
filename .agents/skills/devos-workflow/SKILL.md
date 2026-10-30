---
name: devos-workflow
description: Develop and release logical increments of this DEV/OS repository with tested commits, semantic versions, CHANGELOG.md and progress pushes. Apply to changes in dev-os, not unrelated repositories.
---

# DEV/OS development workflow

The user requests persistent progress at logical implementation milestones:
**implement, verify, update the version/changelog, commit, then push**.
This authorization applies to ordinary repository progress; do not ask again
before each commit or push. Work that requires separate authorization under the
user's instructions (such as deployment or credential changes) stays separate.

## Checkpoints

- Choose a cohesive, reviewable increment: a completed feature, compatibility
  refactor, fix or documentation milestone. Do not commit every tool call or leave
  a whole multi-stage implementation uncommitted until the end.
- Keep commits runnable. Run relevant checks for the increment; use the full
  `python3 -m unittest discover -s tests` suite for integrated runtime milestones.
  Verify wheel packaging when module discovery or packaged assets change.
- Review `git status`, the intended diff and staged diff. Stage explicit owned
  files, especially when several agents share the working directory. Never mix
  another agent's incomplete changes into a checkpoint.
- Use Conventional Commits, e.g. `feat(visuals): add gravity well and visual registry`.
  A coordinating agent owns integration, release files and commits during parallel work.

## Version and changelog

- Canonical package version: `devos/__init__.py::__version__`; packaging reads it.
- Every checkpoint updates root `CHANGELOG.md` with date, version and user-visible
  behavior; include significant limitations and validation when relevant.
- Use SemVer: minor for a coherent new feature milestone, patch for fixes or
  maintenance, major for incompatible public behavior. Related follow-up work
  before the next feature milestone can be a patch. Do not publish a new minor
  number for each individual module in one cohesive feature.
- Keep the changelog newest first. Mark planned features as planned; never list
  a demo, adapter stub or disconnected source as a working live integration.
- CLI/version tests must follow the package version rather than a stale literal.
  Create release tags only for a deliberately finished release, not an incomplete checkpoint.

## Push

- Push each verified checkpoint to the current configured upstream. If there is
  no upstream but `origin` is configured, use the current branch and set its upstream.
- Never force-push, rewrite unrelated history, change CI/CD or deploy as a side
  effect of saving progress.
- If no remote exists, request the repository URL once and continue committing
  useful local work. Do not invent a destination or create a public repository.
- On rejected push, fetch and inspect the divergence; preserve other work. If
  authentication or permissions fail, report the concrete problem and retain
  local commits; do not change credentials or loop indefinitely.
- At handoff report version, commit IDs, checks and whether push succeeded.

## Product invariants

- Python 3.9+, stdlib core; optional installed tmux for real terminal sessions.
  Adding new mandatory runtime dependencies requires the user's approval.
- Renderer does no I/O and does not advance simulation. Workers publish bounded,
  immutable state. Live collection has its own clocks and freshness.
- Terminal input belongs to exactly one selected child or dashboard. Keep F2,
  F3 and Ctrl+] compatibility; ordinary typed text never becomes dashboard commands.
- Native/absolute black background remains available. DEMO/LIVE/REPLAY and missing
  samples are explicit; unknown is not zero or success.
- No real agent/model job, task command, recording or network connection starts
  just because an imported preset exists. Explicit user-selected commands/actions
  start them. Tests use fake agents and local fixtures, not paid model calls.
