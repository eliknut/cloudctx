# cloudctx worktree binding (Orca support) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `.cloudctx` file at a worktree root binds that worktree to one cloudctx context (optionally pinned to one subscription), so every shell and agent started there runs against the right tenant without `cloudctx use`.
**Architecture:** The binary gains a binding core (`find_binding`, `read_binding`, `load_binding_file`, `load_binding_for`), a pin in `env_dict(name, binding=None)`, `_env --binding <path>`, binding-aware `exec`, and `bind`/`unbind` commands. The zsh and bash shims walk up from `$PWD` in pure shell once at source time and only then call the binary. Two unrelated fixes ride along: the zsh prompt segment and iTerm-only OSC 1337.
**Tech Stack:** Python 3.9+ stdlib only (single file `cloudctx`), zsh and bash shims, `unittest`, POSIX `sh` acceptance script, git CLI.
**Spec:** docs/superpowers/specs/2026-10-15-orca-worktree-binding-design.md

## Global Constraints

- Stdlib only: no new import outside the standard library (`typing` is stdlib and is added for `Optional`/`Tuple`).
- Python 3.9 floor: no `X | None`, no `match`, no `tomllib`-only code paths (local dev box runs 3.9.6, so the `_parse_toml` fallback is the path the tests exercise there).
- No new exported variable: `CLEARABLE_VARS` stays at exactly 9 entries.
- Companion contract unchanged: `exec`, `list --names`, `show` format, `--version`, exported vars, the "unknown context" phrase; `tests/test_companion_contract.py` passes without edits.
- Binding file name is exactly `.cloudctx`; content is exactly `context = "<name>"` plus an optional `subscription = "<guid>"` line.
- Subscription pin is GUID-only (`GUID_RE`), and only allowed for a context that has `azure_tenant`.
- The pin overrides exactly two variables: `ARM_SUBSCRIPTION_ID` and `CLOUDCTX_AZURE_LABEL`.
- Different-context exec note is exactly `cloudctx: note: this folder is bound to <Y>` on stderr.
- Shims activate a binding only at source time: nothing on `chpwd`, `precmd` or `PROMPT_COMMAND`.
- A shell started outside any binding runs no `cloudctx` subprocess at start-up.
- `__version__ = "1.6.0"`.
- No change to `docs/companions.md` or `tests/test_companion_contract.py`.
- Tests run with `python3 -m unittest` (no pytest); git-dependent tests skip cleanly when `git` is missing.

## Review Focus

1. A **directory** named `.cloudctx` on the walk-up path (the default registry root `~/.cloudctx` is one, so every folder under `$HOME` meets it): `find_binding` and both shim walks must skip it and keep walking; an unreadable `.cloudctx` file is ignored with one stderr warning. Tests: T3 `test_directory_named_cloudctx_is_skipped`, `test_unreadable_file_warns_and_is_ignored`; T7 `test_registry_dir_named_cloudctx_is_not_a_binding`.
2. A worktree path containing a **space or a single quote**: the shim's `eval "$(command cloudctx _env --binding "$path")"`, the interactive stderr line and `bind` must all handle it. Tests: T6 `test_bind_in_path_with_space_and_quote`; T7 `test_binding_in_path_with_space_and_quote`.
3. A **linked git worktree** (not the main checkout), where `--git-common-dir` differs from `--git-dir`: the file lands at the worktree toplevel and the exclude line in the shared common dir, so `git status` is clean in both. Test: T6 `test_bind_in_linked_worktree_uses_common_exclude`.
4. **No git**: `git` not on `PATH`, `bind` outside any repo, `bind` in `$HOME` or `/`: write in cwd without touching any exclude file, refuse in `$HOME` and `/`. Tests: T6 `test_bind_without_git_installed_writes_cwd`, `test_bind_outside_git_writes_cwd`, `test_bind_refuses_home`, `test_bind_refuses_root`.
5. **Inherited context differs from the binding** (parent shell had `cloudctx use other`, stale `ARM_SUBSCRIPTION_ID`), plus a binding file found in a plain folder **above** the git toplevel: the binding wins at shell start and the stale pin is replaced. Tests: T7 `test_binding_overrides_inherited_context`; T3 `test_binding_above_git_toplevel_is_found`.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `cloudctx` | Modify | Binding core, pin in `env_dict`/`env_lines`, `_env --binding`, binding-aware `cmd_exec`, `cmd_bind`/`cmd_unbind`, iTerm gate in `_decorate`, version 1.6.0 |
| `shell/cloudctx.zsh` | Modify | Prompt segment fix, start-up binding activation, completion for `bind`/`unbind` |
| `shell/cloudctx.bash` | Modify | Start-up binding activation, completion for `bind`/`unbind` |
| `tests/test_cloudctx.py` | Modify | Tests for binding core, env pin, exec rules, bind/unbind, decorate gate |
| `tests/test_shell.py` | Modify | Rendered-prompt tests, start-up activation tests in real `zsh -f` / `bash --norc` |
| `tests/isolation_proof.sh` | Modify | Two worktrees of one temp repo bound to two contexts stay isolated |
| `README.md` | Modify | `bind`/`unbind` usage lines, "Using cloudctx in Orca" section |
| `skills/cloudctx/SKILL.md` | Modify | "Bound worktrees" section |

---

### Task 1: zsh prompt segment renders correctly

The `}` of `%F{cyan}` closes `${CLOUDCTX_CONTEXT:+...}` early. Escaping the braces (`%F\{cyan\}`) leaks the backslashes into the rendered prompt (checked under zsh 5.9). The numeric form `%6F` (6 = cyan) has no braces and renders the same colour.

**Files:**
- Modify: `shell/cloudctx.zsh:96` (the `PROMPT=` line)
- Test: `tests/test_shell.py` (class `TestShimZsh`, after `test_zsh_prompt_escapes_percent` at ~193)

**Interfaces:**
- Consumes: `extract(output)`, `ROOT`, `MARK` from `tests/test_shell.py`.
- Produces: `PROMPT` that renders to the original prompt with no context and to `[name:label aws:profile] ` (cyan) with one.

- [ ] **Step 1: Write the failing tests**

Add at the top of `tests/test_shell.py`, after the imports:

```python
import re

ANSI = re.compile(r"\x1b\[[0-9;]*m")
```

Add to `TestShimZsh` (the bash subclass skips them):

```python
    def _render_zsh_prompt(self, assignments):
        if self.shell != "zsh":
            self.skipTest("zsh prompt rendering")
        script = (
            "PROMPT='> '\n"
            f"source {ROOT}/shell/cloudctx.zsh\n"
            "unset CLOUDCTX_CONTEXT CLOUDCTX_AZURE_LABEL AWS_PROFILE\n"
            f"{assignments}\n"
            'print -rP -- "CCTX=$PROMPT"\n'
        )
        r = subprocess.run(["zsh", "-f", "-c", script], cwd=self.home,
                           env=self.env, capture_output=True, text=True)
        return r, extract(r.stdout)

    def test_zsh_prompt_renders_nothing_without_context(self):
        r, val = self._render_zsh_prompt(":")
        self.assertEqual(val, "> ", msg=r.stderr)

    def test_zsh_prompt_renders_context_segment(self):
        r, val = self._render_zsh_prompt(
            "CLOUDCTX_CONTEXT=demo CLOUDCTX_AZURE_LABEL=sub AWS_PROFILE=p")
        self.assertIsNotNone(val, msg=r.stderr)
        self.assertEqual(ANSI.sub("", val), "[demo:sub aws:p] > ")
        self.assertIn("\x1b[36m", val)  # cyan

    def test_zsh_prompt_renders_name_only(self):
        r, val = self._render_zsh_prompt("CLOUDCTX_CONTEXT=demo")
        self.assertEqual(ANSI.sub("", val or ""), "[demo] > ", msg=r.stderr)

    def test_zsh_prompt_renders_percent_literally(self):
        r, val = self._render_zsh_prompt(
            "CLOUDCTX_CONTEXT=demo CLOUDCTX_AZURE_LABEL='100%n'")
        self.assertEqual(ANSI.sub("", val or ""), "[demo:100%n] > ", msg=r.stderr)
```

- [ ] **Step 2: Run, expect FAIL**

Run: `python3 -m unittest tests.test_shell.TestShimZsh -v 2>&1 | tail -30`
Expected: the four new tests FAIL (rendered value is `[] }> ` plus a colour reset with no context, and the name is missing with a context); every other test passes.

