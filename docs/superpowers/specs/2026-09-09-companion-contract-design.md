# cloudctx companion contract — surfaces other tools may depend on

**Status:** approved design (agreed in chat 2026-09-09)
**Date:** 2026-09-09

## 1. Objective

`pimctl` (Lars Åkerlund's PIM role-activation tool) drives cloudctx from the
outside: `cloudctx exec <ctx> -- az …`, `cloudctx _names`, the `key = value`
lines of `cloudctx show`, `$CLOUDCTX_CONTEXT`, the managed-variable list and
the literal "unknown context" error phrase. None of that is documented as
stable, and one internal command (`_names`) is doing public duty. Three
changes make the seam explicit:

1. a public, machine-readable context listing (`cloudctx list --names`);
2. a per-context store path exported into the environment
   (`CLOUDCTX_STORE`), so a companion can keep its own state *inside* the
   context and `cloudctx delete` sweeps it;
3. a written contract (`docs/companions.md`) pinned by tests.

## 2. Behavior

### `cloudctx list --names`

Prints the registered context names, sorted, one per line, and nothing else.
An empty registry prints nothing and exits 0 (the human form's "no contexts"
sentence is not printed). `--names` and `-v` are mutually exclusive.
`_names` stays as an internal alias with identical output; the shell shim keeps
using it.

### `CLOUDCTX_STORE`

`env_dict` exports `CLOUDCTX_STORE=<resolved ~/.cloudctx/<name>>` for every
context, alongside `CLOUDCTX_CONTEXT` and `AZURE_CONFIG_DIR`. It joins
`CLEARABLE_VARS`, so `cloudctx clear` unsets it and `cloudctx exec` strips an
inherited one before overlaying the target context's. `cloudctx show` prints
the store path as its first path line so the value is discoverable.

Companions keep per-context state under `$CLOUDCTX_STORE/<tool>/`. Nothing in
cloudctx needs to know the tool exists: `delete` already removes the whole
store, and `--keep-store` keeps it.

The name is `CLOUDCTX_STORE`, not `CLOUDCTX_DIR`: `CLOUDCTX_HOME` already
names the *root* (`~/.cloudctx`), and "store" is the word `delete`, `show` and
the README already use for the per-context directory.

### The written contract

`docs/companions.md` lists every surface a companion may rely on, with its
exact shape: `exec` semantics, `list --names`, the `show` line format, the
exported variables, the managed-variable list, the error phrase and exit code,
and which commands are internal. `tests/test_companion_contract.py` pins each
one, so a change to any of them fails the suite and has to be deliberate.

## 3. Constraints

- Stdlib only; no new dependencies.
- No behaviour change for existing commands beyond the additions above:
  `list` without flags, `_env`, `exec` and `show` keep their current output,
  plus one exported variable and one `show` line.
- The site's variable table gains the same row as the README (the folder is
  the deployed artifact; redeploy is a separate step).
- `__version__` becomes 1.4.0; tagging is done at merge.

## 4. Testing

`tests/test_companion_contract.py`, reusing the `Base` harness: `--names`
output and empty-registry behaviour, `_names` parity, `--names`/`-v`
exclusivity, `show` line format, `CLOUDCTX_STORE` in `_env`/`exec`/`clear`,
the exact `CLEARABLE_VARS` list, the error phrase for `exec`/`show`, the
empty-registry sentence of `list`, and `delete` sweeping a companion
directory under the store (kept with `--keep-store`).

## 5. Addendum: pimctl 0.2.0 audit (2026-09-09)

pimctl went public the same day (github.com/larsakerlund/pimctl, v0.2.0) and
now requires cloudctx >= 1.4.0. Reading its `internal/azauth/cloudctx.go`
against this contract found three gaps, closed in the doc and tests:

- **`--version` joins the contract.** pimctl gates on `cloudctx --version`,
  parsing `cloudctx X.Y.Z` from stdout (cached per binary for a day). The
  contract had no word on it, so a format change would have broken the gate
  silently. Pinned as one stdout line, `cloudctx <semver>`, exit 0.
- **The human `list` sentence leaves the contract.** It was pinned "until no
  companion needs it"; pimctl 0.2.0 reads `--names` only and never parses the
  human listing. The test and the promise are removed.
- **`store:` is named as contract, not just described.** The doc told
  companions to parse `key = value` lines only, while pimctl (and our own
  test) read the `store:` path line. The doc now says both are the contract
  and the other path lines are not.

Verified unchanged from pimctl's side: `exec`, `list --names`, the seven
managed variables (its `cloudctxVars` mirrors `CLEARABLE_VARS` exactly), and
the "unknown context" stderr phrase, which it matches case-insensitively.
