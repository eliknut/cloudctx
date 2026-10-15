# cloudctx zsh shim — source this from ~/.zshrc:
#     source /path/to/cloudctx/shell/cloudctx.zsh
#
# `cloudctx use`/`cloudctx clear` must mutate THIS shell, so they run here rather than in
# the binary (a child process can't export into its parent). Everything else
# delegates to the `cloudctx` binary (via `command`, which bypasses
# this function).

cloudctx() {
  emulate -L zsh
  case "$1" in
    use)
      if [[ -z "$2" ]]; then print -u2 "usage: cloudctx use <name>"; return 1; fi
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
# az/aws with no context selected. Callable directly; also driven by preexec.
_cloudctx_guard() {
  emulate -L zsh
  # ${(z)} lexes like the shell itself: separators become standalone tokens
  # even unspaced (`cd x&&az` -> cd, x, &&, az) and quoted strings stay one
  # word — so `cd x && az login` warns while `git commit -m "a && az b"`
  # stays silent. Walk the tokens: after every separator we expect a new
  # command; skip assignments/wrappers/flags, then check for az/aws.
  local -a words
  words=(${(z)1})
  local w
  local -i expect=1
  for w in $words; do
    case $w in
      '&&'|'||'|';'|'|'|'|&'|'&') expect=1; continue ;;
    esac
    (( expect )) || continue
    case $w in
      # skip assignments, wrappers, and wrapper flags (stdbuf -oL az ...)
      *=*|-*|sudo|command|env|nice|nohup|time|builtin|exec|stdbuf) ;;
      az|aws)
        if [[ -z "$CLOUDCTX_CONTEXT" ]]; then
          print -u2 "cloudctx: WARNING — '$w' run with no context selected (using global default store). Run 'cloudctx use <name>' first."
        fi
        expect=0
        ;;
      *) expect=0 ;;
    esac
  done
}

# preexec receives the typed command line as $1.
cloudctx_preexec() { _cloudctx_guard "$1"; }
autoload -Uz add-zsh-hook
add-zsh-hook preexec cloudctx_preexec

# --- tab completion --------------------------------------------------------
# Registered only if compdef exists, i.e. compinit ran before this file was
# sourced. If you source the shim earlier, run compinit first or re-source.
_cloudctx() {
  local -a subcmds names
  subcmds=(use clear new list show delete status login exec open claude
           gen-profiles install self-update)
  if (( CURRENT == 2 )); then
    compadd -- $subcmds
  elif (( CURRENT == 3 )); then
    case "${words[2]}" in
      use|login|exec|open|claude|delete|show)
        names=(${(f)"$(command cloudctx _names 2>/dev/null)"})
        compadd -- $names
        ;;
    esac
  fi
}
if (( ${+functions[compdef]} )); then
  compdef _cloudctx cloudctx
fi

# Show the active context (+ short Azure subscription label + AWS_PROFILE) in
# the prompt. AWS_PROFILE is the only cloud indicator for an AWS-only context.
# Single quotes + PROMPT_SUBST => re-evaluated on every redraw, stable per shell.
# `%` in the (free-form) values is doubled so it is rendered literally and can
# never act as a prompt escape.
setopt PROMPT_SUBST
# %6F is cyan in numeric form: a braced %F{cyan} would close the enclosing
# ${CLOUDCTX_CONTEXT:+...} at its first `}` and break the whole segment.
PROMPT='${CLOUDCTX_CONTEXT:+%6F[${CLOUDCTX_CONTEXT//\%/%%}${CLOUDCTX_AZURE_LABEL:+:${CLOUDCTX_AZURE_LABEL//\%/%%}}${AWS_PROFILE:+ aws:${AWS_PROFILE//\%/%%}}]%f }'"$PROMPT"