- [ ] **Step 3: Fix the segment**

Replace the last line of `shell/cloudctx.zsh` with:

```zsh
# %6F is cyan in numeric form: a braced %F{cyan} would close the enclosing
# ${CLOUDCTX_CONTEXT:+...} at its first `}` and break the whole segment.
PROMPT='${CLOUDCTX_CONTEXT:+%6F[${CLOUDCTX_CONTEXT//\%/%%}${CLOUDCTX_AZURE_LABEL:+:${CLOUDCTX_AZURE_LABEL//\%/%%}}${AWS_PROFILE:+ aws:${AWS_PROFILE//\%/%%}}]%f }'"$PROMPT"
```

- [ ] **Step 4: Run, expect PASS**

Run: `python3 -m unittest tests.test_shell -v 2>&1 | tail -5`
Expected: `OK` (including `test_zsh_prompt_escapes_percent`, `test_prompt_has_context_segment`, `test_prompt_includes_aws_profile`; bash `PS1` untouched).

- [ ] **Step 5: Commit**

```bash
git add shell/cloudctx.zsh tests/test_shell.py
git commit -m "Fix zsh prompt segment closing early on %F{cyan}"
```

---

### Task 2: `_decorate` sends OSC 1337 only under iTerm2

**Files:**
- Modify: `cloudctx:478-505` (`decorate_bytes`, `decorate_clear`)
- Test: `tests/test_cloudctx.py` (`TestDecorate` at ~293, new class `TestDecorateTerminal` after it)

**Interfaces:**
- Consumes: `_badge_seq`, `_title_seq`, `_tabcolor_seq`, `_hex6`, `load_registry`.
- Produces: `_is_iterm() -> bool`; `decorate_bytes(name: str) -> str` (title always, badge and tab colour only under iTerm); `decorate_clear() -> str` (empty string outside iTerm).

- [ ] **Step 1: Write the failing tests and pin iTerm in the existing ones**

In `TestDecorate.setUp` (~294), after `super().setUp()`, add:

```python
        os.environ["TERM_PROGRAM"] = "iTerm.app"   # OSC 1337 is iTerm-only
        os.environ.pop("LC_TERMINAL", None)
```

Add after `TestDecorate`:

```python
class TestDecorateTerminal(Base):
    def setUp(self):
        super().setUp()
        os.environ.pop("TERM_PROGRAM", None)
        os.environ.pop("LC_TERMINAL", None)
        self.run_cli("new", "acme", "--display", "Acme AB", "--color", "#c0392b",
                     "--azure-tenant", "t", "--no-login")

    def test_orca_gets_title_only(self):
        os.environ["TERM_PROGRAM"] = "Orca"
        _, out = self.run_cli("_decorate", "acme")
        self.assertIn("\x1b]0;Acme AB\x07", out)
        self.assertNotIn("1337", out)

    def test_no_terminal_hint_gets_title_only(self):
        _, out = self.run_cli("_decorate", "acme")
        self.assertIn("\x1b]0;Acme AB\x07", out)
        self.assertNotIn("1337", out)

    def test_lc_terminal_iterm2_gets_badge_and_tab(self):
        os.environ["TERM_PROGRAM"] = "tmux"
        os.environ["LC_TERMINAL"] = "iTerm2"
        _, out = self.run_cli("_decorate", "acme")
        self.assertIn("SetBadgeFormat=", out)
        self.assertIn("SetColors=tab=c0392b", out)
        self.assertIn("\x1b]0;Acme AB\x07", out)

    def test_clear_outside_iterm_sends_no_1337(self):
        os.environ["TERM_PROGRAM"] = "Orca"
        code, out = self.run_cli("_decorate", "--clear")
        self.assertEqual(code, 0)
        self.assertNotIn("1337", out)

    def test_clear_under_iterm_resets(self):
        os.environ["TERM_PROGRAM"] = "iTerm.app"
        _, out = self.run_cli("_decorate", "--clear")
        self.assertIn("SetColors=tab=default", out)
```

- [ ] **Step 2: Run, expect FAIL**

Run: `python3 -m unittest tests.test_cloudctx.TestDecorateTerminal tests.test_cloudctx.TestDecorate -v`
Expected: `test_orca_gets_title_only`, `test_no_terminal_hint_gets_title_only`, `test_clear_outside_iterm_sends_no_1337` FAIL (`1337` found in output); the rest pass.

- [ ] **Step 3: Implement the gate**

Replace `decorate_bytes` and `decorate_clear` in `cloudctx` with:

```python
def _is_iterm() -> bool:
    """OSC 1337 (badge, tab colour) is iTerm2-only; other terminals (Orca,
    VS Code, Terminal.app) may print it as garbage, so gate it."""
    return (os.environ.get("TERM_PROGRAM") == "iTerm.app"
            or os.environ.get("LC_TERMINAL") == "iTerm2")


def decorate_bytes(name: str) -> str:
    entry = load_registry()[name]
    display = entry.get("display") or name
    out = _title_seq(display)  # OSC 0: every terminal
    if not _is_iterm():
        return out
    out = _badge_seq(display) + out
    color = entry.get("color")
    if color:
        try:
            out += _tabcolor_seq(_hex6(color))
        except ValueError:
            pass  # malformed color: skip the tab-color sequence, don't crash
    return out


def decorate_clear() -> str:
    if not _is_iterm():
        return ""
    return (f"{ESC}]1337;SetBadgeFormat={BEL}"          # empty badge payload
            f"{ESC}]1337;SetColors=tab=default{BEL}")    # documented reset
```

- [ ] **Step 4: Run, expect PASS**

Run: `python3 -m unittest tests.test_cloudctx -v 2>&1 | tail -5`
Expected: `OK`. If `TestReviewFixes.test_decorate_sanitizes_control_chars_in_title` or another test outside `TestDecorate` asserts a 1337 sequence, set `os.environ["TERM_PROGRAM"] = "iTerm.app"` at the start of that test.

- [ ] **Step 5: Commit**

```bash
git add cloudctx tests/test_cloudctx.py
git commit -m "Send iTerm2 badge and tab colour only under iTerm2"
```

---

### Task 3: binding file core

**Files:**
- Modify: `cloudctx:21` (imports), and a new section inserted after `save_registry` (ends ~358) and before the "Env emission" banner (~360)
- Test: `tests/test_cloudctx.py` (new class `TestBindingFile` after `TestArmEnv`)

**Interfaces:**
- Consumes: `_tomllib`, `_parse_toml`, `NAME_RE`, `GUID_RE` (defined at ~532, used at call time), `load_registry`.
- Produces:
  - `BINDING_FILE = ".cloudctx"`
  - `class BindingError(Exception)`
  - `find_binding(start: Path) -> Optional[Path]`
  - `read_binding(path: Path) -> dict` returning `{"context": str, "subscription": Optional[str]}`, raises `BindingError`
  - `load_binding_file(path: Path) -> Optional[Tuple[Path, dict]]` (warns on stderr, returns None when unusable or unknown context)
  - `load_binding_for(start: Path) -> Optional[Tuple[Path, dict]]`

Note: `_parse_toml` (`cloudctx:232`) ignores any key that appears before a `[table]` header, so a header-less binding file parses to `{}` on Python 3.9. `read_binding` wraps the text in a synthetic `[binding]` table for the fallback only.

- [ ] **Step 1: Write the failing tests**

