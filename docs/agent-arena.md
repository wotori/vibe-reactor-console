# Agent Arena: what runs today

DEV/OS can show two independent interactive terminals or run a finite managed
conversation using the installed Codex and Claude CLIs. Demo requires neither
CLI nor an account. Opening a preset never starts a model request.

## Demo

```bash
python3 dev_show.py --no-config --preset duet
```

The local story advances through parallel work, two handoffs and a final result,
then stops. Every response and check in this mode is labelled DEMO. Press **E**
or **H** to advance it manually. No project files or model APIs are touched.

## Watch: two real terminals

```bash
python3 dev_show.py --no-config --screen arena --agents watch \
  --command codex --right-command claude --cwd /path/to/project
```

This uses the optional installed `tmux` executable. Each terminal has its own
private server, input, size and screen state. These are ordinary interactive CLI
sessions controlled by you; Watch does not automatically relay their text.

- **F2** or **Ctrl+]** toggles terminal input and dashboard input.
- **F4** selects/focuses the other terminal.
- **O** changes the selected dashboard panel; **Z** expands it.
- **3** opens Arena; **P** opens commands; **?** shows help.
- On a narrower terminal, one terminal is shown at a time. Hidden jobs continue.
- Leave terminal input before using **Q** to quit DEV/OS. Inside a terminal,
  keystrokes, including Ctrl+C, go to its child process.

The two terminals appear side by side from 160 columns. Their capture rate is
reduced when hidden; graphics do not wait for terminal capture subprocesses.
Mouse forwarding is not implemented. One paste is limited to 16 KiB, and the
input backlog is bounded.

## Relay: inspect, edit and send

Run from the DEV/OS checkout, replacing the source project path. The destination
must be new and outside that source project:

```bash
python3 dev_show.py --no-config --screen arena --agents relay \
  --mission "Compare two designs for a bounded event queue; discuss tradeoffs." \
  --cwd /path/to/project --prepare-workspaces /tmp/devos-relay-001 \
  --scenario design-rally --handoffs 6 --mission-timeout 900
```

Two initial managed requests run independently. After both finish, Relay waits
at `READY`; it does not send either response automatically. Select the sender's
panel, leave terminal input and press **H**. The composer opens with its completed
public summary. **Ctrl+U** clears it, Backspace edits, Enter sends and Esc cancels.
The current composer accepts ASCII keyboard edits; existing Unicode summaries
are preserved. The recipient must be idle. Each explicit handoff starts one turn.

Managed output is parsed from JSONL, not guessed from terminal prompts or ANSI
screens. Token deltas never trigger another agent. Provider session IDs are
retained separately and resumed explicitly; `--last` is never used.

## Mission: bounded task graphs

Change `--agents relay` to `--agents mission` to run a finite task graph:

1. Both initial tasks start in separate working directories.
2. The coordinator waits for both completed public results.
3. Design Rally exchanges the proposals for one follow-up critique each.
4. Builder/Challenger follows the ordered review/fix graph below. Split performs
   one exchange and stops for explicit candidate integration.

`design-rally` uses read-only profiles and produces proposals plus critique.
`builder-challenger` gives Codex the implementation and Claude initial edge cases.
`split` assigns core work to Codex and tests/documentation to Claude, each in its
own editable directory.

For **Builder/Challenger**, the actual order is:

1. Codex implements while Claude independently prepares acceptance cases.
2. Once both finish, Claude receives the current builder files in a fresh,
   versioned snapshot, plus an actual diff preview and the initial cases.
3. Claude returns a completed JSON verdict: `approved`, `changes_requested` or
   `blocked`, a nonempty `summary`, and optional `acceptance` checks. Malformed
   or extra command fields stop at `NEEDS_USER` without being relayed.
4. `changes_requested` sends the concrete review to Codex for a fix.
5. After the fix, a new file snapshot goes to Claude for a new review. The
   original review copy remains unchanged.
6. A second fix/review is allowed if necessary. Two fixes is the fixed maximum:
   at most five handoffs and seven agent turns, including the first parallel pair.

