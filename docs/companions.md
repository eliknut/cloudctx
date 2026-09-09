# Companion contract

cloudctx is one tool in a chain: other tools drive it from the outside to get
per-customer credential isolation without reimplementing it. pimctl (batch
activation of Azure PIM roles) is the first. This page lists the surfaces a
companion may rely on, with their exact shape. Each one is pinned by
`tests/test_companion_contract.py`, so changing one is deliberate: it fails the
suite, and ships with a minor version bump.

Anything not on this page, in particular every `_`-prefixed command (`_env`,
`_names`, `_decorate`, `_refresh-update-check`), is internal and may change
without notice.

## Running a command in a context

    cloudctx exec <name> -- <command> [args...]

- The child runs with the context's environment (table below) overlaid on the
  caller's, after every managed variable has been stripped from the caller's.
  "No context" therefore means none of the managed variables are set, and a
  context switch replaces the previous context's variables entirely.
- The child's exit code is returned unchanged.
- `--` is optional but recommended.
- Credentials are only ever touched by the command you run; cloudctx does not
  log in, refresh or read tokens itself.

## Listing contexts

    cloudctx list --names

One registered name per line, sorted, nothing else. An empty registry prints
nothing and exits 0. Mutually exclusive with `-v`.

`cloudctx list` without flags is for people. Its empty-registry output is the
single line `no contexts. Create one with: cloudctx new <name>`; pimctl v0.1.1
still matches that sentence, so it stays pinned until no companion needs it.

## Reading a context's registry entry

    cloudctx show <name>

Line 1 is `[<name>]`. Then one `key = value` line per registry field, sorted by
key; the keys are the `--` flags of `cloudctx new` with dashes as underscores
(`azure_tenant`, `azure_subscription`, `display`, `color`, `aws_profile`, ...).
Then a blank line and path lines, of which `store:` is the first:

    [acme]
    azure_tenant = 9f0e...
    display = Acme AB

    store:           /Users/me/.cloudctx/acme
    azure store:     /Users/me/.cloudctx/acme/azure  (exists)
    aws config:      /Users/me/.cloudctx/acme/aws/config  (missing)
    aws credentials: /Users/me/.cloudctx/acme/aws/credentials  (missing)

A companion reading a field should parse the `key = value` lines only and
ignore everything else.

## Environment a context exports

| Variable | Value | Set |
|---|---|---|
| `CLOUDCTX_CONTEXT` | the context name | always |
| `CLOUDCTX_STORE` | `~/.cloudctx/<name>`, resolved | always |
| `AZURE_CONFIG_DIR` | `$CLOUDCTX_STORE/azure` | always |
| `CLOUDCTX_AZURE_LABEL` | the pinned subscription, for prompts | when the context has one |
| `AWS_CONFIG_FILE` | `$CLOUDCTX_STORE/aws/config` | when the context defines AWS fields |
| `AWS_SHARED_CREDENTIALS_FILE` | `$CLOUDCTX_STORE/aws/credentials` | ditto |
| `AWS_PROFILE` | the context's profile name | ditto |

These seven are the **managed variables**. `cloudctx clear` unsets all of
them, and `exec` strips them from the caller before overlaying. A companion
that spawns its own "no context" child should strip the same seven.

`$CLOUDCTX_CONTEXT` set in a shell means `cloudctx use` selected that context
there; a companion may treat it as the ambient selection.

## Where a companion keeps state

Under `$CLOUDCTX_STORE/<tool>/`, for example `~/.cloudctx/acme/pimctl/`. That
puts it inside the isolation boundary: `cloudctx delete <name>` removes it with
the rest of the store, `--keep-store` keeps it, and nothing in cloudctx needs
to know the tool exists. Never write into `AZURE_CONFIG_DIR` or the AWS files
except through `az` and `aws` themselves.

## Errors

An unknown context, on any command that takes one, prints exactly

    cloudctx: error: unknown context '<name>'

to stderr, prints nothing to stdout, and exits 1.

## Not part of the contract

- The human-oriented output of `list` (other than the empty sentence above),
  `status`, `login` and `new`, and the iTerm2 decoration.
- Every `_`-prefixed command. `_names` happens to equal `list --names` today
  and the shell shim uses it; do not depend on it from outside.
- The registry file format (`contexts.toml`). Read it through `show`.