```python
class TestBindingFile(Base):
    SUB = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def setUp(self):
        super().setUp()
        self.run_cli("new", "acme", "--azure-tenant", "t", "--no-login")
        self.root = Path(tempfile.mkdtemp(prefix="cctx-bind-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def write(self, folder, text):
        folder.mkdir(parents=True, exist_ok=True)
        p = folder / ".cloudctx"
        p.write_text(text, encoding="utf-8")
        return p

    def test_find_binding_nearest_wins(self):
        outer = self.write(self.root, 'context = "acme"\n')
        inner = self.write(self.root / "a", 'context = "acme"\n')
        start = self.root / "a" / "b" / "c"
        start.mkdir(parents=True)
        self.assertEqual(self.cc.find_binding(start), inner)
        self.assertEqual(self.cc.find_binding(self.root), outer)

    def test_find_binding_none(self):
        start = self.root / "x"
        start.mkdir()
        self.assertIsNone(self.cc.find_binding(start))

    def test_directory_named_cloudctx_is_skipped(self):
        # ~/.cloudctx (the registry root) is a directory on every machine.
        outer = self.write(self.root, 'context = "acme"\n')
        (self.root / "a" / ".cloudctx").mkdir(parents=True)
        self.assertEqual(self.cc.find_binding(self.root / "a"), outer)

    def test_binding_above_git_toplevel_is_found(self):
        if not shutil.which("git"):
            self.skipTest("git not installed")
        outer = self.write(self.root, 'context = "acme"\n')
        repo = self.root / "plain" / "repo"
        (repo / "sub").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        self.assertEqual(self.cc.find_binding(repo / "sub"), outer)

    def test_read_binding_with_subscription(self):
        p = self.write(self.root, f'context = "acme"\nsubscription = "{self.SUB}"\n')
        self.assertEqual(self.cc.read_binding(p),
                         {"context": "acme", "subscription": self.SUB})

    def test_read_binding_without_subscription(self):
        p = self.write(self.root, 'context = "acme"\n')
        self.assertEqual(self.cc.read_binding(p),
                         {"context": "acme", "subscription": None})

    def test_read_binding_rejects_missing_context(self):
        p = self.write(self.root, f'subscription = "{self.SUB}"\n')
        with self.assertRaises(self.cc.BindingError):
            self.cc.read_binding(p)

    def test_read_binding_rejects_non_guid_subscription(self):
        p = self.write(self.root, 'context = "acme"\nsubscription = "Prod"\n')
        with self.assertRaises(self.cc.BindingError):
            self.cc.read_binding(p)

    def test_read_binding_rejects_garbage(self):
        p = self.write(self.root, "this is [not toml\n")
        with self.assertRaises(self.cc.BindingError):
            self.cc.read_binding(p)

    def test_load_binding_for_ok(self):
        p = self.write(self.root, 'context = "acme"\n')
        self.assertEqual(self.cc.load_binding_for(self.root),
                         (p, {"context": "acme", "subscription": None}))

    def test_load_binding_for_unknown_context_warns(self):
        self.write(self.root, 'context = "nope"\n')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(self.cc.load_binding_for(self.root))
        self.assertIn("ignoring binding", err.getvalue())
        self.assertIn("unknown context", err.getvalue())

    def test_load_binding_for_unparsable_warns(self):
        self.write(self.root, "garbage\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(self.cc.load_binding_for(self.root))
        self.assertIn("ignoring binding", err.getvalue())

    def test_unreadable_file_warns_and_is_ignored(self):
        if os.geteuid() == 0:
            self.skipTest("root can read mode-000 files")
        p = self.write(self.root, 'context = "acme"\n')
        p.chmod(0)
        self.addCleanup(p.chmod, 0o644)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(self.cc.load_binding_for(self.root))
        self.assertIn("ignoring binding", err.getvalue())
```

- [ ] **Step 2: Run, expect FAIL**

Run: `python3 -m unittest tests.test_cloudctx.TestBindingFile -v`
Expected: every test errors with `AttributeError: module 'cloudctx' has no attribute 'find_binding'` (or `read_binding`, `BindingError`, `load_binding_for`).

- [ ] **Step 3: Implement the core**

Add to the imports after `from pathlib import Path`:

```python
from typing import Optional, Tuple
```

Insert this section after `save_registry` and before the "Env emission" banner:

```python
# --------------------------------------------------------------------------
# Worktree binding. A regular file `.cloudctx` binds the folder it sits in
# (and everything below) to one context, optionally pinned to one subscription.
# The nearest file walking up from a directory wins. A directory with that
# name (the default registry root ~/.cloudctx is one) is never a binding.
# --------------------------------------------------------------------------
BINDING_FILE = ".cloudctx"


class BindingError(Exception):
    """A .cloudctx file that cannot be used."""


def find_binding(start: Path) -> Optional[Path]:
    """First regular file named .cloudctx from `start` up to /, or None."""
    here = Path(start).absolute()
    for folder in (here, *here.parents):
        candidate = folder / BINDING_FILE
        if candidate.is_file():
            return candidate
    return None


def read_binding(path: Path) -> dict:
    """Parse a binding file. Returns {"context": str, "subscription": str|None}."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise BindingError(f"cannot read {path}: {e}")
    try:
        if _tomllib is not None:
            data = _tomllib.loads(text)
        else:
            # _parse_toml drops keys outside a [table]; give it one.
            data = _parse_toml("[binding]\n" + text).get("binding", {})
    except Exception as e:
        raise BindingError(f"cannot parse {path}: {e}")
    context = data.get("context")
    if not isinstance(context, str) or not NAME_RE.match(context):
        raise BindingError(f'{path} has no valid context = "<name>" line')
    subscription = data.get("subscription")
    if subscription is not None and (not isinstance(subscription, str)
                                     or not GUID_RE.match(subscription)):
        raise BindingError(f"{path}: subscription must be a GUID")
    return {"context": context, "subscription": subscription}


def load_binding_file(path: Path) -> Optional[Tuple[Path, dict]]:
    """read_binding plus a registry check. Unusable: one stderr line, None."""
    try:
        binding = read_binding(path)
    except BindingError as e:
        print(f"cloudctx: warning: ignoring binding: {e}", file=sys.stderr)
        return None
    if binding["context"] not in load_registry():
        print(f"cloudctx: warning: ignoring binding {path}: unknown context "
              f"{binding['context']!r}", file=sys.stderr)
        return None
    return Path(path), binding


def load_binding_for(start: Path) -> Optional[Tuple[Path, dict]]:
    """The usable binding that applies to `start`, or None."""
    path = find_binding(start)
    if path is None:
        return None
    return load_binding_file(path)
```

- [ ] **Step 4: Run, expect PASS**

Run: `python3 -m unittest tests.test_cloudctx.TestBindingFile -v` then `python3 -m unittest tests.test_cloudctx 2>&1 | tail -3`
Expected: `OK` for both.

- [ ] **Step 5: Commit**

```bash
git add cloudctx tests/test_cloudctx.py
git commit -m "Add .cloudctx binding lookup and parser"
```

---

### Task 4: subscription pin in `env_dict` and `_env --binding`

**Files:**
- Modify: `cloudctx:377-425` (`env_dict`, `env_lines`, `cmd_env`), `cloudctx` parser `p_env` (~1056)
- Test: `tests/test_cloudctx.py` (new class `TestBindingEnv` after `TestBindingFile`)

**Interfaces:**
- Consumes: `load_binding_file(path)`, `read_binding` dict shape from Task 3.
- Produces: `env_dict(name: str, binding: Optional[dict] = None) -> dict`; `env_lines(name: str, binding: Optional[dict] = None) -> list`; `cloudctx _env --binding <path>` (prints replacing env lines for the bound context with the pin; prints nothing, exit 0, one stderr warning when unusable).

- [ ] **Step 1: Write the failing tests**