The coordinator checks that the reviewed snapshot and current builder manifest
still match. External changes invalidate the review. An unchanged requested fix
stops as `NO_PROGRESS`; an unresolved verdict after two fixes stops as
`FIX_REVIEW_LIMIT`. A lower `--handoffs` cap takes precedence.

An approved review still stops at `NEEDS_USER` for an explicit independent check;
it never sets `verified` by itself. A blocked review also stops for user input.
The UI can inspect the current phase and number of fix cycles. Split results can
be combined using the explicit candidate operation below. This never merges
changes into the source project, commits, deploys or resets your source tree.

Defaults are six handoffs and 15 minutes. You can reduce them with `--handoffs`
and `--mission-timeout`; hard parser limits are 20 handoffs and one hour. Duplicate
message IDs are not delivered again. Repeated identical handoff content/artifact
hashes stop as `NO_PROGRESS`. Ambiguous process completion becomes
`DELIVERY_UNKNOWN`, without an automatic retry.

Use **P → Mission / pause or resume** to stop issuing new turns while current
ones finish. **!** requests stop and cancellation. Cancellation remains visible
until the local worker exits; stopping a CLI process does not certify that a
remote request was undone. Managed workers keep draining while a menu is open
or dashboard animation is paused.

## Working directories and artifacts

`--prepare-workspaces` defaults to a bounded snapshot. It creates separate
`base/`, `codex/` and `claude/` directories and a `manifest.json`; all are retained after
exit for inspection. Snapshots omit symlinks, `.git`, provider configuration,
common dependency/cache directories, credential/key file names and files larger
than 1 MiB. The snapshot limit is 2,000 included files / 32 MiB. This is a selected
source snapshot rather than an entire environment or dependency installation.
`base/` retains the attested initial bytes for later three-way integration. Neither
agent receives write access to it. Changes to the original source project or
agent workspaces after preparation do not alter this retained base. The manifest
records its path and actual included-file digest.

`--workspace-mode worktree` instead creates two detached Git worktrees at the
existing HEAD commit. It requires a base commit and does not include uncommitted
source changes. DEV/OS never makes an initial commit just to enable this mode.
Worktrees isolate working files; they are not an operating-system sandbox.
The retained base and manifest in this mode come from the detached HEAD checkout,
so dirty or untracked source files do not silently enter the baseline.

You can supply already prepared roots instead:

```bash
python3 dev_show.py --no-config --agents mission \
  --mission "Review the supplied project design." \
  --workspace-left /path/to/isolated-codex \
  --workspace-right /path/to/isolated-claude
```

The resolved roots must be distinct and must not nest. Symlink aliases resolving
to the same directory are rejected. Agent-supplied file URLs and artifact paths
are never treated as authority to read arbitrary local files. Actual patch
capture reads only the coordinator's configured workspace and is bounded for
display/context; inspect retained workspaces for full changes or large binaries.
Builder review copies live under the Claude workspace's
`.devos/reviews/<version>/files/`, with their canonical manifest beside them.
The coordinator only appends new version directories and does not replace
existing reviewer source files. Nonregular files, including FIFOs, are excluded
from snapshots so artifact capture cannot block on them.

## Explicit Split integration

After the agents finish, combine their changes into a new candidate directory:

```bash
python3 dev_show.py --integrate \
  /tmp/devos-split-001/base \
  /tmp/devos-split-001/codex \
  /tmp/devos-split-001/claude \
  /tmp/devos-candidate-001
```

The destination must be new and outside the three sources. This standalone
operation works without curses and does not start models or execute commands.
It compares bounded files against the retained base: independent changes are
copied, identical changes agree, and divergent bytes or structural filename
collisions become conflicts. It does not attempt a line-based automatic merge.

Conflicts preserve the base version in the candidate and retain both alternatives
under `.devos/integration-conflicts/`. The JSON report is
`.devos/integration-report.json`. The source, base and agent roots remain unchanged.
Inspect the report and resolve conflicts in the new candidate, then run explicit
checks there, for example:

```bash
cd /tmp/devos-candidate-001
python3 -m unittest discover -s tests
```

