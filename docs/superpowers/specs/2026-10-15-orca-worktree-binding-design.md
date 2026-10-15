# cloudctx binds a context to a worktree (Orca support)

**Status:** design approved in conversation (Elias, 2026-10-15); spec awaiting review
**Date:** 2026-10-15
**Target:** v1.6.0

## 1. Objective

Orca (https://www.onorca.dev/, stablyai/orca, v1.4.224 at time of writing) runs
coding agents in parallel, one git worktree per task. Elias wants each Orca
worktree tied to one cloudctx context, so every terminal and every agent in it
runs `az`, `aws`, `terraform` and `terragrunt` against the right tenant and
subscription without anyone typing `cloudctx use`.

Orca has no per-worktree environment setting: no `env` key in `orca.yaml`, no
env field on a worktree, no `--env` on its CLI, and no "terminal opened" hook
(verified in the Orca source, `src/main/hooks.ts`, `src/shared/worktree/meta-types.ts`,
`src/cli/specs/core.ts`). What it does do: every terminal and every terminal
agent starts as an interactive login `$SHELL` (`-l`) with the worktree as its
working directory, and the agent command runs only after the rc files
(`src/main/providers/local-pty-launch-plan.ts`,
`src/main/pty/posix-shell-startup-command.ts`). The cloudctx shim is sourced
from the rc file, so the shell start is where the binding takes effect.

Success, observed by hand in Orca: two worktrees of one repo, bound to two
different contexts. In each, a new terminal's prompt shows its own context, and
`claude` started there runs `az account show --subscription "$ARM_SUBSCRIPTION_ID"`
and lands in the bound tenant and subscription.

## 2. The binding file

`cloudctx bind <name> [--subscription <guid>]` writes `.cloudctx` at the root
of the current git worktree (`git rev-parse --show-toplevel`), or in the
current directory when it is not inside a git repo:

```
context = "alleima-omnia"
subscription = "11111111-2222-3333-4444-555555555555"
```

- `context` must exist in the registry, otherwise `bind` fails with the usual
  unknown-context error.
- `subscription` is optional and must be GUID-shaped (`GUID_RE`). A name is
  rejected for the same reason `ARM_SUBSCRIPTION_ID` is GUID-only today. It is
  also rejected for a context without `azure_tenant` (AWS-only).
- `bind` refuses to write in `$HOME` or `/`: a binding there would apply to
  every shell below it.
- The file is written atomically (temp file in the same directory, then
  `os.replace`) and parsed with the existing registry reader (`_tomllib` or
  `_parse_toml`).
- `bind` appends `.cloudctx` to `$(git rev-parse --git-common-dir)/info/exclude`
  when that line is not already there. The common dir is shared by every
  worktree of the repo, so one entry covers them all and nothing is committed.
- `bind` refuses when `.cloudctx` is tracked by git
  (`git ls-files --error-unmatch .cloudctx` succeeds): a committed binding
  would apply to everyone who clones the repo.
- `cloudctx bind` with no arguments prints the binding that applies to the
  current directory (file path, context, subscription) or `no binding`.
- `cloudctx unbind` deletes the `.cloudctx` at the worktree root. It leaves the
  exclude line in place.

Change from the conversation: the binding is shown by `cloudctx bind` with no
arguments, not by `cloudctx show`. `show <name>` describes a context, its
output format is part of the companion contract (`docs/companions.md`), and a
binding belongs to a folder, not to a context.

## 3. Lookup

The binding that applies to a directory is the first `.cloudctx` found walking
up from that directory to `/`. The binary does this in `find_binding(start)`;
the shims do the same walk in shell first, so a shell that starts outside any
binding never pays for a Python start.

A binding is ignored, with one line on stderr, when its file cannot be parsed
or names a context that is not in the registry.

## 4. Which context wins

| Situation | Result |
|---|---|
| Shell starts in a bound folder | Binding applies, even over an inherited `CLOUDCTX_CONTEXT` (the folder is the more specific signal) |
| `cd` into or out of a bound folder | Nothing changes. Activation happens only at shell start |
| `cloudctx use X` in that shell | `X` wins for that shell, with `X`'s own registry subscription |
| `cloudctx exec X -- cmd` in a bound folder, `X` = binding's context | Binding's subscription pin applies to `cmd` |
| `cloudctx exec X -- cmd` in a bound folder, `X` differs | Runs as `X` with `X`'s registry values, plus one stderr line: `cloudctx: note: this folder is bound to <Y>` |
| No binding | Unchanged from v1.5.0 |

A binding's subscription pin overrides the context's registry subscription in
exactly two exported variables, `ARM_SUBSCRIPTION_ID` and `CLOUDCTX_AZURE_LABEL`,
so the prompt and Terraform both show and use the worktree's subscription.
`env_dict(name, binding=None)` gains the optional binding argument; `_env` gains
`--binding <path>` for the shims; `exec` looks the binding up itself from its
working directory.

No new variable is exported. `CLEARABLE_VARS` stays at nine, so the companion
contract and `tests/test_companion_contract.py` do not change.

The `az` CLI has no environment variable for its default subscription (checked
in azure-cli 2.90.0 `azure/cli/core/_profile.py`; it reads only
`azureProfile.json`). Two worktrees on one context still share that file, so the
pin reaches `az` only through `--subscription "$ARM_SUBSCRIPTION_ID"` on each
call (section 6).

## 5. Shell shims

`shell/cloudctx.zsh` and `shell/cloudctx.bash` each gain a start-up step that
runs once, when the shim is sourced:

1. Walk up from `$PWD` looking for `.cloudctx` (shell tests only, no subprocess).
2. If found, `eval "$(command cloudctx _env --binding <path>)"`, then
   `command cloudctx _decorate <name>`.
3. In an interactive shell, print one line to stderr:
   `cloudctx: <name> (bound by <path>)`.

Nothing is added to `chpwd`, `precmd` or `PROMPT_COMMAND`.

`_decorate` sends the iTerm2-only OSC 1337 sequences (badge, tab colour) only
when `TERM_PROGRAM=iTerm.app` or `LC_TERMINAL=iTerm2`. The OSC 0 title is
always sent. In Orca, whose terminals set `TERM_PROGRAM=Orca`, only the title
changes.

## 6. Agents

An agent started in a bound worktree's terminal inherits the environment from
that shell. `skills/cloudctx/SKILL.md` gains a "Bound worktrees" section:

- When `$CLOUDCTX_CONTEXT` is set at session start, it is the scope; do not
  switch away from it without asking (already the rule, restated here).
- Pass `--subscription "$ARM_SUBSCRIPTION_ID"` on every `az` call that accepts
  it whenever `ARM_SUBSCRIPTION_ID` is set.
- Never run `az account set` in a bound worktree: it changes the default for
  every other worktree on the same context.
- `cloudctx bind` with no arguments shows what the folder is bound to.

After merge, the installed copy at `~/.claude/skills/cloudctx/SKILL.md` is
replaced with the repo file, which also clears the drift noted in the handoff.

## 7. Orca documentation (README, no code)

A README section "Using cloudctx in Orca":

- Bind once per worktree: `cloudctx bind <name> [--subscription <guid>]` in the
  worktree's first terminal; terminals opened after that pick it up.
  Terminals that were already open keep their old environment (Orca freezes
  each terminal's env when it spawns).
- Orca's default Claude command is `claude --dangerously-skip-permissions`,
  which turns off every permission prompt. Set the Claude agent command to
  plain `claude` under Settings, Agents, so approval rules still apply.
- Use terminal agents. Orca's experimental Chat view starts agents without a
  shell, from a cached environment that does not depend on the folder, so it
  never sees the binding.

## 8. Out of scope

- SSH worktrees. Orca does not forward local environment to the remote side;
  cloudctx would have to be installed and logged in on the remote host.
- Showing the context in Orca's own UI (sidebar, plugin panel).
- Following `cd`: switching context when a shell moves between folders.
- Separate `az` config per worktree.
- The Documents workspace router and its context guard, which load only under
  `~/Documents`. Handled, if needed, as a separate claude-workspace subject.

## 9. Testing

- `tests/test_cloudctx.py`:
  - `bind`: writes the file at the git toplevel, validates context and GUID,
    adds the exclude line once, refuses a tracked `.cloudctx`, prints the
    binding when called with no arguments.
  - `unbind`: removes the file, leaves the exclude line.
  - `find_binding`: nearest file wins, stops at `/`, skips an unparsable file
    or unknown context with a warning.
  - `env_dict` with a binding: pin overrides `ARM_SUBSCRIPTION_ID` and
    `CLOUDCTX_AZURE_LABEL`, every other variable unchanged.
  - `exec` in a bound folder: same context applies the pin; a different
    context runs unpinned and prints the note.
  - `_decorate`: OSC 1337 only under iTerm, OSC 0 always.
- `tests/test_shell.py`: real `zsh -f` and `bash --norc` started in a bound temp
  repo activate the context; an inherited context is overridden; `cloudctx use`
  afterwards wins; a shell started outside any binding runs no `cloudctx`
  subprocess.
- `tests/isolation_proof.sh`: two worktrees of one temp repo bound to two
  contexts get different `AZURE_CONFIG_DIR` and `ARM_SUBSCRIPTION_ID`.
- `tests/test_companion_contract.py` passes unchanged.
- Manual acceptance in Orca, as in section 1.

## 10. Delivery

Branch `feature/orca-worktree-binding` in eliknut/cloudctx, `__version__` to
1.6.0, README and skill updated in the same branch, independent review of the
diff, then a pull request that Elias merges. After merge: tag v1.6.0,
self-update the install clone at `~/.local/share/cloudctx`, replace the
installed skill copy, and run the manual Orca acceptance check.
