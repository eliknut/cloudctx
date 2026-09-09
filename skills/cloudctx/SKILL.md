---
name: cloudctx
description: Routes all Azure CLI work through the cloudctx tool so each customer's credentials stay isolated per context. Use whenever about to run an `az` command or act on Azure resources via CLI — az login, account/subscription selection, resource groups, VMs, AKS, app services, storage, deployments, ARM/Bicep, key vault, networking, or any `az ...` invocation. Every cloud command must run as `cloudctx exec <context> -- az ...`; never bare `az`, never `cloudctx use`/export then a separate `az` call. Also covers the `aws` CLI on cloudctx contexts. Do NOT use for purely conceptual or architecture Azure questions that need no CLI (e.g. "what is AKS", "App Service vs Functions", "explain managed identities").
---

# cloudctx — isolated Azure CLI per customer context

You are stateless: each shell command runs in a fresh process, so env vars set by one
command do NOT survive to the next. Azure CLI keeps its token cache AND active
subscription in a single `AZURE_CONFIG_DIR` (default `~/.azure`) shared by every shell,
so bare `az` pollutes one global store and can silently point at the WRONG customer's
subscription. The shell guard that warns on bare `az` does NOT fire for you (it needs a
sourced interactive shell), so routing every command through `cloudctx exec` is your
only protection.

A **context = one customer tenant** with its own isolated `AZURE_CONFIG_DIR`. A tenant
usually holds many subscriptions; you pick the specific one per task (see "Selecting the
subscription").

## The one rule

`cloudctx exec` is the only correct mechanism: it injects the context's isolated env and
runs the command in ONE process, so nothing needs to persist. Run EVERY cloud command as:

```sh
cloudctx exec <context> -- az <args...>
```

```sh
cloudctx exec acme -- az account show -o json
cloudctx exec acme -- az group list -o table
cloudctx exec acme -- az aks get-credentials -g rg-prod -n aks-prod
cloudctx exec acme -- aws s3 ls                      # AWS too, if the context defines aws fields
```

`--` is the separator — always include it. Any command works after it, not just `az`
(e.g. `cloudctx exec acme -- terraform plan`).

**Logging in is the exception:** use `cloudctx login <context>` (add `--device-code` for
headless), never `az login` through `exec`. `cloudctx login` pins the context's tenant and
verifies the landed identity against it; a raw `az login` skips both and can poison the
store with the wrong tenant's token.

## Contexts currently on this machine

!`cloudctx list`

If it reports no contexts, none exist yet — see "When no context exists".

## Choosing the context

1. If `CLOUDCTX_CONTEXT` is already set, the session is scoped to that context — use it,
   and do not switch to a different one without asking.
2. Otherwise read the list above (re-run `cloudctx list` only if it isn't shown) and pick
   the context matching the customer/tenant you were asked to act on.
3. If the match is ambiguous, ask — do not guess.
4. Before anything destructive, confirm the landed identity and that its tenant matches the
   context: `cloudctx exec <context> -- cloudctx status`, checked against the `azure_tenant`
   in `cloudctx show <context>`.

## When no context exists

If no context matches, STOP — do NOT fall back to bare `az`. Ask the user, then offer to
create one:

```sh
cloudctx new <name> --display "Human Label" --azure-tenant <tenant-guid>
```

- Tenant only — do NOT pass `--azure-subscription`; the sub is chosen per task, and
  pinning one goes stale (sub ids churn). Add it only if the user wants a fixed default.
- Name must match `[A-Za-z0-9_-]+`. The tenant GUID comes from the user.
- `new` runs `az login` interactively (browser); add `--device-code` for headless.
- `--no-login` scaffolds without auth; log in later with `cloudctx login <name> --device-code`.
- `cloudctx login <name> --dry-run` previews the auth commands without running them.

## Selecting the subscription within a context

A context does NOT pin a subscription — the active one is just whatever it last selected,
possibly wrong for the task. Before subscription-specific work:

```sh
cloudctx exec <ctx> -- az account show -o table                      # active sub
cloudctx exec <ctx> -- az account list -o table                      # all subs in this context
cloudctx exec <ctx> -- az account set --subscription "<name-or-id>"  # switch; persists in the store, not via env
```

Or scope one command without changing the active sub:

```sh
cloudctx exec <ctx> -- az group list --subscription "<name-or-id>" -o table
```

If the user names an environment (prod/test/qa) or subscription and the match is
ambiguous, confirm WHICH before acting — never guess on a destructive op.

## PIM role activation

If [`pimctl`](https://github.com/larsakerlund/pimctl) is installed
(`command -v pimctl`), use it to activate Entra PIM
eligibility for Azure resource roles instead of hand-rolled `az rest` calls
against `roleAssignmentScheduleRequests`. It runs every Azure call through
`cloudctx exec` itself, so name the context explicitly:

```sh
pimctl -c <ctx> ls                                                  # eligible roles
pimctl -c <ctx> up --role "<role>" --scope <scope> --for 1h -j "<reason>" -y
pimctl -c <ctx> status                                              # what is held now
pimctl -c <ctx> down --role "<role>" --scope <scope> -y             # give it back
```

`-j` is required when there is no terminal. Exit code 2 means the request is
waiting for an approver, so the role is NOT active yet. Deactivate with `down`
when the task is done.

## Never

- ❌ bare `az ...` — hits the global store (see above).
- ❌ `cloudctx use <ctx>` then a separate `az ...` — the env dies with that shell, so the next
  `az` has no `AZURE_CONFIG_DIR` (and `cloudctx use` needs a sourced shim a fresh shell may lack).
- ❌ `export AZURE_CONFIG_DIR=...` then `az ...` in a later command — same reason.