```python
class TestBindingEnv(Base):
    TENANT = "11111111-2222-3333-4444-555555555555"
    REG_SUB = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    PIN = "99999999-8888-7777-6666-555555555555"

    def setUp(self):
        super().setUp()
        self.run_cli("new", "acme", "--azure-tenant", self.TENANT,
                     "--azure-subscription", self.REG_SUB,
                     "--aws-profile", "acme-ops", "--no-login")
        self.dir = Path(tempfile.mkdtemp(prefix="cctx-env-")).resolve()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def run_split(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.cc.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_pin_overrides_exactly_two_vars(self):
        plain = self.cc.env_dict("acme")
        pinned = self.cc.env_dict("acme", {"context": "acme", "subscription": self.PIN})
        self.assertEqual(pinned["ARM_SUBSCRIPTION_ID"], self.PIN)
        self.assertEqual(pinned["CLOUDCTX_AZURE_LABEL"], self.PIN)
        for key in set(plain) | set(pinned):
            if key in ("ARM_SUBSCRIPTION_ID", "CLOUDCTX_AZURE_LABEL"):
                continue
            self.assertEqual(plain.get(key), pinned.get(key), key)

    def test_binding_without_subscription_changes_nothing(self):
        self.assertEqual(self.cc.env_dict("acme"),
                         self.cc.env_dict("acme", {"context": "acme", "subscription": None}))

    def test_binding_for_other_context_is_ignored(self):
        self.assertEqual(self.cc.env_dict("acme"),
                         self.cc.env_dict("acme", {"context": "globex", "subscription": self.PIN}))

    def test_clearable_vars_still_nine(self):
        self.assertEqual(len(self.cc.CLEARABLE_VARS), 9)

    def test_env_binding_prints_pinned_lines(self):
        p = self.dir / ".cloudctx"
        p.write_text(f'context = "acme"\nsubscription = "{self.PIN}"\n')
        code, out, err = self.run_split("_env", "--binding", str(p))
        self.assertEqual(code, 0, err)
        self.assertIn("export CLOUDCTX_CONTEXT='acme'", out)
        self.assertIn(f"export ARM_SUBSCRIPTION_ID='{self.PIN}'", out)
        self.assertIn(f"export CLOUDCTX_AZURE_LABEL='{self.PIN}'", out)
        self.assertIn(f"export ARM_TENANT_ID='{self.TENANT}'", out)

    def test_env_binding_replaces_previous_context(self):
        self.run_cli("new", "bare", "--azure-tenant", "t", "--no-login")
        p = self.dir / ".cloudctx"
        p.write_text('context = "bare"\n')
        _, out, _ = self.run_split("_env", "--binding", str(p))
        self.assertIn("unset ARM_SUBSCRIPTION_ID", out)
        self.assertIn("unset AWS_PROFILE", out)

    def test_env_binding_invalid_prints_nothing_and_warns(self):
        p = self.dir / ".cloudctx"
        p.write_text('context = "nope"\n')
        code, out, err = self.run_split("_env", "--binding", str(p))
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertIn("ignoring binding", err)

    def test_env_binding_missing_file_prints_nothing(self):
        code, out, err = self.run_split("_env", "--binding", str(self.dir / "absent"))
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertIn("ignoring binding", err)
```

- [ ] **Step 2: Run, expect FAIL**

Run: `python3 -m unittest tests.test_cloudctx.TestBindingEnv -v`
Expected: env_dict tests error with `TypeError: env_dict() takes 1 positional argument but 2 were given`; `_env --binding` tests fail with argparse exit 2 (`unrecognized arguments: --binding`). `test_clearable_vars_still_nine` passes.

- [ ] **Step 3: Implement**

In `env_dict`, change the signature and add the pin just before `return env`:

```python
def env_dict(name: str, binding: Optional[dict] = None) -> dict:
    """The environment a context exports, as a plain dict (shared by _env and exec).
    A worktree binding for this context may pin the subscription."""
```

```python
    # A worktree binding's pin replaces the registry subscription in exactly
    # these two variables (read_binding already enforced GUID shape).
    if binding and binding.get("context") == name and binding.get("subscription"):
        env["ARM_SUBSCRIPTION_ID"] = binding["subscription"]
        env["CLOUDCTX_AZURE_LABEL"] = binding["subscription"]
    return env
```

Replace `env_lines` and `cmd_env`:

```python
def env_lines(name: str, binding: Optional[dict] = None) -> list:
    env = env_dict(name, binding)
    lines = [f"export {k}={shquote(v)}" for k, v in env.items()]
    # Explicitly unset every managed var this context does NOT set, so a single
    # eval fully *replaces* the previous context (no stale AWS_*/label bleed).
    lines += [f"unset {var}" for var in CLEARABLE_VARS if var not in env]
    return lines


def cmd_env(args) -> int:
    if args.clear:
        print("\n".join(f"unset {var}" for var in CLEARABLE_VARS))
        return 0
    if args.binding:
        # Shim start-up path: an unusable binding prints nothing (the shim then
        # leaves the shell alone) and load_binding_file has already warned.
        loaded = load_binding_file(Path(args.binding))
        if loaded is None:
            return 0
        _, binding = loaded
        print("\n".join(env_lines(binding["context"], binding)))
        return 0
    if not args.name:
        return die("usage: cloudctx _env <name> | --binding <path> | --clear")
    if args.name not in load_registry():
        return die(f"unknown context {args.name!r}")
    print("\n".join(env_lines(args.name)))
    return 0
```

In `build_parser`, after `p_env.add_argument("--clear", action="store_true")`:

```python
    p_env.add_argument("--binding", metavar="PATH",
                       help="emit the env of the context bound by this .cloudctx file")
```

- [ ] **Step 4: Run, expect PASS**

Run: `python3 -m unittest tests.test_cloudctx tests.test_companion_contract 2>&1 | tail -3`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add cloudctx tests/test_cloudctx.py
git commit -m "Pin a bound subscription in env_dict and _env --binding"
```

---

### Task 5: `exec` honours the folder's binding

**Files:**
- Modify: `cloudctx:584-600` (`cmd_exec`)
- Test: `tests/test_cloudctx.py` (new class `TestBindingExec` after `TestBindingEnv`)

**Interfaces:**
- Consumes: `load_binding_for(start)`, `env_dict(name, binding)`.
- Produces: `cmd_exec` behaviour of spec section 4: same context applies the pin; a different context runs with its registry values and prints `cloudctx: note: this folder is bound to <Y>`.

- [ ] **Step 1: Write the failing tests**

```python
class TestBindingExec(Base):
    REG_SUB = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    GLOBEX_SUB = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
    PIN = "99999999-8888-7777-6666-555555555555"
    SHOW = 'printf "C=%s S=%s L=%s" "$CLOUDCTX_CONTEXT" "$ARM_SUBSCRIPTION_ID" "$CLOUDCTX_AZURE_LABEL"'

    def setUp(self):
        super().setUp()
        self.run_cli("new", "acme", "--azure-tenant", "t",
                     "--azure-subscription", self.REG_SUB, "--no-login")
        self.run_cli("new", "globex", "--azure-tenant", "t2",
                     "--azure-subscription", self.GLOBEX_SUB, "--no-login")
        self.dir = Path(tempfile.mkdtemp(prefix="cctx-exec-")).resolve()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        (self.dir / "sub").mkdir()

    def bind(self, text):
        (self.dir / ".cloudctx").write_text(text)

    def exec_in(self, cwd, name):
        env = dict(os.environ)
        env["CLOUDCTX_HOME"] = self.tmp
        env["ARM_SUBSCRIPTION_ID"] = "stale-inherited"
        return subprocess.run([CLI, "exec", name, "--", "sh", "-c", self.SHOW],
                              cwd=str(cwd), env=env, capture_output=True, text=True)

    def test_same_context_applies_pin(self):
        self.bind(f'context = "acme"\nsubscription = "{self.PIN}"\n')
        r = self.exec_in(self.dir / "sub", "acme")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, f"C=acme S={self.PIN} L={self.PIN}")
        self.assertNotIn("note:", r.stderr)

    def test_same_context_without_pin_uses_registry(self):
        self.bind('context = "acme"\n')
        r = self.exec_in(self.dir, "acme")
        self.assertEqual(r.stdout, f"C=acme S={self.REG_SUB} L={self.REG_SUB}")

    def test_other_context_runs_unpinned_with_note(self):
        self.bind(f'context = "acme"\nsubscription = "{self.PIN}"\n')
        r = self.exec_in(self.dir, "globex")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, f"C=globex S={self.GLOBEX_SUB} L={self.GLOBEX_SUB}")
        self.assertIn("cloudctx: note: this folder is bound to acme\n", r.stderr)

    def test_no_binding_unchanged(self):
        r = self.exec_in(self.dir, "acme")
        self.assertEqual(r.stdout, f"C=acme S={self.REG_SUB} L={self.REG_SUB}")
        self.assertEqual(r.stderr, "")

    def test_broken_binding_warns_and_runs_unpinned(self):
        self.bind('context = "nope"\n')
        r = self.exec_in(self.dir, "acme")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, f"C=acme S={self.REG_SUB} L={self.REG_SUB}")
        self.assertIn("ignoring binding", r.stderr)
```

- [ ] **Step 2: Run, expect FAIL**

Run: `python3 -m unittest tests.test_cloudctx.TestBindingExec -v`
Expected: `test_same_context_applies_pin` fails (S is the registry sub), `test_other_context_runs_unpinned_with_note` fails (no note), `test_broken_binding_warns_and_runs_unpinned` fails (no warning). The other two pass.

- [ ] **Step 3: Implement**

In `cmd_exec`, replace `env.update(env_dict(name))` with:

```python
    binding = None
    found = load_binding_for(Path.cwd())
    if found is not None:
        _, bound = found
        if bound["context"] == name:
            binding = bound
        else:
            print(f"cloudctx: note: this folder is bound to {bound['context']}",
                  file=sys.stderr)
    env.update(env_dict(name, binding))
