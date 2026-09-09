# cloudctx

Per-window Azure (and, later, AWS) CLI context isolation. Pin each iTerm2 window
to exactly one named cloud identity, so `az login` or `az account set` in one
window can never silently change the active subscription in another.

> **v1 status:** Azure isolation is complete and proven. AWS fields are
> first-class in the registry and exported when present, but the `aws sso login`
> automation is not wired yet — see [AWS](#aws-status).

**Website:** the landing page lives in [`site/`](site/) and is deployed to a
[Loft](https://github.com/larsakerlund/loft) instance with
`loft deploy site cloudctx` (zero build step — the folder is the artifact).

## Why

The Azure CLI keeps all of its state — the MSAL token cache *and* the active
subscription (`azureProfile.json`) — inside a single directory, `AZURE_CONFIG_DIR`
(default `~/.azure`). Every shell shares it, so logging in to a different customer
tenant in one window repoints every other window. cloudctx gives each context its
own `AZURE_CONFIG_DIR`, so two windows are fully independent.

## How it works

A **context** is a credential bundle under `~/.cloudctx/<name>/`. Selecting it
exports a fixed set of environment variables into the current shell:

| Variable | Points at |
|---|---|
| `AZURE_CONFIG_DIR` | `~/.cloudctx/<name>/azure/` (token cache + active subscription) |
| `CLOUDCTX_STORE` | `~/.cloudctx/<name>/` itself, for companion tools that keep per-context state (see [Companions](#companions)) |
| `AWS_CONFIG_FILE` | `~/.cloudctx/<name>/aws/config` *(when the context defines AWS fields)* |
| `AWS_SHARED_CREDENTIALS_FILE` | `~/.cloudctx/<name>/aws/credentials` *(ditto)* |
| `AWS_PROFILE` | the context's profile name *(ditto)* |

Because selection is purely env-var driven and the active Azure subscription lives
*inside* `AZURE_CONFIG_DIR`, two windows with different exports cannot interfere.

A tiny sourced shell function (also named `cloudctx`, shadowing the binary) does the part a binary can't — mutating the
parent shell — by `eval`-ing `cloudctx _env <name>`; everything else is the
zero-dependency `cloudctx` Python CLI.

## Install

Requires Python 3.9+ (stdlib only) and, for Azure auth, the Azure CLI.

```sh
git clone https://github.com/eliknut/cloudctx ~/code/cloudctx
ln -s ~/code/cloudctx/cloudctx ~/bin/cloudctx     # put it on PATH

# print (or --write) the line that sources the shell shim:
cloudctx install                 # zsh
cloudctx install --shell bash
cloudctx install --write         # append to your rc automatically
```

Then open a new shell. Optional extras:

- **Starship:** paste `starship/cloudctx.toml` into `~/.config/starship.toml`.
- **iTerm2 dynamic profiles:** generated automatically on `cloudctx new`; pick a
  customer from the iTerm2 profile menu to get a correctly scoped, color-coded
  window. Regenerate manually with `cloudctx gen-profiles`.

## Usage

```sh
cloudctx new acme --display "Acme AB" --color "#c0392b" \
    --azure-tenant <tenant-guid> --azure-subscription "<sub-name-or-guid>"
# scaffolds ~/.cloudctx/acme, writes the registry, regenerates iTerm2 profiles,
# then runs `az login --tenant ... && az account set --subscription ...` into
# the isolated store. Add --no-login to scaffold only, --device-code for headless.

cloudctx use acme          # export env into THIS shell + set iTerm2 badge/title/tab color
cloudctx list              # list contexts; * marks the one active in this shell
cloudctx list -v           # ...with display/azure/aws columns
cloudctx list --names      # bare names, one per line (scripts, companion tools)
cloudctx show acme         # full registry entry + store paths
cloudctx status            # the REAL identity here: az account show + aws sts
cloudctx login acme        # re-authenticate; verifies the landed tenant matches
cloudctx exec acme -- terraform plan   # run one command in a context; shell unchanged
cloudctx open acme --cd ~/proj --claude  # new scoped iTerm2 window, cd, run claude
cloudctx claude acme ~/proj              # shorthand for the above
cloudctx clear             # unset the vars + reset the window decoration
cloudctx delete acme       # remove the registry entry AND ~/.cloudctx/acme
                           #   (asks first; --force to skip, --keep-store to keep files)
cloudctx self-update       # git pull the install + regenerate iTerm2 profiles
                           #   (--check only reports whether a newer tag exists)
# interactive commands print a one-line stderr notice (TTY only, cached,
# refreshed in the background at most daily) when a newer tag exists.
# Opt out with CLOUDCTX_NO_UPDATE_CHECK=1.
```

Add `--dry-run` to `login`/`open`/`claude` to print the commands instead of
running them. The shim registers tab completion for subcommands and context
names in both zsh (needs compinit before sourcing) and bash.

### The guard

The shim warns if you run `az`/`aws` while no context is selected, so you never
accidentally touch the global default store:

```
$ cloudctx clear
$ az account show
cloudctx: WARNING — 'az' run with no context selected (using global default store). Run 'cloudctx use <name>' first.
```

## Claude Code

Claude Code inherits the shell environment at launch and passes it to every
subprocess, so any `az`/`aws`/Terraform it runs uses the context's isolated
config automatically. **Only start Claude Code from a scoped shell** (after
`cloudctx use`) or via `cloudctx claude <name> <path>`.

## Agent skill

`skills/cloudctx/` is a [SKILL.md](https://github.com/anthropics/skills) that makes an AI
coding agent run every Azure command as `cloudctx exec <context> -- az ...` instead of bare
`az`. An agent's shell resets between commands, so a `cloudctx use` selection doesn't carry
over to the next one; `exec` scopes each call in a single process. The skill reads the
context list, uses `$CLOUDCTX_CONTEXT` when the session is already scoped, and asks instead
of guessing when the target is ambiguous. It declares no `allowed-tools`, so `cloudctx`
commands still go through the agent's own permission prompts.

Install it with [`skills`](https://github.com/vercel-labs/skills):

```sh
npx skills add eliknut/cloudctx -g
```

## Companions

Other tools can drive cloudctx to get per-customer isolation without
reimplementing it: run through `cloudctx exec <name> -- ...`, enumerate with
`cloudctx list --names`, read a context's fields with `cloudctx show <name>`,
and keep any per-context state under `$CLOUDCTX_STORE/<tool>/` so
`cloudctx delete` sweeps it with the rest of the store. The surfaces a
companion may rely on are listed in [docs/companions.md](docs/companions.md)
and pinned by `tests/test_companion_contract.py`. The first companion is
[pimctl](https://github.com/larsakerlund/pimctl), which batch-activates Azure
PIM roles per context and requires cloudctx 1.4.0 or newer.

## Migration

You have an existing `~/.azure` login. Two options:

1. **Preserve it** — move it into a context store:
   ```sh
   cloudctx new mycorp --azure-tenant <t> --azure-subscription <s> --no-login
   rsync -a ~/.azure/ ~/.cloudctx/mycorp/azure/
   ```
   (Copy, verify with `cloudctx exec mycorp -- az account show`, then optionally clear
   the global `~/.azure`.)
2. **Start fresh** — run `cloudctx new` per customer and re-login.

cloudctx never auto-migrates and never writes `~/.azure`, `~/.aws`, or the AWS
`default` profile.

## AWS status

AWS contexts are scaffolded and their env vars are exported when a context
defines any `aws_*` field, so an AWS context works the moment you install the AWS
CLI and populate its store. What's **not** automated yet is the login itself
(`aws sso login` / static keys / assume-role); `cloudctx login` prints guidance for an
AWS-bearing context. The AWS SSO token cache (`~/.aws/sso/cache`) is intentionally
left shared — it is keyed per SSO start URL, so customers never collide, and AWS
provides no supported env var to relocate it.

## Edge cases

- GUI-launched terminals don't inherit a scoped env — always start from a scoped
  shell or via `cloudctx open`.
- Token-cache file locking is avoided because each context has its own directory.
- The sourced `cloudctx` function shadows the binary on purpose; it reaches the
  real executable via `command cloudctx`, so nothing loops.

## Development

```sh
python3 -m unittest discover -s tests -v   # unit + shell shim tests (stdlib only)
sh tests/isolation_proof.sh                # the two-context isolation acceptance test
```

Design and plan: `docs/superpowers/specs/` and `docs/superpowers/plans/`.
