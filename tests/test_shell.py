"""Tests for the sourced shell shims (shell/cloudctx.zsh, shell/cloudctx.bash).

These drive real zsh/bash subprocesses, so they're skipped when the shell
isn't installed. A throwaway $CLOUDCTX_HOME and a stubbed `cloudctx` on PATH
keep them hermetic (no real Azure/iTerm2 needed).
"""
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
import re

ANSI = re.compile(r"\x1b\[[0-9;]*m")

ROOT = Path(__file__).resolve().parent.parent
MARK = "CCTX="


def base_env(home):
    env = dict(os.environ)
    env["CLOUDCTX_HOME"] = home
    env["PATH"] = f"{ROOT}:{env.get('PATH', '')}"  # find the real `cloudctx`
    env.pop("CLOUDCTX_CONTEXT", None)
    return env


def extract(output):
    """Pull the `CCTX=...` marker out of output that may be prefixed by raw
    iTerm2 escape bytes emitted by `_decorate`."""
    idx = output.rfind(MARK)
    if idx == -1:
        return None
    return output[idx + len(MARK):].splitlines()[0]


@unittest.skipUnless(shutil.which("zsh"), "zsh not installed")
class TestShimZsh(unittest.TestCase):
    shell = "zsh"
    rcflag = "-f"

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="cctx-sh-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.env = base_env(self.home)
        subprocess.run([str(ROOT / "cloudctx"), "new", "acme", "--display",
                        "Acme AB", "--color", "#c0392b", "--azure-tenant", "t",
                        "--azure-subscription", "Prod", "--no-login"],
                       env=self.env, check=True, capture_output=True)

    def run_script(self, body):
        script = f"source {ROOT}/shell/cloudctx.{self.shell}\n" + textwrap.dedent(body)
        r = subprocess.run([self.shell, self.rcflag, "-c", script], cwd=self.home,
                           env=self.env, capture_output=True, text=True)
        return r

    def test_use_sets_env(self):
        r = self.run_script(f'''
            cloudctx use acme >/dev/null
            print -r -- "{MARK}$CLOUDCTX_CONTEXT|$AZURE_CONFIG_DIR"
        ''' if self.shell == "zsh" else f'''
            cloudctx use acme >/dev/null
            echo "{MARK}$CLOUDCTX_CONTEXT|$AZURE_CONFIG_DIR"
        ''')
        val = extract(r.stdout)
        self.assertIsNotNone(val, msg=r.stderr)
        ctx, _, azdir = val.partition("|")
        self.assertEqual(ctx, "acme")
        self.assertIn(os.path.join("acme", "azure"), azdir)

    def test_clear_unsets_context(self):
        echo = "print -r --" if self.shell == "zsh" else "echo"
        r = self.run_script(f'''
            cloudctx use acme >/dev/null
            cloudctx clear >/dev/null
            {echo} "{MARK}[$CLOUDCTX_CONTEXT]"
        ''')
        val = extract(r.stdout)
        self.assertEqual(val, "[]", msg=r.stderr)

    def test_unknown_subcommand_delegates_to_binary(self):
        echo = "print -r --" if self.shell == "zsh" else "echo"
        r = self.run_script(f'''
            cloudctx list >/tmp/cloudctx_list_$$ 2>&1
            {echo} "{MARK}$(grep -c acme /tmp/cloudctx_list_$$)"
            rm -f /tmp/cloudctx_list_$$
        ''')
        self.assertEqual(extract(r.stdout), "1", msg=r.stderr)

    # --- Task 7: prompt segment + guard ---
    pvar = "PROMPT"

    def test_guard_warns_without_context(self):
        r = self.run_script('_cloudctx_guard az')
        self.assertIn("no context", r.stderr.lower(), msg=r.stderr)

    def test_guard_silent_with_context(self):
        r = self.run_script('''
            cloudctx use acme >/dev/null
            _cloudctx_guard az
        ''')
        self.assertNotIn("no context", r.stderr.lower(), msg=r.stderr)

    def test_guard_ignores_non_cloud_commands(self):
        r = self.run_script('_cloudctx_guard ls')
        self.assertEqual(r.stderr.strip(), "", msg=r.stderr)

    def test_prompt_has_context_segment(self):
        echo = "print -r --" if self.shell == "zsh" else "echo"
        r = self.run_script(f'{echo} "{MARK}${self.pvar}"')
        self.assertIn("CLOUDCTX_CONTEXT", extract(r.stdout) or "", msg=r.stderr)

    def test_prompt_includes_aws_profile(self):
        # Spec §7.2: the prompt segment surfaces AWS_PROFILE — the only cloud
        # indicator for an AWS-only context. Assert the prompt string references
        # it (conditionally, so it shows only when a context is active).
        echo = "print -r --" if self.shell == "zsh" else "echo"
        r = self.run_script(f'{echo} "{MARK}${self.pvar}"')
        self.assertIn("AWS_PROFILE", extract(r.stdout) or "", msg=r.stderr)

    # --- review regression tests ---
    def _new(self, *args):
        subprocess.run([str(ROOT / "cloudctx"), "new", *args, "--no-login"],
                       env=self.env, check=True, capture_output=True)

    def test_switch_clears_stale_aws_vars(self):
        # Findings #2/#3: switching AWS context -> Azure-only must clear AWS_*.
        self._new("awsctx", "--aws-profile", "awsctx")
        echo = "print -r --" if self.shell == "zsh" else "echo"
        r = self.run_script(f'''
            cloudctx use awsctx >/dev/null
            cloudctx use acme >/dev/null
            {echo} "{MARK}[$CLOUDCTX_CONTEXT|$AWS_PROFILE|$CLOUDCTX_AZURE_LABEL]"
        ''')
        self.assertEqual(extract(r.stdout), "[acme||Prod]", msg=r.stderr)

    def test_switch_clears_stale_label(self):
        # Finding #7: Azure(sub) -> Azure(no sub) must clear the label.
        self._new("bare", "--azure-tenant", "t2")
        echo = "print -r --" if self.shell == "zsh" else "echo"
        r = self.run_script(f'''
            cloudctx use acme >/dev/null
            cloudctx use bare >/dev/null
            {echo} "{MARK}[$CLOUDCTX_CONTEXT|$CLOUDCTX_AZURE_LABEL]"
        ''')
        self.assertEqual(extract(r.stdout), "[bare|]", msg=r.stderr)

    def test_guard_catches_compound_and(self):
        # Review 2026-07-06 #1: `cd x && az login` must still warn.
        r = self.run_script('_cloudctx_guard "cd /tmp && az login"')
        self.assertIn("no context", r.stderr.lower(), msg=r.stderr)

    def test_guard_catches_pipeline(self):
        r = self.run_script('_cloudctx_guard "echo hi | aws s3 ls"')
        self.assertIn("no context", r.stderr.lower(), msg=r.stderr)

    def test_guard_catches_semicolon(self):
        r = self.run_script('_cloudctx_guard "true; az account show"')
        self.assertIn("no context", r.stderr.lower(), msg=r.stderr)

    def test_guard_catches_or_list(self):
        r = self.run_script('_cloudctx_guard "false || az login"')
        self.assertIn("no context", r.stderr.lower(), msg=r.stderr)

    def test_guard_catches_stdbuf_flags(self):
        # Review 2026-07-06 #5: wrapper flags (`stdbuf -oL az`) must be skipped.
        r = self.run_script('_cloudctx_guard "stdbuf -oL az login"')
        self.assertIn("no context", r.stderr.lower(), msg=r.stderr)

    def test_guard_silent_on_compound_without_cloud_cmds(self):
        r = self.run_script('_cloudctx_guard "ls -la && echo done | wc -l"')
        self.assertEqual(r.stderr.strip(), "", msg=r.stderr)

    def test_guard_ignores_separators_inside_quotes(self):
        # A quoted argument containing `&& az ...` is data, not a command.
        r = self.run_script(
            "_cloudctx_guard 'git commit -m \"wire up && az login flow\"'")
        self.assertEqual(r.stderr.strip(), "", msg=r.stderr)

    def test_guard_catches_unspaced_separators(self):
        r = self.run_script("_cloudctx_guard 'cd /tmp&&az login'")
        self.assertIn("no context", r.stderr.lower(), msg=r.stderr)

    def test_guard_strips_wrappers(self):
        # Finding #15: `sudo az ...` should still trigger the guard.
        r = self.run_script('_cloudctx_guard "sudo az login"')
        self.assertIn("no context", r.stderr.lower(), msg=r.stderr)

    def test_guard_strips_assignments(self):
        # Finding #15: leading VAR=val assignments should be skipped.
        r = self.run_script('_cloudctx_guard "FOO=bar aws s3 ls"')
        self.assertIn("no context", r.stderr.lower(), msg=r.stderr)

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

    def test_zsh_prompt_escapes_percent(self):
        # Finding #14: a '%' in the subscription label must not be a prompt escape.
        # Assert (a) the PROMPT embeds the %-doubling, and (b) that idiom renders
        # a literal % under zsh prompt expansion (${(%)...}). If % were NOT
        # doubled, '100%n' would prompt-expand to '100<username>'.
        if self.shell != "zsh":
            self.skipTest("zsh-specific prompt percent escaping")
        r = self.run_script(r'''
            case "$PROMPT" in
              *'//\%/%%'*) hasesc=yes ;;
              *) hasesc=no ;;
            esac
            label='100%n'
            esc="${label//\%/%%}"
            print -r -- "CCTX=$hasesc|${(%)esc}"
        ''')
        self.assertEqual(extract(r.stdout), "yes|100%n", msg=r.stderr)

    def test_completion_registered(self):
        if self.shell == "zsh":
            # compdef only exists after compinit; stub it to test registration
            # hermetically, then assert our completer function is defined and
            # was registered for the `cloudctx` command.
            script = (
                "compdef() { CCTX_REG=\"$*\"; }\n"
                f"source {ROOT}/shell/cloudctx.zsh\n"
                'print -r -- "CCTX=${+functions[_cloudctx]}|$CCTX_REG"\n'
            )
            r = subprocess.run(["zsh", "-f", "-c", script],
                               env=self.env, capture_output=True, text=True)
            self.assertEqual(extract(r.stdout), "1|_cloudctx cloudctx",
                             msg=r.stderr)
        else:
            script = (f"source {ROOT}/shell/cloudctx.bash\n"
                      'echo "CCTX=$(complete -p cloudctx 2>/dev/null)"\n')
            r = subprocess.run(["bash", "--norc", "--noprofile", "-c", script],
                               env=self.env, capture_output=True, text=True)
            self.assertIn("_cloudctx_complete", extract(r.stdout) or "",
                          msg=r.stderr)

    def test_bash_completion_suggests_context_names(self):
        if self.shell != "bash":
            self.skipTest("bash completion internals")
        script = (
            f"source {ROOT}/shell/cloudctx.bash\n"
            'COMP_WORDS=(cloudctx use ""); COMP_CWORD=2\n'
            "_cloudctx_complete\n"
            'echo "CCTX=${COMPREPLY[*]}"\n'
        )
        r = subprocess.run(["bash", "--norc", "--noprofile", "-c", script],
                           env=self.env, capture_output=True, text=True)
        self.assertIn("acme", extract(r.stdout) or "", msg=r.stderr)

    def test_bash_guard_composes_with_bash_preexec(self):
        # Finding #8 (coexistence): when bash-preexec is present, register as a
        # preexec function and DON'T seize the DEBUG trap, so other hooks survive.
        if self.shell != "bash":
            self.skipTest("bash-specific guard registration")
        script = (
            "preexec_functions=()\n"           # simulate bash-preexec being loaded
            "trap 'echo PRIOR' DEBUG\n"        # a pre-existing DEBUG trap
            f"source {ROOT}/shell/cloudctx.bash\n"
            'echo "CCTX=$(declare -p preexec_functions)|$(trap -p DEBUG)"\n'
        )
        r = subprocess.run([self.shell, "--norc", "--noprofile", "-c", script],
                           cwd=self.home, env=self.env, capture_output=True, text=True)
        val = extract(r.stdout) or ""
        self.assertIn("_cloudctx_bp_preexec", val, msg=r.stderr)   # we registered
        self.assertIn("echo PRIOR", val, msg="our shim clobbered the DEBUG trap")

    def test_bash_guard_installs_trap_without_preexec(self):
        # Finding #8 (fallback): on plain bash the guard still installs.
        if self.shell != "bash":
            self.skipTest("bash-specific guard registration")
        script = (f"source {ROOT}/shell/cloudctx.bash\n"
                  'echo "CCTX=$(trap -p DEBUG)"\n')
        r = subprocess.run([self.shell, "--norc", "--noprofile", "-c", script],
                           cwd=self.home, env=self.env, capture_output=True, text=True)
        self.assertIn("cloudctx_debug", extract(r.stdout) or "", msg=r.stderr)

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


@unittest.skipUnless(shutil.which("bash"), "bash not installed")
class TestShimBash(TestShimZsh):
    shell = "bash"
    rcflag = "--norc"
    pvar = "PS1"

    def run_script(self, body):
        script = f"source {ROOT}/shell/cloudctx.{self.shell}\n" + textwrap.dedent(body)
        r = subprocess.run([self.shell, "--norc", "--noprofile", "-c", script],
                           cwd=self.home, env=self.env, capture_output=True, text=True)
        return r


if __name__ == "__main__":
    unittest.main()