```

- [ ] **Step 4: Run, expect PASS**

Run: `python3 -m unittest tests.test_cloudctx tests.test_companion_contract 2>&1 | tail -3`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add cloudctx tests/test_cloudctx.py
git commit -m "Apply a folder's binding pin in exec; note a mismatched context"
```

---

### Task 6: `bind` and `unbind` commands

**Files:**
- Modify: `cloudctx` (new functions after `load_binding_for` from Task 3; parser after `p_show` ~1012), `shell/cloudctx.zsh:72-80` (completion), `shell/cloudctx.bash:98-106` (completion)
- Test: `tests/test_cloudctx.py` (module helper `git_cmd`, new class `TestBind`), `tests/test_shell.py` (bash completion test)

**Interfaces:**
- Consumes: `_git(install_dir, *args, timeout=30)` (`cloudctx:826`, returns `CompletedProcess` or None), `_escape`, `GUID_RE`, `load_registry`, `load_binding_for`, `BINDING_FILE`, `die`.
- Produces:
  - `binding_target(cwd: Path) -> Tuple[Path, Optional[Path]]` (folder for the file, git common dir or None)
  - `write_binding(folder: Path, name: str, subscription: Optional[str]) -> Path`
  - `ensure_excluded(common_dir: Path) -> None`
  - `cmd_bind(args) -> int`, `cmd_unbind(args) -> int`
  - CLI: `cloudctx bind [<name> [--subscription <guid>]]`, `cloudctx unbind`

- [ ] **Step 1: Write the failing tests**

Module-level helper in `tests/test_cloudctx.py`, after `write_stub`:

```python
def git_cmd(cwd, *args):
    """Run git hermetically (no signing, fixed identity) in `cwd`."""
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main", *args],
        cwd=str(cwd), check=True, capture_output=True, text=True)
```

```python
@unittest.skipUnless(shutil.which("git"), "git not installed")
class TestBind(Base):
    PIN = "99999999-8888-7777-6666-555555555555"

    def setUp(self):
        super().setUp()
        self.addCleanup(os.chdir, os.getcwd())
        self.run_cli("new", "acme", "--azure-tenant", "t", "--no-login")
        self.run_cli("new", "awsonly", "--aws-profile", "p", "--no-login")
        self.base = Path(tempfile.mkdtemp(prefix="cctx-repo-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        git_cmd(self.repo, "init", "-q")
        os.chdir(self.repo)

    def exclude_lines(self, common):
        p = common / "info" / "exclude"
        return p.read_text().splitlines() if p.exists() else []

    def test_bind_writes_file_at_toplevel(self):
        (self.repo / "a" / "b").mkdir(parents=True)
        os.chdir(self.repo / "a" / "b")
        code, out = self.run_cli("bind", "acme", "--subscription", self.PIN)
        self.assertEqual(code, 0, out)
        self.assertEqual((self.repo / ".cloudctx").read_text(),
                         f'context = "acme"\nsubscription = "{self.PIN}"\n')
        self.assertFalse((self.repo / "a" / "b" / ".cloudctx").exists())
        self.assertEqual(list(self.repo.glob(".cloudctx.tmp")), [])

    def test_bind_without_subscription(self):
        code, _ = self.run_cli("bind", "acme")
        self.assertEqual(code, 0)
        self.assertEqual((self.repo / ".cloudctx").read_text(), 'context = "acme"\n')

    def test_bind_unknown_context(self):
        code, out = self.run_cli("bind", "nope")
        self.assertEqual(code, 1)
        self.assertIn("unknown context", out)
        self.assertFalse((self.repo / ".cloudctx").exists())

    def test_bind_rejects_non_guid_subscription(self):
        code, out = self.run_cli("bind", "acme", "--subscription", "Prod")
        self.assertEqual(code, 1)
        self.assertIn("GUID", out)
        self.assertFalse((self.repo / ".cloudctx").exists())

    def test_bind_rejects_subscription_for_aws_only(self):
        code, out = self.run_cli("bind", "awsonly", "--subscription", self.PIN)
        self.assertEqual(code, 1)
        self.assertIn("azure_tenant", out)

    def test_bind_adds_exclude_line_once(self):
        self.run_cli("bind", "acme")
        self.run_cli("bind", "acme", "--subscription", self.PIN)
        self.assertEqual(self.exclude_lines(self.repo / ".git").count(".cloudctx"), 1)
        status = git_cmd(self.repo, "status", "--porcelain").stdout
        self.assertNotIn(".cloudctx", status)

    def test_bind_creates_missing_info_dir(self):
        shutil.rmtree(self.repo / ".git" / "info", ignore_errors=True)
        code, out = self.run_cli("bind", "acme")
        self.assertEqual(code, 0, out)
        self.assertIn(".cloudctx", self.exclude_lines(self.repo / ".git"))

    def test_bind_refuses_tracked_file(self):
        (self.repo / ".cloudctx").write_text('context = "acme"\n')
        git_cmd(self.repo, "add", ".cloudctx")
        code, out = self.run_cli("bind", "acme", "--subscription", self.PIN)
        self.assertEqual(code, 1)
        self.assertIn("tracked", out)
        self.assertEqual((self.repo / ".cloudctx").read_text(), 'context = "acme"\n')

    def test_bind_in_linked_worktree_uses_common_exclude(self):
        git_cmd(self.repo, "commit", "--allow-empty", "-q", "-m", "init")
        wt = self.base / "wt"
        git_cmd(self.repo, "worktree", "add", "-q", str(wt))
        os.chdir(wt)
        code, out = self.run_cli("bind", "acme")
        self.assertEqual(code, 0, out)
        self.assertTrue((wt / ".cloudctx").is_file())
        self.assertFalse((self.repo / ".cloudctx").exists())
        self.assertIn(".cloudctx", self.exclude_lines(self.repo / ".git"))
        self.assertEqual(git_cmd(wt, "status", "--porcelain").stdout, "")

    def test_bind_in_path_with_space_and_quote(self):
        odd = self.base / "it's a repo"
        odd.mkdir()
        git_cmd(odd, "init", "-q")
        os.chdir(odd)
        code, out = self.run_cli("bind", "acme", "--subscription", self.PIN)
        self.assertEqual(code, 0, out)
        r = subprocess.run(
            ["sh", "-c", 'eval "$("$0" _env --binding "$1")"; printf %s "$ARM_SUBSCRIPTION_ID"',
             CLI, str(odd / ".cloudctx")],
            env=dict(os.environ), capture_output=True, text=True)
        self.assertEqual(r.stdout, self.PIN, r.stderr)

    def test_bind_outside_git_writes_cwd(self):
        plain = self.base / "plain"
        plain.mkdir()
        os.chdir(plain)
        code, out = self.run_cli("bind", "acme")
        self.assertEqual(code, 0, out)
        self.assertTrue((plain / ".cloudctx").is_file())
        self.assertFalse((plain / ".git").exists())

    def test_bind_without_git_installed_writes_cwd(self):
        os.environ["PATH"] = str(self.base / "empty-path")
        code, out = self.run_cli("bind", "acme")
        self.assertEqual(code, 0, out)
        self.assertTrue((self.repo / ".cloudctx").is_file())

    def test_bind_refuses_home(self):
        home = self.base / "home"
        home.mkdir()
        os.environ["HOME"] = str(home)
        os.chdir(home)
        code, out = self.run_cli("bind", "acme")
        self.assertEqual(code, 1)
        self.assertIn("refusing", out)
        self.assertFalse((home / ".cloudctx").exists())

    def test_bind_refuses_root(self):
        os.chdir("/")
        code, out = self.run_cli("bind", "acme")
        self.assertEqual(code, 1)
        self.assertIn("refusing", out)

    def test_bind_refuses_directory_named_cloudctx(self):
        (self.repo / ".cloudctx").mkdir()
        code, out = self.run_cli("bind", "acme")
        self.assertEqual(code, 1)
        self.assertIn("cannot write", out)

    def test_bind_no_args_shows_binding(self):
        self.run_cli("bind", "acme", "--subscription", self.PIN)
        (self.repo / "deep").mkdir()
        os.chdir(self.repo / "deep")
        code, out = self.run_cli("bind")
        self.assertEqual(code, 0)
        self.assertIn(str(self.repo / ".cloudctx"), out)
        self.assertIn("context:      acme", out)
        self.assertIn(f"subscription: {self.PIN}", out)

    def test_bind_no_args_without_binding(self):
        code, out = self.run_cli("bind")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "no binding")

    def test_bind_subscription_without_name_is_usage_error(self):
        code, out = self.run_cli("bind", "--subscription", self.PIN)
        self.assertEqual(code, 1)
        self.assertIn("usage", out)

    def test_unbind_removes_file_keeps_exclude(self):
        self.run_cli("bind", "acme")
        (self.repo / "sub").mkdir()
        os.chdir(self.repo / "sub")
        code, out = self.run_cli("unbind")
        self.assertEqual(code, 0, out)
        self.assertFalse((self.repo / ".cloudctx").exists())
        self.assertIn(".cloudctx", self.exclude_lines(self.repo / ".git"))

    def test_unbind_without_binding(self):
        code, out = self.run_cli("unbind")
        self.assertEqual(code, 0)
        self.assertIn("no binding", out)
```