A conflict-free candidate is ready for review, not automatically verified.
Successful checks in the earlier Codex or Claude workspace do not certify this
combined candidate. Applying the candidate to the original project remains a
separate explicit decision.

## Tool profiles and installed capabilities

Before an explicit managed launch, DEV/OS probes CLI help for required flags.
Missing executables or unsupported restricted interfaces fail clearly and leave
Demo/Watch available. Existing CLI authentication is reused; DEV/OS does not
change credentials, install an SDK or promise account access. Authentication
errors are shown as actual provider failures.

- Codex uses `exec --json`, `--ignore-user-config`, an explicit `read-only` or
  `workspace-write` sandbox, and `approval_policy="never"`. Resume addresses the
  exact stored session ID. See [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode).
- Claude uses `-p --output-format stream-json --verbose --restricted`, a strict
  empty MCP configuration and explicit built-in file tools. Read-only receives
  Read/Glob/Grep; editable receives those plus Edit/Write. Bash, code execution
  and subagents are not enabled. Editable Claude can prepare tests but cannot run
  shell checks through this profile. Older CLIs without `--restricted` are not
  presented as safely equivalent. See [Claude CLI reference](https://code.claude.com/docs/en/cli-reference).
- No permission-bypass flags are added. A prompt or peer message cannot widen the
  selected profile, change cwd or answer an approval on your behalf.

The UI shows provider-reported token usage and Claude's reported cost when
available. An unknown cost stays unknown. Time/handoff caps are local limits,
not a guaranteed dollar ceiling. Only public completed replies are stored;
hidden reasoning is not reconstructed.

## Independent validation

Configure a check explicitly with `--task`, for example:

```bash
python3 dev_show.py --no-config --agents relay \
  --mission "Propose and review this project's design." \
  --cwd /path/to/project --prepare-workspaces /tmp/devos-check-001 \
  --task "python3 -m unittest discover -s tests"
```

After managed turns finish, select the workspace panel and choose
**P → Mission / validate selected workspace**. The argv is run in that isolated
directory. A report contains argv, cwd, the included-file manifest hash, elapsed
time, actual exit code and bounded stdout/stderr. Success requires the selected
checks to exit zero and the included-file manifest to remain unchanged while
they run. Provider statements such as “tests passed” do not satisfy this gate.

`READY_FOR_USER` on a Design Rally means proposals are ready to inspect; it does
not imply tested code. The separate `verified` flag requires actual independent
validation. Validation verifies the selected workspace and command, not a future
integration of two different workspaces. Check counts/coverage are not inferred
from an exit code, and normal **Task / run configured check** is separate from
the mission verification gate.

## Verification and current boundaries

Automated tests exercise the entire managed graph with local fake JSONL
subprocesses, including parallel starts, exact resumes, manual relay, edited
messages, cancellation, deadlines, bounded stderr, malformed output and real
validation commands. The ordered Builder/Challenger fixture checks both fix
cycles, current bytes, retained previous snapshots and every bounded stop.
Tests do not call either model API. Two real tmux shells are also checked for
independent input/resize and private-server cleanup.

No automatic source merge, SDK approval bridge,
live token transcript, guaranteed spending cap or managed-session restoration
across DEV/OS restarts is implemented. Outputs remain in the explicit working
directories. Event recordings contain normalized Arena events rather than the
full managed-session state; replay never launches agent turns or terminal jobs.

## Public reports and Replay Arena

**P → Arena / export public mission report** creates a new JSON/Markdown report
under `--export-dir` (or the DEV/OS config exports directory). It includes public
results, artifact hashes, actual recorded check evidence and remaining work.
Paths, model session identities, raw check output and credential-like public text
are redacted by default. Export itself runs no command and never overwrites an
existing report directory.

New event recordings contain bounded `arena.snapshot` and `presentation.changed`
states. In Replay Studio, choose **P → Replay / toggle Arena reconstruction** to
inspect the recorded cards/handoffs/checks. Playback does not resume model sessions
or expose human-owned terminals. **M** adds a marker; during recording the marker
is written into the log, while markers added to a loaded replay stay in memory
and can be included in a portable capsule. Playback speed and seek preserve
source values and recorded simulation clock anchors.
