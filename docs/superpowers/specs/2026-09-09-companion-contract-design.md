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