In `tests/test_shell.py` add to `TestShimZsh` (runs for both shells via the subclass, bash path only):

```python
    def test_bash_completion_offers_bind(self):
        if self.shell != "bash":
            self.skipTest("bash completion internals")
        script = (
            f"source {ROOT}/shell/cloudctx.bash\n"
            'COMP_WORDS=(cloudctx bi); COMP_CWORD=1\n'
            "_cloudctx_complete\n"
            'echo "CCTX=${COMPREPLY[*]}"\n'
            'COMP_WORDS=(cloudctx bind ""); COMP_CWORD=2\n'
            "_cloudctx_complete\n"
            'echo "CCTX=${COMPREPLY[*]}"\n'
        )
        r = subprocess.run(["bash", "--norc", "--noprofile", "-c", script],
                           cwd=self.home, env=self.env, capture_output=True, text=True)
        lines = [l for l in r.stdout.splitlines() if l.startswith(MARK)]
        self.assertEqual(lines[0], f"{MARK}bind", msg=r.stderr)
        self.assertIn("acme", lines[1])
```

- [ ] **Step 2: Run, expect FAIL**

Run: `python3 -m unittest tests.test_cloudctx.TestBind tests.test_shell.TestShimBash.test_bash_completion_offers_bind -v`
Expected: `TestBind` tests fail with argparse exit 2 (`invalid choice: 'bind'`); the completion test fails (`COMPREPLY` empty for `bi`).

- [ ] **Step 3: Implement the commands**

Add after `load_binding_for` in `cloudctx`:

```python
def binding_target(cwd: Path) -> Tuple[Path, Optional[Path]]:
    """(folder the binding file belongs in, git common dir or None).
    Inside a git worktree: its toplevel and the repo's shared common dir.
    Outside git, or with no git installed: cwd and None."""
    r = _git(cwd, "rev-parse", "--show-toplevel", "--git-common-dir", timeout=10)
    if r is None or r.returncode != 0:
        return cwd, None
    lines = r.stdout.splitlines()
    if len(lines) < 2:
        return cwd, None
    common = Path(lines[1])
    if not common.is_absolute():
        common = cwd / common  # git prints it relative to the -C directory
    return Path(lines[0]), common.resolve()


def write_binding(folder: Path, name: str, subscription: Optional[str]) -> Path:
    """Write folder/.cloudctx atomically (temp file in the same dir, os.replace)."""
    path = folder / BINDING_FILE
    text = f'context = "{_escape(name)}"\n'
    if subscription:
        text += f'subscription = "{_escape(subscription)}"\n'
    tmp = folder / (BINDING_FILE + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    return path


def ensure_excluded(common_dir: Path) -> None:
    """Add .cloudctx to <common dir>/info/exclude once. The common dir is
    shared by every worktree of the repo, so one line covers them all."""
    info = common_dir / "info"
    info.mkdir(parents=True, exist_ok=True)
    exclude = info / "exclude"
    text = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
    if BINDING_FILE in (line.strip() for line in text.splitlines()):
        return
    if text and not text.endswith("\n"):
        text += "\n"
    exclude.write_text(text + BINDING_FILE + "\n", encoding="utf-8")


def _show_binding(cwd: Path) -> int:
    found = load_binding_for(cwd)
    if found is None:
        print("no binding")
        return 0
    path, binding = found
    print(f"binding:      {path}")
    print(f"context:      {binding['context']}")
    print(f"subscription: {binding['subscription'] or '(registry default)'}")
    return 0


def cmd_bind(args) -> int:
    cwd = Path.cwd()
    if not args.name:
        if args.subscription:
            return die("usage: cloudctx bind <name> [--subscription <guid>]")
        return _show_binding(cwd)
    registry = load_registry()
    if args.name not in registry:
        return die(f"unknown context {args.name!r}")
    sub = args.subscription
    if sub is not None:
        if not GUID_RE.match(sub):
            return die(f"--subscription must be a GUID, got {sub!r}")
        if not registry[args.name].get("azure_tenant"):
            return die(f"context {args.name!r} has no azure_tenant; "
                       "--subscription applies to Azure contexts only")
    folder, common = binding_target(cwd)
    resolved = folder.resolve()
    if resolved in (Path.home().resolve(), Path("/")):
        return die(f"refusing to bind {resolved}: a binding there would apply "
                   "to every shell below it")
    if common is not None:
        tracked = _git(folder, "ls-files", "--error-unmatch", "--", BINDING_FILE,
                       timeout=10)
        if tracked is not None and tracked.returncode == 0:
            return die(f"{folder / BINDING_FILE} is tracked by git; a committed "
                       "binding would apply to everyone who clones the repo")
    try:
        path = write_binding(folder, args.name, sub)
    except OSError as e:
        return die(f"cannot write {folder / BINDING_FILE}: {e}")
    if common is not None:
        try:
            ensure_excluded(common)
        except OSError as e:
            print(f"cloudctx: warning: could not update {common}/info/exclude: {e}",
                  file=sys.stderr)
    print(f"bound {path} to {args.name}"
          + (f" (subscription {sub})" if sub else ""))
    return 0


def cmd_unbind(args) -> int:
    folder, _ = binding_target(Path.cwd())
    path = folder / BINDING_FILE
    if not path.is_file():
        print(f"no binding at {path}")
        return 0
    path.unlink()
    print(f"removed {path}")
    return 0
```

In `build_parser`, after the `p_show` block:

```python
    p_bind = sub.add_parser("bind", help="bind this worktree (or folder) to a context; "
                                         "no arguments: show the binding here")
    p_bind.add_argument("name", nargs="?")
    p_bind.add_argument("--subscription", metavar="GUID",
                        help="pin this subscription for the worktree")
    p_bind.set_defaults(func=cmd_bind)

    p_unbind = sub.add_parser("unbind", help="remove this worktree's .cloudctx binding")
    p_unbind.set_defaults(func=cmd_unbind)
```

- [ ] **Step 4: Add completion in both shims**

`shell/cloudctx.zsh`, in `_cloudctx`:

```zsh
  subcmds=(use clear new list show delete status login exec open claude
           bind unbind gen-profiles install self-update)
```

```zsh
      use|login|exec|open|claude|delete|show|bind)
```

`shell/cloudctx.bash`, in `_cloudctx_complete`:

```bash
    COMPREPLY=($(compgen -W "use clear new list show delete status login exec open claude bind unbind gen-profiles install self-update" -- "$cur"))
```

```bash
      use|login|exec|open|claude|delete|show|bind)
```

- [ ] **Step 5: Run, expect PASS**

