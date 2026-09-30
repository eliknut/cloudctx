# cloudctx exports ARM_TENANT_ID and ARM_SUBSCRIPTION_ID

**Status:** approved design (Elias, 2026-09-30)
**Date:** 2026-09-30

## 1. Objective

cloudctx isolates the Azure CLI per context through `AZURE_CONFIG_DIR`, but it
exports nothing Terraform reads directly. The azurerm provider then falls back
to whatever the Azure CLI store says, and since azurerm 4.0 it refuses to run
without a subscription ID at all:

> In version 4.0 of the Azure Provider, it's now required to specify the Azure
> Subscription ID when configuring a provider instance in your configuration.
> This can be done by specifying the `subscription_id` provider property, or by
> exporting the `ARM_SUBSCRIPTION_ID` environment variable.
> (azurerm 4.0 upgrade guide, "Specifying Subscription ID is now Mandatory")

Exporting `ARM_TENANT_ID` and `ARM_SUBSCRIPTION_ID` from the context's pinned
values makes `cloudctx exec <ctx> -- terraform plan` target the context's
tenant and subscription even if someone ran `az account set` in that store,
and satisfies azurerm 4.x without editing every provider block.

Prompted by a colleague's per-identity `AZURE_CONFIG_DIR` convention, which
sets both variables alongside the config dir for Terraform.

## 2. Behavior

`env_dict(name)` adds, only when the registry value is GUID-shaped
(`GUID_RE`, the same check `_verify_azure_login` uses):

| Variable | Source | Set when |
|---|---|---|
| `ARM_TENANT_ID` | `azure_tenant` | `azure_tenant` is a GUID |
| `ARM_SUBSCRIPTION_ID` | `azure_subscription` | `azure_subscription` is a GUID |

- A domain-form tenant (`contoso.onmicrosoft.com`) or a subscription given by
  name exports nothing. Terraform rejects non-GUID values, and resolving them
  would need a network call on every `exec`.
- Both join `CLEARABLE_VARS`, so `cloudctx clear` unsets them and
  `cloudctx exec` strips inherited values before overlaying the target
  context's. This matters: an `ARM_SUBSCRIPTION_ID` left over from another
  customer's window must never leak into this one.
- `cloudctx use` exports them through `env_lines`, so the shell shim needs no
  change.
- `cloudctx show` is unchanged: it prints registry fields and store paths, not env vars, and its contract format stays as is.
- Explicit provider arguments still win over the environment, so a Terraform
  root that sets `subscription_id` / `tenant_id` (or a root `env.sh` that
  exports its own `ARM_*` after `exec`) keeps its current behavior.

## 3. Registry state today (measured 2026-09-30)

- 24 contexts; 22 pin `azure_tenant`, 12 of those as a GUID.
- 1 context pins `azure_subscription` (a GUID).

So after this change 12 contexts get `ARM_TENANT_ID` and 1 gets
`ARM_SUBSCRIPTION_ID`. The workspace convention is "pin the tenant only, pick
subscriptions per command", so most Terraform roots will still need their own
`subscription_id` or `ARM_SUBSCRIPTION_ID`. This change does not try to fix
that.

## 4. Contract impact

`ARM_TENANT_ID` and `ARM_SUBSCRIPTION_ID` become managed variables, which is a
companion-contract surface (`docs/companions.md`,
`tests/test_companion_contract.py`). Additive only; nothing existing changes.

- Add both rows to the exported-variables table in `docs/companions.md`.
- Append both to `MANAGED_VARS` in `tests/test_companion_contract.py`.
- Bump to 1.5.0 (additive surface), tag, redeploy the landing page if it lists
  the variables.

## 5. Tests

- GUID tenant exports `ARM_TENANT_ID`; domain tenant does not.
- GUID subscription exports `ARM_SUBSCRIPTION_ID`; name-form does not.
- `exec` strips an inherited `ARM_SUBSCRIPTION_ID` when the target context
  has none (the cross-customer leak case).
- `clear` output unsets both.
- Existing contract test updated for the new `CLEARABLE_VARS` list.

## 6. Non-goals

- No `ARM_CLIENT_ID` / secrets / OIDC. Auth stays Azure CLI.
- No resolving domain tenants or subscription names at `exec` time.
- No change to `az` behavior; `AZURE_CONFIG_DIR` stays the isolation boundary.

## 7. Open question

The 10 contexts with a domain-form tenant get no `ARM_TENANT_ID`.
Recommendation: re-pin them as GUIDs in `~/.cloudctx/contexts.toml` (the
tenant ID is printed by `cloudctx login` as `tenant=<guid>`). Later option,
not in this change: have `login` cache the resolved tenant ID in
`$CLOUDCTX_STORE` and export from the cache.
