# cloudctx bash shim — source this from ~/.bashrc:
#     source /path/to/cloudctx/shell/cloudctx.bash
#
# `cloudctx use`/`cloudctx clear` must mutate THIS shell, so they run here rather than in
# the binary (a child process can't export into its parent). Everything else
# delegates to the `cloudctx` binary (via `command`, which bypasses
# this function).

cloudctx() {
  case "$1" in
    use)
      if [[ -z "$2" ]]; then echo "usage: cloudctx use <name>" >&2; return 1; fi
      local _cloudctx_env
      _cloudctx_env="$(command cloudctx _env "$2")" || return 1
      eval "$_cloudctx_env"
      command cloudctx _decorate "$2"
      ;;
    clear)
      eval "$(command cloudctx _env --clear)"
      command cloudctx _decorate --clear
      ;;
    *)
      command cloudctx "$@"
      ;;
  esac
}

# --- prompt segment + az/aws guard ----------------------------------------
# Guard takes a full command line, skips leading VAR=value assignments and
# common wrappers (sudo/command/env/...), then warns if the real command is
# az/aws with no context selected. Callable directly; also driven by the trap.
_cloudctx_guard() {
  # Check every list/pipeline segment, not just the first: `cd x && az login`
  # and `echo y | az ...` must still warn. Quoted spans are stripped before
  # splitting so a separator inside a string argument is data, not a command
  # boundary. (A lone escaped quote can still confuse the stripper — an
  # acceptable edge for an advisory guard. bash 3.2 compatible.)
  local raw="$1" line="" ch q="" seg nl=$'\n'
  local -i n=${#raw} j
  for ((j = 0; j < n; j++)); do
    ch=${raw:j:1}
    if [ -n "$q" ]; then
      [ "$ch" = "$q" ] && q=""
      continue
    fi
    case $ch in
      \"|\') q=$ch ;;
      *) line+=$ch ;;
    esac
  done
  line=${line//&&/$nl}
  line=${line//||/$nl}
  line=${line//;/$nl}
  line=${line//|/$nl}
  line=${line//&/$nl}
  local -a words
  local i
  while IFS= read -r seg; do
    read -ra words <<< "$seg"
    i=0
    while [ "$i" -lt "${#words[@]}" ]; do
      case "${words[$i]}" in
        # skip assignments, wrappers, and wrapper flags (stdbuf -oL az ...)
        *=*|-*|sudo|command|env|nice|nohup|time|builtin|exec|stdbuf) i=$((i + 1)) ;;
        *) break ;;
      esac
    done
    case "${words[$i]:-}" in
      az|aws)
        if [ -z "$CLOUDCTX_CONTEXT" ]; then
          echo "cloudctx: WARNING — '${words[$i]}' run with no context selected (using global default store). Run 'cloudctx use <name>' first." >&2
        fi
        ;;
    esac
  done <<< "$line"
}

cloudctx_debug() { _cloudctx_guard "$BASH_COMMAND"; }

# Bash has no native preexec. If rcaloras/bash-preexec is loaded, register as a
# preexec function so we compose with every other hook (starship/atuin/direnv,
# which use bash-preexec) — this is the supported way to coexist.
#
# Otherwise install our own DEBUG trap. We deliberately do NOT try to "chain"
# onto a pre-existing DEBUG trap: `trap -p DEBUG` returns nothing from inside a
# sourced file's scope, so the previous handler is invisible here and any chain
# would be a no-op that still clobbers it. If you use other DEBUG-trap tools,
# load bash-preexec (then we take the preexec path above and nothing is lost).
if declare -p preexec_functions >/dev/null 2>&1; then
  _cloudctx_bp_preexec() { _cloudctx_guard "$1"; }
  preexec_functions+=(_cloudctx_bp_preexec)
else
  trap 'cloudctx_debug' DEBUG
fi

# --- tab completion --------------------------------------------------------
_cloudctx_complete() {
  local cur=${COMP_WORDS[COMP_CWORD]}
  if [ "$COMP_CWORD" -eq 1 ]; then
    COMPREPLY=($(compgen -W "use clear new list show delete status login exec open claude bind unbind gen-profiles install self-update" -- "$cur"))
  elif [ "$COMP_CWORD" -eq 2 ]; then
    case "${COMP_WORDS[1]}" in
      use|login|exec|open|claude|delete|show|bind)
        COMPREPLY=($(compgen -W "$(command cloudctx _names 2>/dev/null)" -- "$cur"))
        ;;
    esac
  fi
}
complete -F _cloudctx_complete cloudctx

# Show the active context (+ short Azure subscription label + AWS_PROFILE) in
# the prompt. AWS_PROFILE is the only cloud indicator for an AWS-only context.
PS1='${CLOUDCTX_CONTEXT:+\[\e[36m\][${CLOUDCTX_CONTEXT}${CLOUDCTX_AZURE_LABEL:+:${CLOUDCTX_AZURE_LABEL}}${AWS_PROFILE:+ aws:${AWS_PROFILE}}]\[\e[0m\] }'"$PS1"