Run: `python3 -m unittest tests.test_cloudctx tests.test_shell tests.test_companion_contract 2>&1 | tail -3`
Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add cloudctx shell/cloudctx.zsh shell/cloudctx.bash tests/test_cloudctx.py tests/test_shell.py
git commit -m "Add bind and unbind commands"
```

---

### Task 7: shims activate a binding at shell start

**Files:**
- Modify: `shell/cloudctx.zsh` (append after the `PROMPT=` line), `shell/cloudctx.bash` (append after the `PS1=` line)
- Test: `tests/test_shell.py` (`TestShimZsh.run_script` gets `cwd=self.home`; new tests in `TestShimZsh`, inherited by `TestShimBash`; `TestShimBash.run_script` also gets `cwd=self.home`)

**Interfaces:**
- Consumes: `cloudctx _env --binding <path>` (Task 4), `cloudctx _decorate <name>`.
- Produces: shell functions `_cloudctx_find_binding` (sets `_cloudctx_binding`, returns 0 when found) and `_cloudctx_apply_binding`, called once at source time.

- [ ] **Step 1: Make existing shell tests hermetic and write the failing tests**

In both `run_script` methods add `cwd=self.home` to `subprocess.run` (the repo checkout must not decide whether a binding applies).

Add to `TestShimZsh`:

```python
    PIN = "99999999-8888-7777-6666-555555555555"

    def _argv(self, interactive=False):
        flags = ["-f"] if self.shell == "zsh" else ["--norc", "--noprofile"]
        return [self.shell, *flags, *(["-i"] if interactive else [])]

    def run_in(self, cwd, body, env=None, interactive=False):
        script = f"source {ROOT}/shell/cloudctx.{self.shell}\n" + textwrap.dedent(body)
        return subprocess.run([*self._argv(interactive), "-c", script], cwd=str(cwd),
                              env=env or self.env, capture_output=True, text=True)

    def _show(self):
        echo = "print -r --" if self.shell == "zsh" else "echo"
        return f'{echo} "{MARK}$CLOUDCTX_CONTEXT|$ARM_SUBSCRIPTION_ID|$CLOUDCTX_AZURE_LABEL"'

    def bound_dir(self, name="bound", text=None):
        # resolve(): the shell reports the physical path (/private/var on macOS)
        d = Path(tempfile.mkdtemp(prefix="cctx-wt-")).resolve() / name
        self.addCleanup(shutil.rmtree, d.parent, ignore_errors=True)
        d.mkdir()
        (d / ".cloudctx").write_text(
            text or f'context = "acme"\nsubscription = "{self.PIN}"\n')
        (d / "sub").mkdir()
        return d

    def test_start_in_bound_folder_activates(self):
        d = self.bound_dir()
        r = self.run_in(d / "sub", self._show())
        self.assertEqual(extract(r.stdout), f"acme|{self.PIN}|{self.PIN}", msg=r.stderr)

    def test_binding_overrides_inherited_context(self):
        d = self.bound_dir()
        env = dict(self.env, CLOUDCTX_CONTEXT="other", ARM_SUBSCRIPTION_ID="stale",
                   AWS_PROFILE="stale-profile")
        r = self.run_in(d, self._show() + "\n" +
                        ('print -r -- "CCTX2=${AWS_PROFILE-unset}"' if self.shell == "zsh"
                         else 'echo "CCTX2=${AWS_PROFILE-unset}"'), env=env)
        self.assertEqual(extract(r.stdout), f"acme|{self.PIN}|{self.PIN}", msg=r.stderr)
        self.assertIn("CCTX2=unset", r.stdout)

    def test_use_after_start_wins(self):
        self._new("globex", "--azure-tenant", "t2", "--azure-subscription", "Other")
        d = self.bound_dir()
        r = self.run_in(d, "cloudctx use globex >/dev/null\n" + self._show())
        self.assertEqual(extract(r.stdout), "globex||Other", msg=r.stderr)

    def test_cd_into_bound_folder_changes_nothing(self):
        d = self.bound_dir()
        plain = Path(tempfile.mkdtemp(prefix="cctx-plain-")).resolve()
        self.addCleanup(shutil.rmtree, plain, ignore_errors=True)
        r = self.run_in(plain, f'cd "{d}"\n' + self._show())
        self.assertEqual(extract(r.stdout), "||", msg=r.stderr)

    def test_interactive_start_prints_bound_line(self):
        d = self.bound_dir()
        r = self.run_in(d, "true", interactive=True)
        self.assertIn(f"cloudctx: acme (bound by {d}/.cloudctx)", r.stderr)

    def test_non_interactive_start_is_silent(self):
        d = self.bound_dir()
        r = self.run_in(d, "true")
        self.assertNotIn("bound by", r.stderr)

    def test_binding_in_path_with_space_and_quote(self):
        d = self.bound_dir(name="it's bound")
        r = self.run_in(d, self._show(), interactive=True)
        self.assertEqual(extract(r.stdout), f"acme|{self.PIN}|{self.PIN}", msg=r.stderr)
        self.assertIn(f"(bound by {d}/.cloudctx)", r.stderr)

    def test_unknown_context_binding_is_ignored(self):
        d = self.bound_dir(text='context = "nope"\n')
        r = self.run_in(d, self._show())
        self.assertEqual(r.returncode, 0)
        self.assertEqual(extract(r.stdout), "||", msg=r.stderr)
        self.assertIn("ignoring binding", r.stderr)

    def _stub_env(self):
        stub = Path(tempfile.mkdtemp(prefix="cctx-stub-"))
        self.addCleanup(shutil.rmtree, stub, ignore_errors=True)
        log = stub / "calls.log"
        p = stub / "cloudctx"
        p.write_text(f'#!/bin/sh\necho "$*" >> "{log}"\n')
        p.chmod(0o755)
        env = dict(self.env)
        env["PATH"] = f"{stub}:{env['PATH']}"
        return env, log

    def test_start_outside_binding_runs_no_cloudctx(self):
        env, log = self._stub_env()
        plain = Path(tempfile.mkdtemp(prefix="cctx-plain-")).resolve()
        self.addCleanup(shutil.rmtree, plain, ignore_errors=True)
        r = self.run_in(plain, "true", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(log.exists(), log.read_text() if log.exists() else "")

    def test_registry_dir_named_cloudctx_is_not_a_binding(self):
        env, log = self._stub_env()
        top = Path(tempfile.mkdtemp(prefix="cctx-home-")).resolve()
        self.addCleanup(shutil.rmtree, top, ignore_errors=True)
        (top / ".cloudctx").mkdir()          # like ~/.cloudctx
        (top / "proj").mkdir()
        r = self.run_in(top / "proj", "true", env=env)
        self.assertFalse(log.exists(), r.stderr)

    def test_start_in_bound_folder_calls_env_binding(self):
        env, log = self._stub_env()
        d = self.bound_dir()
        self.run_in(d, "true", env=env)
        self.assertIn(f"_env --binding {d}/.cloudctx", log.read_text())
```

Move `_new` (currently defined after `test_prompt_includes_aws_profile`) above `test_use_after_start_wins` if Python complains; methods are looked up at call time, so its position does not matter.

- [ ] **Step 2: Run, expect FAIL**

Run: `python3 -m unittest tests.test_shell -v 2>&1 | grep -E "FAIL|ERROR|OK|Ran"`
Expected: in both `TestShimZsh` and `TestShimBash`, these FAIL: `test_start_in_bound_folder_activates`, `test_binding_overrides_inherited_context`, `test_interactive_start_prints_bound_line`, `test_binding_in_path_with_space_and_quote`, `test_unknown_context_binding_is_ignored` (no warning), `test_start_in_bound_folder_calls_env_binding` (log missing). The no-subprocess, cd, use and silent tests pass already.

- [ ] **Step 3: Implement the zsh start-up step**

Append to `shell/cloudctx.zsh`:

```zsh
# --- worktree binding (runs once, now) -------------------------------------
# Walk up from $PWD in pure shell for a regular file named .cloudctx (a
# directory with that name, like the registry root ~/.cloudctx, never counts).
# Only when one exists does the binary run, so an unbound shell pays nothing.
# Nothing hooks chpwd/precmd: a binding applies at shell start only.
_cloudctx_find_binding() {
  emulate -L zsh
  local d=$PWD
  typeset -g _cloudctx_binding=
  while :; do
    if [[ -f "${d%/}/.cloudctx" ]]; then
      _cloudctx_binding="${d%/}/.cloudctx"
      return 0
    fi
    [[ -z "$d" || "$d" == / ]] && return 1
    d=${d%/*}
    [[ -n "$d" ]] || d=/
  done
}

_cloudctx_apply_binding() {
  emulate -L zsh
  _cloudctx_find_binding || return 0
  local _cloudctx_env
  _cloudctx_env="$(command cloudctx _env --binding "$_cloudctx_binding")" || return 0
  [[ -n "$_cloudctx_env" ]] || return 0
  eval "$_cloudctx_env"
  command cloudctx _decorate "$CLOUDCTX_CONTEXT"
  if [[ -o interactive ]]; then
    print -u2 -r -- "cloudctx: $CLOUDCTX_CONTEXT (bound by $_cloudctx_binding)"
  fi
}
_cloudctx_apply_binding
```

- [ ] **Step 4: Implement the bash start-up step**

Append to `shell/cloudctx.bash`:

```bash
# --- worktree binding (runs once, now) -------------------------------------
# Walk up from $PWD in pure shell for a regular file named .cloudctx (a
# directory with that name, like the registry root ~/.cloudctx, never counts).
# Only when one exists does the binary run, so an unbound shell pays nothing.
# Nothing is added to PROMPT_COMMAND: a binding applies at shell start only.
_cloudctx_find_binding() {
  local d=$PWD
  _cloudctx_binding=
  while :; do
    if [[ -f "${d%/}/.cloudctx" ]]; then
      _cloudctx_binding="${d%/}/.cloudctx"
      return 0
    fi
    [[ -z "$d" || "$d" == / ]] && return 1
    d=${d%/*}
    [[ -n "$d" ]] || d=/
  done
}

_cloudctx_apply_binding() {
  _cloudctx_find_binding || return 0
  local _cloudctx_env
  _cloudctx_env="$(command cloudctx _env --binding "$_cloudctx_binding")" || return 0
  [ -n "$_cloudctx_env" ] || return 0
  eval "$_cloudctx_env"
  command cloudctx _decorate "$CLOUDCTX_CONTEXT"
  case $- in
    *i*) echo "cloudctx: $CLOUDCTX_CONTEXT (bound by $_cloudctx_binding)" >&2 ;;
  esac
}
_cloudctx_apply_binding
```

- [ ] **Step 5: Run, expect PASS**

Run: `python3 -m unittest tests.test_shell -v 2>&1 | tail -5`
Expected: `OK`. If `bash -i` prints job-control noise on stderr, the `assertIn` checks still hold; do not assert an empty stderr for interactive runs.

- [ ] **Step 6: Full suite and commit**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -3`
Expected: `OK`.

```bash
git add shell/cloudctx.zsh shell/cloudctx.bash tests/test_shell.py
git commit -m "Activate a worktree binding when the shell shim is sourced"
```

---

### Task 8: isolation proof covers two bound worktrees

**Files:**
- Modify: `tests/isolation_proof.sh` (insert before `rm -rf "$HOME_DIR"`)

**Interfaces:**
- Consumes: `cloudctx bind`, `cloudctx _env --binding` (the shim's path), contexts `acme` and `globex` already created in the script (their registry subscriptions are names, so `ARM_SUBSCRIPTION_ID` can only come from the pin).
- Produces: a second PASS line; non-zero exit when the two worktrees share `AZURE_CONFIG_DIR` or `ARM_SUBSCRIPTION_ID`.

- [ ] **Step 1: Extend the script**

Insert before `rm -rf "$HOME_DIR"`:

```sh
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
```

- [ ] **Step 2: Run, expect PASS**

Run: `sh tests/isolation_proof.sh; echo "exit=$?"`
Expected: both PASS lines, `exit=0`. Sanity check that it can fail: temporarily change `PIN_B` in the `[ "${WB#*|}" = "$PIN_B" ]` line to `$PIN_A`, run, expect `FAIL: globex worktree subscription` and `exit=1`, then revert.

- [ ] **Step 3: Commit**

```bash
git add tests/isolation_proof.sh
git commit -m "Prove two bound worktrees stay isolated"
```

---

### Task 9: docs, skill and version 1.6.0

**Files:**
- Modify: `cloudctx:23` (`__version__`), `README.md:66-97` (usage block), `README.md:110-116` (new section after "Claude Code"), `skills/cloudctx/SKILL.md` (new section after "Choosing the context", before "When no context exists" at ~61)
- Test: `tests/test_cloudctx.py` `TestVersion` (~932, existing), `tests/test_companion_contract.py` (unchanged), `tests/test_site.py` (unchanged: it checks section ids and external links only, no version or command list)

**Interfaces:**
- Consumes: everything above.
- Produces: version string `cloudctx 1.6.0`; user and agent docs.

- [ ] **Step 1: Bump the version**

`cloudctx:23`:

```python
__version__ = "1.6.0"
```

- [ ] **Step 2: README usage lines**

In the `## Usage` code block, after the `cloudctx exec acme -- terraform plan` line:

```sh
cloudctx bind acme --subscription <guid>  # bind this git worktree (or folder) to acme;
                                          #   new shells started here pick it up
cloudctx bind              # show the binding that applies here, or `no binding`
cloudctx unbind            # remove this worktree's .cloudctx
```

- [ ] **Step 3: README section "Using cloudctx in Orca"**

Insert after the `## Claude Code` section:

```markdown
## Using cloudctx in Orca

[Orca](https://www.onorca.dev/) runs each task in its own git worktree, and every
terminal there starts a login shell that sources the cloudctx shim. A `.cloudctx`
file at the worktree root (written by `cloudctx bind`, kept out of git through
`.git/info/exclude`) makes that shell start in the bound context, with the
subscription pin in `ARM_SUBSCRIPTION_ID`.

- Bind once per worktree: `cloudctx bind <name> [--subscription <guid>]` in the
  worktree's first terminal; terminals opened after that pick it up. Terminals
  that were already open keep their old environment (Orca freezes each
  terminal's env when it spawns).
- Orca's default Claude command is `claude --dangerously-skip-permissions`,
  which turns off every permission prompt. Set the Claude agent command to
  plain `claude` under Settings, Agents, so approval rules still apply.
- Use terminal agents. Orca's experimental Chat view starts agents without a
  shell, from a cached environment that does not depend on the folder, so it
  never sees the binding.

`cloudctx bind` with no arguments shows what the current folder is bound to;
`cloudctx unbind` removes the file. A `cd` never switches context: the binding
applies when a shell starts, and `cloudctx use <name>` still wins afterwards.
```

- [ ] **Step 4: SKILL.md "Bound worktrees"**

Insert before `## When no context exists`:

```markdown
## Bound worktrees

A worktree can carry a `.cloudctx` binding; a shell started there is already scoped,
possibly to one pinned subscription.

- When `$CLOUDCTX_CONTEXT` is set at session start, it is the scope; do not switch away
  from it without asking.
- Pass `--subscription "$ARM_SUBSCRIPTION_ID"` on every `az` call that accepts it
  whenever `ARM_SUBSCRIPTION_ID` is set.
- Never run `az account set` in a bound worktree: it changes the default for every other
  worktree on the same context.
- `cloudctx bind` with no arguments shows what the folder is bound to.
```

- [ ] **Step 5: Full verification**

Run: `python3 -m unittest discover -s tests -v 2>&1 | tail -5`
Expected: `OK` (companion contract tests included and unedited; `git diff main -- tests/test_companion_contract.py docs/companions.md` prints nothing).

Run: `sh tests/isolation_proof.sh`
Expected: both PASS lines, exit 0.

Run: `./cloudctx --version`
Expected: `cloudctx 1.6.0`.

Run: `python3 -c "import importlib.machinery as m,importlib.util as u;l=m.SourceFileLoader('c','cloudctx');s=u.spec_from_loader('c',l);c=u.module_from_spec(s);l.exec_module(c);print(len(c.CLEARABLE_VARS))"`
Expected: `9`.

- [ ] **Step 6: Commit**

```bash
git add cloudctx README.md skills/cloudctx/SKILL.md
git commit -m "Document worktree binding and Orca use; version 1.6.0"
```

---

## After merge (Elias merges; not part of the implementer's tasks)

Before the PR: an independent review of the full branch diff (`git diff main...feature/orca-worktree-binding`), re-reviewed if the diff changes materially. The PR body follows the repo's normal style.

1. Tag the merge commit: `git tag v1.6.0 && git push origin v1.6.0`.
2. Update the install clone: `cloudctx self-update` (updates `~/.local/share/cloudctx`); confirm `cloudctx --version` prints `cloudctx 1.6.0`.
3. Replace the installed skill: `cp ~/.local/share/cloudctx/skills/cloudctx/SKILL.md ~/.claude/skills/cloudctx/SKILL.md`.
4. Manual Orca acceptance (spec section 1): in Orca, two worktrees of one repo; run `cloudctx bind <ctx-a> --subscription <guid-a>` in one and `cloudctx bind <ctx-b> --subscription <guid-b>` in the other. Open a new terminal in each: the prompt shows its own context and the start-up line `cloudctx: <ctx> (bound by <path>)` appears. Start `claude` there and have it run `az account show --subscription "$ARM_SUBSCRIPTION_ID"`: it lands in the bound tenant and subscription in each worktree.
