#!/bin/sh
# The headline acceptance test: two contexts must isolate AZURE_CONFIG_DIR so
# that two shells never share Azure login / active-subscription state.
#
# Hermetic — uses a throwaway $CLOUDCTX_HOME, no real Azure/iTerm2 needed.
set -eu

HOME_DIR=$(mktemp -d)
export CLOUDCTX_HOME="$HOME_DIR"
export CLOUDCTX_PROFILES="$HOME_DIR/profiles.json"
CLI="$(CDPATH= cd "$(dirname "$0")/.." && pwd)/cloudctx"

"$CLI" new acme   --display "Acme"   --color "#c0392b" \
  --azure-tenant t-acme   --azure-subscription sub-acme   --no-login --no-profiles >/dev/null
"$CLI" new globex --display "Globex" --color "#2980b9" \
  --azure-tenant t-globex --azure-subscription sub-globex --no-login --no-profiles >/dev/null

# Two independent subshells select different contexts.
A=$(sh -c 'eval "$('"$CLI"' _env acme)";   printf %s "$AZURE_CONFIG_DIR"')
B=$(sh -c 'eval "$('"$CLI"' _env globex)"; printf %s "$AZURE_CONFIG_DIR"')

echo "acme   -> AZURE_CONFIG_DIR=$A"
echo "globex -> AZURE_CONFIG_DIR=$B"

fail=0
[ "$A" != "$B" ] || { echo "FAIL: AZURE_CONFIG_DIR identical across contexts"; fail=1; }
case "$A" in *"/acme/azure")   : ;; *) echo "FAIL: acme dir unexpected";   fail=1 ;; esac
case "$B" in *"/globex/azure") : ;; *) echo "FAIL: globex dir unexpected"; fail=1 ;; esac

# Two worktrees of ONE repo bound to two contexts: a shell started in each
# (the shim runs `_env --binding <path>`) gets its own store and subscription.
if command -v git >/dev/null 2>&1; then
  PIN_A=11111111-1111-1111-1111-111111111111
  PIN_B=22222222-2222-2222-2222-222222222222
  REPO="$HOME_DIR/repo"
  WT="$HOME_DIR/repo-wt"
  G="git -c user.name=t -c user.email=t@example.invalid -c commit.gpgsign=false"
  $G init -q "$REPO"
  $G -C "$REPO" commit --allow-empty -q -m init
  $G -C "$REPO" worktree add -q "$WT"
  (cd "$REPO" && "$CLI" bind acme   --subscription "$PIN_A" >/dev/null)
  (cd "$WT"   && "$CLI" bind globex --subscription "$PIN_B" >/dev/null)

  WA=$(cd "$REPO" && sh -c 'eval "$("$1" _env --binding "$2")"; printf "%s|%s" "$AZURE_CONFIG_DIR" "$ARM_SUBSCRIPTION_ID"' sh "$CLI" "$REPO/.cloudctx")
  WB=$(cd "$WT"   && sh -c 'eval "$("$1" _env --binding "$2")"; printf "%s|%s" "$AZURE_CONFIG_DIR" "$ARM_SUBSCRIPTION_ID"' sh "$CLI" "$WT/.cloudctx")
  echo "worktree acme   -> $WA"
  echo "worktree globex -> $WB"

  [ "${WA%%|*}" != "${WB%%|*}" ] || { echo "FAIL: worktrees share AZURE_CONFIG_DIR"; fail=1; }
  [ "${WA#*|}" = "$PIN_A" ] || { echo "FAIL: acme worktree subscription ${WA#*|}"; fail=1; }
  [ "${WB#*|}" = "$PIN_B" ] || { echo "FAIL: globex worktree subscription ${WB#*|}"; fail=1; }
  case "${WA%%|*}" in *"/acme/azure")   : ;; *) echo "FAIL: acme worktree dir";   fail=1 ;; esac
  case "${WB%%|*}" in *"/globex/azure") : ;; *) echo "FAIL: globex worktree dir"; fail=1 ;; esac
  [ -z "$($G -C "$WT" status --porcelain)" ] || { echo "FAIL: .cloudctx shows in git status"; fail=1; }
  if [ "$fail" = 0 ]; then
    echo "PASS: two bound worktrees isolate AZURE_CONFIG_DIR and ARM_SUBSCRIPTION_ID"
  fi
else
  echo "SKIP: git not installed, bound-worktree check skipped"
fi

rm -rf "$HOME_DIR"
if [ "$fail" = 0 ]; then
  echo "PASS: two contexts isolate AZURE_CONFIG_DIR (no cross-shell bleed)"
  exit 0
fi
exit 1
