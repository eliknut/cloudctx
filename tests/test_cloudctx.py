"""Unit tests for the cloudctx CLI.

Run with: python3 -m unittest discover -s tests -v

The CLI lives in an extensionless executable file `cloudctx`, so we load it
as a module via importlib. Every test points the registry at a throwaway temp
dir through $CLOUDCTX_HOME so nothing touches the real ~/.cloudctx.
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from importlib.machinery import SourceFileLoader
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = str(ROOT / "cloudctx")


def write_stub(directory, name, body):
    """Drop an executable shell stub (e.g. a fake `az`) into `directory`."""
    p = Path(directory) / name
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return p


def git_cmd(cwd, *args):
    """Run git hermetically (no signing, fixed identity) in `cwd`."""
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main", *args],
        cwd=str(cwd), check=True, capture_output=True, text=True)


def trust(cc, path):
    """Record a hand-written binding as trusted, as `cloudctx bind` would."""
    cc.trust_binding(Path(path))
    return Path(path)


def load():
    # The CLI file has no .py extension, so spec_from_file_location can't infer
    # a loader. Supply a SourceFileLoader explicitly.
    loader = SourceFileLoader("cloudctx", str(ROOT / "cloudctx"))
    spec = importlib.util.spec_from_loader("cloudctx", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cloudctx-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._saved_env = dict(os.environ)
        os.environ["CLOUDCTX_HOME"] = self.tmp
        # isolate the dynamic-profiles target too
        os.environ["CLOUDCTX_PROFILES"] = os.path.join(self.tmp, "profiles.json")
        os.environ.pop("CLOUDCTX_CONTEXT", None)
        self.cc = load()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved_env)

    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = self.cc.main(list(args))
        return code, out.getvalue()


class TestList(Base):
    def test_list_empty(self):
        code, out = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn("no contexts", out.lower())


class TestListVerboseShowNames(Base):
    def setUp(self):
        super().setUp()
        self.run_cli("new", "acme", "--display", "Acme AB",
                     "--azure-tenant", "t-1", "--azure-subscription", "Prod Sub",
                     "--no-login")
        self.run_cli("new", "globex", "--aws-profile", "globex-ops", "--no-login")

    def test_list_verbose_columns(self):
        code, out = self.run_cli("list", "-v")
        self.assertEqual(code, 0)
        self.assertIn("Acme AB", out)
        self.assertIn("Prod Sub", out)
        self.assertIn("globex-ops", out)

    def test_list_plain_output_unchanged(self):
        code, out = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertNotIn("Acme AB", out)  # plain list stays terse

    def test_show_prints_entry_and_paths(self):
        code, out = self.run_cli("show", "acme")
        self.assertEqual(code, 0)
        self.assertIn("display = Acme AB", out)
        self.assertIn("azure_subscription = Prod Sub", out)
        self.assertIn(str(self.cc.azure_dir("acme")), out)

    def test_show_unknown_context(self):
        code, out = self.run_cli("show", "ghost")
        self.assertNotEqual(code, 0)

    def test_names_prints_bare_names(self):
        code, out = self.run_cli("_names")
        self.assertEqual(code, 0)
        self.assertEqual(out.split(), ["acme", "globex"])


class TestRegistry(Base):
    def test_roundtrip(self):
        data = {"acme": {"display": "Acme AB", "color": "#c0392b",
                         "azure_tenant": "t-1", "azure_subscription": "Prod Sub"}}
        self.cc.save_registry(data)
        self.assertEqual(self.cc.load_registry(), data)

    def test_parse_with_comments(self):
        text = '# top comment\n\n[acme]\ndisplay = "Acme AB"  # trailing comment\ncolor = "#c0392b"\n'
        self.assertEqual(
            self.cc._parse_toml(text),
            {"acme": {"display": "Acme AB", "color": "#c0392b"}},
        )

    def test_missing_file(self):
        self.assertEqual(self.cc.load_registry(), {})

    def test_value_with_special_chars_roundtrips(self):
        # values containing spaces, '#', quotes and backslashes survive
        data = {"x": {"display": 'A "quoted" #1 \\ name'}}
        self.cc.save_registry(data)
        self.assertEqual(self.cc.load_registry(), data)

    def test_hash_inside_quotes_not_treated_as_comment(self):
        text = '[a]\ncolor = "#ff0000"\n'
        self.assertEqual(self.cc._parse_toml(text), {"a": {"color": "#ff0000"}})

    def test_header_with_inline_comment(self):
        # a hand-edited table header carrying a trailing comment must still
        # parse (tomllib accepts `[acme]  # note`); the fallback must match it
        # rather than silently dropping the whole table.
        text = '[acme]  # prod account\ndisplay = "Acme AB"\n'
        self.assertEqual(self.cc._parse_toml(text),
                         {"acme": {"display": "Acme AB"}})


class TestNew(Base):
    def test_new_scaffolds_dirs_and_registry(self):
        code, _ = self.run_cli(
            "new", "acme", "--display", "Acme AB", "--color", "#c0392b",
            "--azure-tenant", "t-1", "--azure-subscription", "Prod", "--no-login",
        )
        self.assertEqual(code, 0)
        self.assertTrue(self.cc.azure_dir("acme").is_dir())
        self.assertTrue(self.cc.context_dir("acme").joinpath("aws").is_dir())
        reg = self.cc.load_registry()
        self.assertEqual(reg["acme"]["display"], "Acme AB")
        self.assertEqual(reg["acme"]["azure_tenant"], "t-1")

    def test_new_rejects_duplicate(self):
        self.run_cli("new", "acme", "--no-login")
        code, out = self.run_cli("new", "acme", "--no-login")
        self.assertNotEqual(code, 0)
        self.assertIn("exists", out.lower())

    def test_new_rejects_invalid_name(self):
        code, out = self.run_cli("new", "a b", "--no-login")
        self.assertNotEqual(code, 0)

    def test_list_marks_active(self):
        self.run_cli("new", "acme", "--no-login")
        self.run_cli("new", "globex", "--no-login")
        os.environ["CLOUDCTX_CONTEXT"] = "globex"
        code, out = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn("acme", out)
        self.assertIn("globex", out)
        # the active line is the one marked with *
        active_line = [ln for ln in out.splitlines() if "globex" in ln][0]
        self.assertIn("*", active_line)


class TestEnv(Base):
    def test_azure_only_unsets_aws_vars(self):
        self.run_cli("new", "acme", "--display", "Acme AB",
                     "--azure-tenant", "t", "--azure-subscription", "Prod", "--no-login")
        code, out = self.run_cli("_env", "acme")
        self.assertEqual(code, 0)
        self.assertIn("export AZURE_CONFIG_DIR=", out)
        self.assertIn("export CLOUDCTX_CONTEXT='acme'", out)
        self.assertIn("export CLOUDCTX_AZURE_LABEL='Prod'", out)
        # AWS vars must be explicitly UNSET (not merely absent) so a context
        # switch fully replaces the previous context's environment.
        self.assertNotIn("export AWS_PROFILE", out)
        self.assertIn("unset AWS_PROFILE", out)
        self.assertIn("unset AWS_CONFIG_FILE", out)
        self.assertIn(str(self.cc.azure_dir("acme").resolve()), out)

    def test_env_unsets_label_when_no_subscription(self):
        self.run_cli("new", "bare", "--azure-tenant", "t", "--no-login")
        _, out = self.run_cli("_env", "bare")
        self.assertNotIn("export CLOUDCTX_AZURE_LABEL", out)
        self.assertIn("unset CLOUDCTX_AZURE_LABEL", out)

    def test_aws_vars_when_present(self):
        self.run_cli("new", "globex", "--aws-profile", "globex", "--no-login")
        code, out = self.run_cli("_env", "globex")
        self.assertIn("export AWS_PROFILE='globex'", out)
        # assert the VALUES point into globex's per-context store, not merely
        # that the export lines exist — a regression pointing these at the
        # global ~/.aws files is the exact isolation failure this tool prevents.
        self.assertIn(str(self.cc.aws_config("globex").resolve()), out)
        self.assertIn(str(self.cc.aws_credentials("globex").resolve()), out)

    def test_aws_profile_defaults_to_name(self):
        # an aws field present but no explicit aws_profile -> profile defaults to ctx name
        self.run_cli("new", " initech".strip(), "--aws-account-id", "123", "--no-login")
        code, out = self.run_cli("_env", "initech")
        self.assertIn("export AWS_PROFILE='initech'", out)

    def test_injection_safe(self):
        self.run_cli("new", "x", "--azure-subscription", "a'; rm -rf $HOME; '", "--no-login")
        code, out = self.run_cli("_env", "x")
        # the dangerous single quote must be neutralized via the '\'' idiom,
        # so no bare `; rm -rf` escapes the quoting.
        self.assertIn("'\\''", out)
        self.assertNotIn("='a'; rm", out)

    def test_unknown_context_errors_with_empty_stdout(self):
        # stdout must stay empty so `eval "$(... )"` is a safe no-op
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.cc.main(["_env", "nope"])
        self.assertNotEqual(code, 0)
        self.assertEqual(out.getvalue().strip(), "")

    def test_clear(self):
        code, out = self.run_cli("_env", "--clear")
        self.assertEqual(code, 0)
        # every managed var must be unset, so `ctx clear` leaves no stale
        # isolation env behind; drive the assertion off the constant so the
        # test stays in lockstep with CLEARABLE_VARS.
        for var in self.cc.CLEARABLE_VARS:
            self.assertIn(f"unset {var}", out)


class TestArmEnv(Base):
    TENANT = "11111111-2222-3333-4444-555555555555"
    SUB = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def test_guid_tenant_exports_arm_tenant_id(self):
        self.run_cli("new", "acme", "--azure-tenant", self.TENANT, "--no-login")
        _, out = self.run_cli("_env", "acme")
        self.assertIn(f"export ARM_TENANT_ID='{self.TENANT}'", out)

    def test_domain_tenant_does_not_export_arm_tenant_id(self):
        self.run_cli("new", "acme", "--azure-tenant", "contoso.onmicrosoft.com", "--no-login")
        _, out = self.run_cli("_env", "acme")
        self.assertNotIn("export ARM_TENANT_ID", out)
        self.assertIn("unset ARM_TENANT_ID", out)

    def test_guid_subscription_exports_arm_subscription_id(self):
        self.run_cli("new", "acme", "--azure-subscription", self.SUB, "--no-login")
        _, out = self.run_cli("_env", "acme")
        self.assertIn(f"export ARM_SUBSCRIPTION_ID='{self.SUB}'", out)

    def test_named_subscription_does_not_export_arm_subscription_id(self):
        self.run_cli("new", "acme", "--azure-subscription", "Prod", "--no-login")
        _, out = self.run_cli("_env", "acme")
        self.assertNotIn("export ARM_SUBSCRIPTION_ID", out)
        self.assertIn("unset ARM_SUBSCRIPTION_ID", out)

    def test_exec_strips_inherited_arm_subscription_id(self):
        # The cross-customer leak case: an ARM_SUBSCRIPTION_ID left over from
        # another context's window must not reach a context that pins none.
        self.run_cli("new", "acme", "--azure-tenant", self.TENANT, "--no-login")
        env = dict(os.environ)
        env["CLOUDCTX_HOME"] = self.tmp
        env["ARM_SUBSCRIPTION_ID"] = "stale-other-customer"
        r = subprocess.run([CLI, "exec", "acme", "--", "sh", "-c",
                            'printf "S=%s T=%s" "$ARM_SUBSCRIPTION_ID" "$ARM_TENANT_ID"'],
                           env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(r.stdout, f"S= T={self.TENANT}")

    def test_clear_unsets_arm_vars(self):
        _, out = self.run_cli("_env", "--clear")
        self.assertIn("unset ARM_TENANT_ID", out)
        self.assertIn("unset ARM_SUBSCRIPTION_ID", out)


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
        try:
            trust(self.cc, p)
        except self.cc.BindingError:
            pass  # unparsable on purpose: rejected before the trust check
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
        trust(self.cc, p)
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
        trust(self.cc, p)
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
        trust(self.cc, self.dir / ".cloudctx")

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


class TestBindingTrust(Base):
    PIN = "99999999-8888-7777-6666-555555555555"
    REG_SUB = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def setUp(self):
        super().setUp()
        self.addCleanup(os.chdir, os.getcwd())
        self.run_cli("new", "acme", "--azure-tenant", "t",
                     "--azure-subscription", self.REG_SUB, "--no-login")
        self.run_cli("new", "globex", "--azure-tenant", "t2", "--no-login")
        self.base = Path(tempfile.mkdtemp(prefix="cctx-trust-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.a = self.base / "a"
        self.b = self.base / "b"
        self.a.mkdir()
        self.b.mkdir()

    def bind_in(self, folder, *args):
        os.chdir(folder)
        code, out = self.run_cli("bind", *args)
        self.assertEqual(code, 0, out)
        return folder / ".cloudctx"

    def load_quiet(self, folder):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            found = self.cc.load_binding_for(folder)
        return found, err.getvalue()

    def assert_untrusted(self, folder):
        found, err = self.load_quiet(folder)
        self.assertIsNone(found)
        self.assertEqual(len(err.splitlines()), 1, err)
        self.assertIn("untrusted", err)

    def test_bound_file_is_trusted(self):
        if not shutil.which("git"):
            self.skipTest("git not installed")
        git_cmd(self.a, "init", "-q")
        p = self.bind_in(self.a, "acme")
        found, err = self.load_quiet(self.a)
        self.assertEqual(found, (p, {"context": "acme", "subscription": None}))
        self.assertEqual(err, "")

    def test_hand_written_file_is_ignored(self):
        (self.a / ".cloudctx").write_text('context = "acme"\n')
        self.assert_untrusted(self.a)

    def test_edited_file_is_ignored(self):
        p = self.bind_in(self.a, "acme")
        p.write_text('context = "globex"\n')
        self.assert_untrusted(self.a)

    def test_copied_file_is_ignored(self):
        p = self.bind_in(self.a, "acme")
        shutil.copy(str(p), str(self.b / ".cloudctx"))
        self.assert_untrusted(self.b)
        self.assertIsNotNone(self.load_quiet(self.a)[0])

    def test_symlinked_file_is_ignored(self):
        p = self.bind_in(self.a, "acme")
        # A hard link keeps bytes, dev and ino identical, so only the
        # symlink rule can reject this.
        elsewhere = self.b / "target"
        os.link(str(p), str(elsewhere))
        p.unlink()
        p.symlink_to(elsewhere)
        self.assert_untrusted(self.a)

    def test_path_reuse_is_ignored(self):
        p = self.bind_in(self.a, "acme")
        text = p.read_text()
        os.chdir(self.base)
        shutil.rmtree(str(self.a))
        self.a.mkdir()
        (self.a / ".cloudctx").write_text(text)
        self.assert_untrusted(self.a)
        self.bind_in(self.a, "acme")
        self.assertIsNotNone(self.load_quiet(self.a)[0])

    def test_recreated_context_with_other_tenant_is_ignored(self):
        self.bind_in(self.a, "acme")
        reg = self.cc.load_registry()
        reg["acme"]["azure_tenant"] = "other-tenant"
        self.cc.save_registry(reg)
        self.assert_untrusted(self.a)
        self.bind_in(self.a, "acme")
        self.assertIsNotNone(self.load_quiet(self.a)[0])

    def test_old_format_record_is_ignored(self):
        p = self.bind_in(self.a, "acme")
        key = self.cc.binding_key(p)
        self.cc.trust_path().write_text(
            json.dumps({key: self.cc.binding_digest(p)}))
        self.assertEqual(self.cc.load_trusted(), {})
        self.assert_untrusted(self.a)

    def test_unbind_reports_unwritable_trust_file(self):
        # A 0500 CLOUDCTX_HOME is not enough: the writer re-chmods it 0700.
        self.bind_in(self.a, "acme")
        def denied(_data):
            raise PermissionError(13, "Permission denied", str(self.cc.trust_path()))
        with unittest.mock.patch.object(self.cc, "_save_trusted", denied):
            code, out = self.run_cli("unbind")
        self.assertEqual(code, 1)
        self.assertIn("cannot update", out)
        self.assertNotIn("Traceback", out)

    def test_unbind_removes_trust(self):
        p = self.bind_in(self.a, "acme")
        text = p.read_text()
        code, out = self.run_cli("unbind")
        self.assertEqual(code, 0, out)
        p.write_text(text)
        self.assert_untrusted(self.a)

    def test_trust_file_modes(self):
        self.bind_in(self.a, "acme")
        tp = self.cc.trust_path()
        self.assertEqual(stat.S_IMODE(tp.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(tp.parent.stat().st_mode), 0o700)
        self.assertEqual(list(tp.parent.glob("*.tmp")), [])

    def test_corrupt_trust_file_is_empty(self):
        tp = self.cc.trust_path()
        tp.parent.mkdir(parents=True, exist_ok=True)
        for text in ("{not json", "[1]", '{"k": 1}'):
            tp.write_text(text)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(self.cc.load_trusted(), {})
            self.assertNotIn("Traceback", err.getvalue())

    def test_exec_ignores_untrusted_binding(self):
        (self.a / ".cloudctx").write_text(
            f'context = "acme"\nsubscription = "{self.PIN}"\n')
        env = dict(os.environ)
        env["CLOUDCTX_HOME"] = self.tmp
        r = subprocess.run([CLI, "exec", "acme", "--", "sh", "-c",
                            'printf %s "$ARM_SUBSCRIPTION_ID"'],
                           cwd=str(self.a), env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, self.REG_SUB)
        self.assertIn("untrusted", r.stderr)


class TestDecorate(Base):
    def setUp(self):
        super().setUp()
        os.environ["TERM_PROGRAM"] = "iTerm.app"   # OSC 1337 is iTerm-only
        os.environ.pop("LC_TERMINAL", None)
        self.run_cli("new", "acme", "--display", "Acme AB", "--color", "#c0392b",
                     "--azure-tenant", "t", "--no-login")

    def test_badge_is_base64_of_display(self):
        import base64
        code, out = self.run_cli("_decorate", "acme")
        self.assertEqual(code, 0)
        b64 = base64.b64encode("Acme AB".encode()).decode()
        self.assertIn("\x1b]1337;SetBadgeFormat=" + b64 + "\x07", out)

    def test_title_sequence(self):
        _, out = self.run_cli("_decorate", "acme")
        self.assertIn("\x1b]0;Acme AB\x07", out)

    def test_tab_color_is_packed_hex_no_hash(self):
        _, out = self.run_cli("_decorate", "acme")
        self.assertIn("\x1b]1337;SetColors=tab=c0392b\x07", out)

    def test_clear_resets_tab_color(self):
        _, out = self.run_cli("_decorate", "--clear")
        self.assertIn("SetColors=tab=default", out)
        # badge cleared (empty payload)
        self.assertIn("\x1b]1337;SetBadgeFormat=\x07", out)

    def test_hex_to_rgb255(self):
        self.assertEqual(self.cc.hex_to_rgb255("#c0392b"), (192, 57, 43))
        self.assertEqual(self.cc.hex_to_rgb255("c0392b"), (192, 57, 43))
        self.assertEqual(self.cc.hex_to_rgb255("#f00"), (255, 0, 0))

    def test_rgb_floats(self):
        r, g, b = self.cc.rgb_floats("#ff0000")
        self.assertAlmostEqual(r, 1.0)
        self.assertAlmostEqual(g, 0.0)
        self.assertAlmostEqual(b, 0.0)

    def test_no_color_skips_tab_color(self):
        self.run_cli("new", "nocolor", "--display", "No Color", "--no-login")
        _, out = self.run_cli("_decorate", "nocolor")
        self.assertNotIn("SetColors=tab=", out)
        self.assertIn("SetBadgeFormat=", out)  # badge still set


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


class TestExecStatus(Base):
    def _run_bin(self, *args, extra_path=None):
        env = dict(os.environ)
        env["CLOUDCTX_HOME"] = self.tmp
        if extra_path:
            env["PATH"] = extra_path + os.pathsep + env.get("PATH", "")
        return subprocess.run([CLI, *args], env=env, capture_output=True, text=True)

    def test_exec_sets_context_env(self):
        self.run_cli("new", "acme", "--azure-tenant", "t", "--no-login")
        r = self._run_bin("exec", "acme", "--", "printenv", "AZURE_CONFIG_DIR")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn(os.path.join("acme", "azure"), r.stdout)

    def test_exec_propagates_exit_code(self):
        self.run_cli("new", "acme", "--no-login")
        r = self._run_bin("exec", "acme", "--", "sh", "-c", "exit 7")
        self.assertEqual(r.returncode, 7)

    def test_exec_unknown_context(self):
        r = self._run_bin("exec", "nope", "--", "true")
        self.assertNotEqual(r.returncode, 0)

    def test_status_reports_azure_subscription(self):
        self.run_cli("new", "acme", "--azure-tenant", "t", "--no-login")
        stubdir = tempfile.mkdtemp(prefix="stub-")
        self.addCleanup(shutil.rmtree, stubdir, ignore_errors=True)
        write_stub(stubdir, "az",
                   'echo \'{"name":"Prod Sub","id":"sub-123",'
                   '"tenantId":"t-9","user":{"name":"me@example.com"}}\'')
        r = self._run_bin("status", extra_path=stubdir)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("Prod Sub", r.stdout)
        self.assertIn("me@example.com", r.stdout)


class TestProbe(Base):
    """_probe_json: the timeout-guarded identity probe used by status/login."""

    def test_probe_ok(self):
        status, data = self.cc._probe_json(
            ["sh", "-c", 'echo \'{"name": "x"}\''])
        self.assertEqual(status, "ok")
        self.assertEqual(data, {"name": "x"})

    def test_probe_error_on_nonzero_exit(self):
        status, data = self.cc._probe_json(["sh", "-c", "exit 1"])
        self.assertEqual((status, data), ("error", None))

    def test_probe_timeout(self):
        status, data = self.cc._probe_json(["sleep", "5"], timeout=0.2)
        self.assertEqual((status, data), ("timeout", None))

    def test_probe_unparsable_output(self):
        status, data = self.cc._probe_json(["sh", "-c", "echo not-json"])
        self.assertEqual((status, data), ("error", None))

    def test_status_reports_timeout_not_hang(self):
        # Review 2026-07-06 #4: a hanging az must surface as a message, fast.
        self.run_cli("new", "acme", "--azure-tenant", "t", "--no-login")
        stubdir = tempfile.mkdtemp(prefix="stub-")
        self.addCleanup(shutil.rmtree, stubdir, ignore_errors=True)
        write_stub(stubdir, "az", "sleep 30")
        write_stub(stubdir, "aws", "exit 1")
        env = dict(os.environ)
        env["CLOUDCTX_HOME"] = self.tmp
        env["CLOUDCTX_PROBE_TIMEOUT"] = "0.2"
        env["PATH"] = stubdir + os.pathsep + env["PATH"]
        r = subprocess.run([CLI, "status"], env=env, capture_output=True,
                           text=True, timeout=10)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("timed out", r.stdout)


class TestLogin(Base):
    def test_azure_login_cmds(self):
        entry = {"azure_tenant": "t", "azure_subscription": "s"}
        self.assertEqual(
            self.cc.azure_login_cmds(entry),
            [["az", "login", "--tenant", "t"],
             ["az", "account", "set", "--subscription", "s"]],
        )

    def test_azure_login_cmds_device_code(self):
        entry = {"azure_tenant": "t"}
        cmds = self.cc.azure_login_cmds(entry, device_code=True)
        self.assertEqual(cmds[0], ["az", "login", "--tenant", "t", "--use-device-code"])

    def test_login_dry_run_shows_commands_and_aws_stub(self):
        self.run_cli("new", "acme", "--azure-tenant", "t",
                     "--azure-subscription", "s", "--aws-profile", "acme", "--no-login")
        code, out = self.run_cli("login", "acme", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("az login --tenant t", out)
        self.assertIn("az account set --subscription s", out)
        self.assertIn(str(self.cc.azure_dir("acme").resolve()), out)
        self.assertIn("not yet", out.lower())  # AWS stub

    def test_login_unknown_context(self):
        code, out = self.run_cli("login", "ghost", "--dry-run")
        self.assertNotEqual(code, 0)

    GUID = "9fc151d1-62b8-402f-b07e-91533ff07e0d"

    def _login_with_stub_az(self, registry_tenant, landed_tenant):
        self.run_cli("new", "acme", "--azure-tenant", registry_tenant,
                     "--no-login")
        stubdir = tempfile.mkdtemp(prefix="stub-")
        self.addCleanup(shutil.rmtree, stubdir, ignore_errors=True)
        write_stub(
            stubdir, "az",
            'case "$1 $2" in\n'
            '"account show") echo \'{"name":"Sub","id":"s-1",'
            f'"tenantId":"{landed_tenant}"}}\' ;;\n'
            '*) exit 0 ;;\n'
            'esac')
        env = dict(os.environ)
        env["CLOUDCTX_HOME"] = self.tmp
        env["PATH"] = stubdir + os.pathsep + env["PATH"]
        return subprocess.run([CLI, "login", "acme"], env=env,
                              capture_output=True, text=True)

    def test_login_verifies_matching_tenant_quietly(self):
        r = self._login_with_stub_az(self.GUID, self.GUID)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("logged in:", r.stdout)
        self.assertNotIn("MISMATCH", r.stderr)

    def test_login_warns_on_guid_tenant_mismatch(self):
        other = "00000000-0000-0000-0000-000000000000"
        r = self._login_with_stub_az(self.GUID, other)
        self.assertEqual(r.returncode, 0, msg=r.stderr)  # login itself succeeded
        self.assertIn("MISMATCH", r.stderr)

    def test_login_skips_comparison_for_domain_tenant(self):
        # domain-form tenants can't be compared to the landed GUID — no warning
        r = self._login_with_stub_az("contoso.onmicrosoft.com", self.GUID)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertNotIn("MISMATCH", r.stderr)


class TestOpen(Base):
    def test_applescript_escape(self):
        self.assertEqual(self.cc.applescript_escape('a"b\\c'), 'a\\"b\\\\c')

    def test_open_argv_new_window(self):
        argv = self.cc.open_osascript_argv("cloudctx use acme", tab=False)
        self.assertEqual(argv[0], "osascript")
        self.assertIn("set _w to (create window with default profile)", argv)
        self.assertIn('write text "cloudctx use acme"', argv)

    def test_open_argv_new_tab(self):
        argv = self.cc.open_osascript_argv("cloudctx use acme", tab=True)
        self.assertIn("set _t to (create tab with default profile)", argv)

    def test_build_open_command_cd_and_claude(self):
        cmd = self.cc.build_open_command("acme", cd="/x", claude=True)
        self.assertEqual(cmd, "cloudctx use acme && cd '/x' && claude")

    def test_open_dry_run(self):
        self.run_cli("new", "acme", "--no-login")
        code, out = self.run_cli("open", "acme", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn('write text "cloudctx use acme"', out)

    def test_claude_dry_run_includes_claude(self):
        self.run_cli("new", "acme", "--no-login")
        code, out = self.run_cli("claude", "acme", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("&& claude", out)


class TestProfiles(Base):
    def test_build_profiles_shape(self):
        prof = self.cc.build_profiles(
            {"acme": {"display": "Acme AB", "color": "#c0392b"}})
        self.assertEqual(len(prof["Profiles"]), 1)
        p = prof["Profiles"][0]
        self.assertEqual(p["Guid"], "cloudctx-acme")
        self.assertEqual(p["Initial Text"], "cloudctx use acme")
        self.assertTrue(p["Use Tab Color"])
        self.assertAlmostEqual(p["Tab Color"]["Red Component"], 192 / 255)
        self.assertEqual(p["Tab Color"]["Color Space"], "sRGB")

    def test_no_color_omits_tab_color(self):
        p = self.cc.build_profiles({"x": {"display": "X"}})["Profiles"][0]
        self.assertNotIn("Use Tab Color", p)

    def test_gen_profiles_writes_valid_json(self):
        import json
        self.run_cli("new", "acme", "--color", "#c0392b", "--no-login", "--no-profiles")
        code, _ = self.run_cli("gen-profiles")
        self.assertEqual(code, 0)
        path = self.cc.profiles_path()
        data = json.loads(Path(path).read_text())
        self.assertEqual(data["Profiles"][0]["Guid"], "cloudctx-acme")

    def test_new_regenerates_profiles(self):
        import json
        self.run_cli("new", "acme", "--color", "#2980b9", "--no-login")
        data = json.loads(Path(self.cc.profiles_path()).read_text())
        self.assertEqual(len(data["Profiles"]), 1)


class TestReviewFixes(Base):
    """Regression tests for issues found by the adversarial review."""

    def test_exec_strips_inherited_managed_vars(self):
        # Finding #4: exec into an Azure-only context must not leak a caller's AWS_PROFILE.
        self.run_cli("new", "acme", "--azure-tenant", "t", "--no-login")
        env = dict(os.environ)
        env["CLOUDCTX_HOME"] = self.tmp
        env["AWS_PROFILE"] = "stale-leak"
        env["AWS_CONFIG_FILE"] = "/tmp/stale/config"
        r = subprocess.run([CLI, "exec", "acme", "--", "sh", "-c",
                            "echo P=$AWS_PROFILE C=$AWS_CONFIG_FILE"],
                           env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("P= ", r.stdout + " ")  # AWS_PROFILE empty in child
        self.assertNotIn("stale-leak", r.stdout)

    def test_load_registry_drops_traversal_names(self):
        # Finding #1: a hand-edited registry with a path-traversal table name
        # must not become a usable context. Quoted-key form, which is valid
        # TOML — both tomllib and the fallback parse it, then the name filter
        # must drop it.
        Path(self.cc.registry_path()).parent.mkdir(parents=True, exist_ok=True)
        Path(self.cc.registry_path()).write_text(
            '[acme]\ndisplay = "Acme"\n\n["../../evil"]\ndisplay = "Evil"\n')
        reg = self.cc.load_registry()
        self.assertIn("acme", reg)
        self.assertNotIn("../../evil", reg)
        self.assertNotIn('"../../evil"', reg)

    def test_unparsable_registry_fails_cleanly(self):
        # 3.13 regression (this branch): a bare `[../../evil]` header is
        # INVALID TOML — tomllib must produce a clean error, never a traceback
        # (and never an empty registry, which a later save would persist).
        # The 3.9 fallback is lenient and drops the bad table instead.
        Path(self.cc.registry_path()).parent.mkdir(parents=True, exist_ok=True)
        Path(self.cc.registry_path()).write_text(
            '[acme]\ndisplay = "Acme"\n\n[../../evil]\ndisplay = "Evil"\n')
        env = dict(os.environ)
        env["CLOUDCTX_HOME"] = self.tmp
        # run under THIS interpreter so the tomllib branch below matches the
        # parser the subprocess actually used (the shebang may be older).
        r = subprocess.run([sys.executable, CLI, "list"], env=env,
                           capture_output=True, text=True)
        self.assertNotIn("Traceback", r.stderr)
        if self.cc._tomllib is not None:
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("could not parse", r.stderr)
        else:
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("acme", r.stdout)

    def test_control_chars_in_values_roundtrip(self):
        # 3.13 regression (this branch): _escape must encode BEL/ESC etc. as
        # \uXXXX — raw control chars are invalid TOML and locked tomllib out
        # of the registry entirely.
        data = {"x": {"display": "Acme\x07\x1b]0;HIJACK\x07"}}
        self.cc.save_registry(data)
        self.assertEqual(self.cc.load_registry(), data)

    def test_env_rejects_traversal_name(self):
        code, out = self.run_cli("_env", "../../evil")
        self.assertNotEqual(code, 0)

    def test_toml_roundtrips_newline_value(self):
        # Findings #5/#6: a newline in a value must survive (not truncate/crash).
        data = {"x": {"display": "line1\nline2\twith tab"}}
        self.cc.save_registry(data)
        self.assertEqual(self.cc.load_registry(), data)

    def test_new_rejects_invalid_color(self):
        # Finding #12: invalid --color should fail fast, not silently drop later.
        code, out = self.run_cli("new", "acme", "--color", "not-a-color", "--no-login")
        self.assertNotEqual(code, 0)
        self.assertIn("color", out.lower())

    def test_decorate_sanitizes_control_chars_in_title(self):
        # Finding #10: control chars in display must not inject a second OSC.
        # The text may survive as plain title text; what must NOT survive is a
        # raw ESC/BEL inside the title payload (that's what enables injection).
        self.run_cli("new", "acme", "--display", "Acme\x07\x1b]0;HIJACK\x07", "--no-login")
        _, out = self.run_cli("_decorate", "acme")
        payload = out.split("\x1b]0;", 1)[1].split("\x07", 1)[0]
        self.assertNotIn("\x1b", payload)
        self.assertNotIn("\x07", payload)

    def test_decorate_no_name_usage(self):
        # Finding #16: clearer message than "unknown context None".
        code, out = self.run_cli("_decorate")
        self.assertNotEqual(code, 0)
        self.assertNotIn("None", out)

    def test_status_tolerates_non_object_json(self):
        # Finding #11: aws returning a JSON array must not crash status.
        self.run_cli("new", "acme", "--azure-tenant", "t", "--no-login")
        stubdir = tempfile.mkdtemp(prefix="stub-")
        self.addCleanup(shutil.rmtree, stubdir, ignore_errors=True)
        write_stub(stubdir, "aws", "echo '[1,2,3]'")
        write_stub(stubdir, "az", "exit 1")  # az "not logged in"
        env = dict(os.environ)
        env["CLOUDCTX_HOME"] = self.tmp
        env["PATH"] = stubdir + os.pathsep + env["PATH"]
        r = subprocess.run([CLI, "status"], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_load_registry_drops_non_string_values(self):
        # Review 2026-07-06 #2: a hand-edited `flag = true` parses to a bool
        # under tomllib (3.11+) and must never reach shquote/encode — and must
        # not be coerced (bool True must not become subscription "True").
        Path(self.cc.registry_path()).parent.mkdir(parents=True, exist_ok=True)
        Path(self.cc.registry_path()).write_text(
            '[acme]\ndisplay = "Acme"\nflag = true\ncount = 3\n')
        reg = self.cc.load_registry()
        for entry in reg.values():
            for v in entry.values():
                self.assertIsInstance(v, str)
        # _env must exit cleanly on such a context, not traceback.
        code, out = self.run_cli("_env", "acme")
        self.assertEqual(code, 0)
        self.assertIn("export CLOUDCTX_CONTEXT='acme'", out)

    def test_permissions_locked_down(self):
        # Finding #9: registry root 0700, contexts.toml 0600, stores 0700.
        self.run_cli("new", "acme", "--no-login")
        self.assertEqual(stat.S_IMODE(os.stat(self.cc.registry_path()).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.cc.REGISTRY_ROOT()).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(self.cc.azure_dir("acme")).st_mode), 0o700)


class TestSelfUpdate(Base):
    """self-update against a local fixture remote — hermetic, no network."""

    def _git(self, cwd, *args):
        return subprocess.run(
            ["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t",
             *args], capture_output=True, text=True, check=True)

    def _fixture(self):
        """A 'remote' repo holding the real CLI file, and an install clone."""
        remote = Path(tempfile.mkdtemp(prefix="cctx-remote-"))
        self.addCleanup(shutil.rmtree, remote, ignore_errors=True)
        subprocess.run(["git", "init", "-q", str(remote)],
                       check=True, capture_output=True)
        shutil.copy(CLI, remote / "cloudctx")
        self._git(remote, "add", "-A")
        self._git(remote, "commit", "-qm", "initial")
        parent = Path(tempfile.mkdtemp(prefix="cctx-install-"))
        self.addCleanup(shutil.rmtree, parent, ignore_errors=True)
        install = parent / "clone"
        subprocess.run(["git", "clone", "-q", str(remote), str(install)],
                       check=True, capture_output=True)
        self.cc.script_dir = lambda: install
        return remote, install

    def _bump_remote(self, remote, version):
        text = (remote / "cloudctx").read_text()
        text = text.replace(f'__version__ = "{self.cc.__version__}"',
                            f'__version__ = "{version}"')
        (remote / "cloudctx").write_text(text)
        self._git(remote, "commit", "-aqm", f"bump {version}")

    def test_check_reports_update_available(self):
        remote, install = self._fixture()
        self._git(remote, "tag", "v9.9.9")
        code, out = self.run_cli("self-update", "--check")
        self.assertEqual(code, 0)
        self.assertIn("update available", out)
        self.assertIn("9.9.9", out)

    def test_check_reports_up_to_date(self):
        remote, install = self._fixture()
        self._git(remote, "tag", f"v{self.cc.__version__}")
        code, out = self.run_cli("self-update", "--check")
        self.assertEqual(code, 0)
        self.assertIn("up to date", out)

    def test_update_pulls_and_reports_versions(self):
        remote, install = self._fixture()
        self._bump_remote(remote, "9.9.9")
        code, out = self.run_cli("self-update")
        self.assertEqual(code, 0, msg=out)
        self.assertIn("updated:", out)
        self.assertIn("9.9.9", out)
        self.assertIn("new shell", out)
        self.assertIn('__version__ = "9.9.9"',
                      (install / "cloudctx").read_text())

    def test_update_already_up_to_date(self):
        remote, install = self._fixture()
        code, out = self.run_cli("self-update")
        self.assertEqual(code, 0, msg=out)
        self.assertIn("already up to date", out)

    def test_update_refuses_dirty_tree(self):
        remote, install = self._fixture()
        self._bump_remote(remote, "9.9.9")
        with open(install / "cloudctx", "a") as f:
            f.write("# local edit\n")
        code, out = self.run_cli("self-update")
        self.assertNotEqual(code, 0)
        self.assertIn("local changes", out)
        # nothing was pulled
        self.assertNotIn('__version__ = "9.9.9"',
                         (install / "cloudctx").read_text())

    def test_update_refuses_non_clone(self):
        plain = Path(tempfile.mkdtemp(prefix="cctx-plain-"))
        self.addCleanup(shutil.rmtree, plain, ignore_errors=True)
        self.cc.script_dir = lambda: plain
        code, out = self.run_cli("self-update")
        self.assertNotEqual(code, 0)
        self.assertIn("not a git clone", out)


class _TtyOut(io.StringIO):
    def isatty(self):
        return True


class TestUpdateNotice(Base):
    """Passive 'new version available' notice — cached, stderr, TTY-only."""

    def _state(self, latest, checked_at=None):
        import time
        root = self.cc.REGISTRY_ROOT()
        root.mkdir(parents=True, exist_ok=True)
        self.cc._update_state_path().write_text(json.dumps({
            "checked_at": int(time.time()) if checked_at is None else checked_at,
            "latest": latest}))

    def setUp(self):
        super().setUp()
        # never spawn a real background refresh from tests
        self.spawned = []
        self.cc._spawn_update_refresh = lambda: self.spawned.append(True)

    def test_notice_when_cached_newer(self):
        self._state("9.9.9")
        s = _TtyOut()
        self.cc._maybe_notify_update(stream=s)
        self.assertIn("new version available", s.getvalue())
        self.assertIn("9.9.9", s.getvalue())
        self.assertIn("cloudctx self-update", s.getvalue())

    def test_silent_when_current(self):
        self._state(self.cc.__version__)
        s = _TtyOut()
        self.cc._maybe_notify_update(stream=s)
        self.assertEqual(s.getvalue(), "")

    def test_silent_on_non_tty(self):
        self._state("9.9.9")
        s = io.StringIO()  # isatty() False
        self.cc._maybe_notify_update(stream=s)
        self.assertEqual(s.getvalue(), "")

    def test_silent_when_opted_out(self):
        self._state("9.9.9")
        os.environ["CLOUDCTX_NO_UPDATE_CHECK"] = "1"
        s = _TtyOut()
        self.cc._maybe_notify_update(stream=s)
        self.assertEqual(s.getvalue(), "")

    def test_machine_commands_never_notify(self):
        # _env stdout is eval'd, _decorate stdout is raw escapes: a notice on
        # those paths would be a shell-injection/garbage bug, not a nicety.
        for cmd in ("_env", "_decorate", "_names", "exec", "self-update"):
            self.assertNotIn(cmd, self.cc.NOTIFY_COMMANDS)
        for cmd in ("list", "status", "login", "new", "delete", "show"):
            self.assertIn(cmd, self.cc.NOTIFY_COMMANDS)

    def test_stale_state_triggers_background_refresh(self):
        self._state("0.0.1", checked_at=1)  # ancient
        s = _TtyOut()
        self.cc._maybe_notify_update(stream=s)
        self.assertTrue(self.spawned)

    def test_fresh_state_does_not_respawn(self):
        self._state("0.0.1")  # checked_at = now
        s = _TtyOut()
        self.cc._maybe_notify_update(stream=s)
        self.assertFalse(self.spawned)

    def test_refresh_command_writes_state(self):
        # reuse the self-update fixture: local remote with a known tag
        helper = TestSelfUpdate()
        helper.cc = self.cc
        helper.addCleanup = self.addCleanup
        helper.run_cli = self.run_cli
        remote, install = helper._fixture()
        helper._git(remote, "tag", "v9.9.9")
        code, _ = self.run_cli("_refresh-update-check")
        self.assertEqual(code, 0)
        state = json.loads(self.cc._update_state_path().read_text())
        self.assertEqual(state["latest"], "9.9.9")
        self.assertGreater(state["checked_at"], 0)


class TestInstall(Base):
    def test_install_prints_source_line(self):
        code, out = self.run_cli("install")
        self.assertEqual(code, 0)
        self.assertIn("source", out)
        self.assertIn("cloudctx.zsh", out)

    def test_install_bash_variant(self):
        code, out = self.run_cli("install", "--shell", "bash")
        self.assertIn("cloudctx.bash", out)

    def test_install_write_appends_to_rc(self):
        os.environ["HOME"] = self.tmp
        code, out = self.run_cli("install", "--shell", "zsh", "--write")
        self.assertEqual(code, 0)
        rc = Path(self.tmp) / ".zshrc"
        self.assertTrue(rc.exists())
        self.assertIn("cloudctx.zsh", rc.read_text())

    def test_install_write_is_idempotent(self):
        # Review 2026-07-06 #3: a second --write must not duplicate the hook.
        os.environ["HOME"] = self.tmp
        self.run_cli("install", "--shell", "zsh", "--write")
        code, out = self.run_cli("install", "--shell", "zsh", "--write")
        self.assertEqual(code, 0)
        self.assertIn("already", out.lower())
        rc_text = (Path(self.tmp) / ".zshrc").read_text()
        self.assertEqual(rc_text.count("cloudctx.zsh"), 1)


class TestDelete(Base):
    def setUp(self):
        super().setUp()
        self.run_cli("new", "acme", "--display", "Acme AB", "--no-login")
        self.run_cli("new", "globex", "--no-login")

    def _delete(self, *args, stdin=""):
        env = dict(os.environ)
        env["CLOUDCTX_HOME"] = self.tmp
        return subprocess.run([CLI, "delete", *args], env=env, input=stdin,
                              capture_output=True, text=True)

    def test_force_delete_removes_entry_and_store(self):
        store = self.cc.context_dir("acme")
        self.assertTrue(store.is_dir())
        r = self._delete("acme", "--force")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertNotIn("acme", self.cc.load_registry())
        self.assertFalse(store.exists())
        # profiles regenerated without the deleted context
        profiles = Path(self.cc.profiles_path()).read_text()
        self.assertNotIn("cloudctx-acme", profiles)
        self.assertIn("cloudctx-globex", profiles)

    def test_keep_store_deletes_entry_only(self):
        store = self.cc.context_dir("acme")
        r = self._delete("acme", "--force", "--keep-store")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertNotIn("acme", self.cc.load_registry())
        self.assertTrue(store.is_dir())

    def test_unknown_context_errors(self):
        r = self._delete("ghost", "--force")
        self.assertNotEqual(r.returncode, 0)

    def test_prompt_no_aborts(self):
        r = self._delete("acme", stdin="n\n")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("acme", self.cc.load_registry())
        self.assertTrue(self.cc.context_dir("acme").is_dir())

    def test_prompt_eof_aborts(self):
        r = self._delete("acme", stdin="")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("acme", self.cc.load_registry())

    def test_prompt_yes_deletes(self):
        r = self._delete("acme", stdin="y\n")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertNotIn("acme", self.cc.load_registry())

    def test_warns_when_active_in_this_shell(self):
        env = dict(os.environ)
        env["CLOUDCTX_HOME"] = self.tmp
        env["CLOUDCTX_CONTEXT"] = "acme"
        r = subprocess.run([CLI, "delete", "acme", "--force"], env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("active in this shell", r.stderr)


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
        self.assertEqual(list(self.repo.glob(".cloudctx.*tmp")), [])

    def test_bind_does_not_follow_planted_tmp_symlink(self):
        victim = self.repo / "victim.txt"
        victim.write_text("original\n")
        (self.repo / ".cloudctx.tmp").symlink_to(victim)
        code, out = self.run_cli("bind", "acme")
        self.assertEqual(code, 0, out)
        self.assertEqual(victim.read_text(), "original\n")
        self.assertEqual((self.repo / ".cloudctx").read_text(), 'context = "acme"\n')

    def test_bind_ignores_repo_fsmonitor(self):
        marker = self.base / "fsmonitor-ran"
        hook = write_stub(self.base, "fsmon", f'touch "{marker}"\n')
        (self.repo / "f").write_text("x")
        git_cmd(self.repo, "add", "f")
        git_cmd(self.repo, "config", "core.fsmonitor", str(hook))
        code, out = self.run_cli("bind", "acme")
        self.assertEqual(code, 0, out)
        self.assertFalse(marker.exists())

    def test_bind_ignores_inherited_git_dir(self):
        other = self.base / "other"
        other.mkdir()
        git_cmd(other, "init", "-q")
        os.environ["GIT_DIR"] = str(other / ".git")
        code, out = self.run_cli("bind", "acme")
        self.assertEqual(code, 0, out)
        self.assertTrue((self.repo / ".cloudctx").is_file())
        self.assertFalse((other / ".cloudctx").exists())
        self.assertIn(".cloudctx", self.exclude_lines(self.repo / ".git"))
        self.assertNotIn(".cloudctx", self.exclude_lines(other / ".git"))

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


class TestVersion(Base):
    def test_version_flag(self):
        r = subprocess.run([CLI, "--version"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertRegex(r.stdout.strip(), r"^cloudctx \d+\.\d+\.\d+$")

    def test_version_constant_matches_flag(self):
        r = subprocess.run([CLI, "--version"], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), f"cloudctx {self.cc.__version__}")


if __name__ == "__main__":
    unittest.main()


class TestFinalReviewFixes(Base):
    PIN = "99999999-8888-7777-6666-555555555555"
    REG_SUB = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def setUp(self):
        super().setUp()
        self.addCleanup(os.chdir, os.getcwd())
        self.run_cli("new", "acme", "--azure-tenant", "t",
                     "--azure-subscription", self.REG_SUB, "--no-login")
        self.base = Path(tempfile.mkdtemp(prefix="cctx-final-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)

    def env(self):
        env = dict(os.environ)
        env["CLOUDCTX_HOME"] = self.tmp
        return env

    # 1. deleted cwd / untraversable ancestor
    def test_exec_from_deleted_cwd_runs(self):
        gone = self.base / "gone"
        gone.mkdir()
        script = ('import os,subprocess,sys; os.chdir(sys.argv[1]); os.rmdir(sys.argv[1]); '
                  'r=subprocess.run([sys.argv[2],"exec","acme","--","/bin/sh","-c",'
                  '"printf %s \\"$ARM_SUBSCRIPTION_ID\\""],capture_output=True,text=True); '
                  'sys.stdout.write(r.stdout); sys.stderr.write(r.stderr); sys.exit(r.returncode)')
        r = subprocess.run([sys.executable, "-c", script, str(gone), CLI],
                           env=self.env(), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, self.REG_SUB)
        self.assertNotIn("Traceback", r.stderr)

    def test_bind_from_deleted_cwd_dies_cleanly(self):
        gone = self.base / "gone"
        gone.mkdir()
        os.chdir(gone)
        os.rmdir(gone)
        for args in (("bind", "acme"), ("bind",), ("unbind",)):
            code, out = self.run_cli(*args)
            self.assertEqual(code, 1, args)
            self.assertIn("cloudctx: error:", out)

    def test_find_binding_untraversable_ancestor(self):
        if os.geteuid() == 0:
            self.skipTest("root traverses anything")
        (self.base / ".cloudctx").write_text('context = "acme"\n')
        locked = self.base / "locked"
        (locked / "inner").mkdir(parents=True)
        os.chmod(locked, 0)
        self.addCleanup(os.chmod, locked, 0o700)
        self.assertEqual(self.cc.find_binding(locked / "inner"), self.base / ".cloudctx")

    # 2. control characters in paths
    def test_controls_in_binding_path_are_stripped(self):
        evil = self.base / "x\x1b]0;x\x07y"
        evil.mkdir()
        (evil / ".cloudctx").write_text('context = "acme"\n')
        r = subprocess.run([CLI, "_env", "--binding", str(evil / ".cloudctx")],
                           env=self.env(), capture_output=True, text=True)
        self.assertIn("untrusted", r.stderr)
        r2 = subprocess.run([CLI, "exec", "acme", "--", "true"], cwd=str(evil),
                            env=self.env(), capture_output=True, text=True)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIn("untrusted", r2.stderr)
        for err in (r.stderr, r2.stderr):
            self.assertNotIn("\x1b", err)
            self.assertNotIn("\x07", err)
        os.chdir(evil)
        code, out = self.run_cli("bind", "acme")
        self.assertEqual(code, 0, out)
        self.assertNotIn("\x1b", out)
        self.assertNotIn("\x07", out)

    # 3. untrusted hint keeps the subscription
    def test_untrusted_hint_includes_subscription(self):
        (self.base / ".cloudctx").write_text(
            f'context = "acme"\nsubscription = "{self.PIN}"\n')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(self.cc.load_binding_for(self.base))
        self.assertIn(f"run 'cloudctx bind acme --subscription {self.PIN}' in that "
                      "folder to trust it", err.getvalue())

    def test_untrusted_hint_without_subscription(self):
        (self.base / ".cloudctx").write_text('context = "acme"\n')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.cc.load_binding_for(self.base)
        self.assertIn("run 'cloudctx bind acme' in that folder to trust it",
                      err.getvalue())

    # 4. note outside a git repo
    def test_bind_outside_git_notes(self):
        os.chdir(self.base)
        with unittest.mock.patch.object(self.cc, "binding_target",
                                        lambda cwd: (cwd, None)):
            code, out = self.run_cli("bind", "acme")
        self.assertEqual(code, 0, out)
        self.assertIn(f"cloudctx: note: {self.base} is not in a git repo; this "
                      "binding applies to every folder below it", out)

    def test_bind_in_git_repo_no_note(self):
        if not shutil.which("git"):
            self.skipTest("git not installed")
        git_cmd(self.base, "init", "-q")
        os.chdir(self.base)
        code, out = self.run_cli("bind", "acme")
        self.assertEqual(code, 0, out)
        self.assertNotIn("not in a git repo", out)

    # 5. trust file pruning
    def test_trust_file_prunes_missing_folders(self):
        dirs = {}
        for n in "abc":
            dirs[n] = self.base / n
            dirs[n].mkdir()
        for n in "ab":
            os.chdir(dirs[n])
            self.assertEqual(self.run_cli("bind", "acme")[0], 0)
        os.chdir(self.base)
        shutil.rmtree(str(dirs["a"]))
        os.chdir(dirs["c"])
        self.assertEqual(self.run_cli("bind", "acme")[0], 0)
        keys = set(self.cc.load_trusted())
        self.assertNotIn(str(dirs["a"] / ".cloudctx"), keys)
        self.assertIn(str(dirs["b"] / ".cloudctx"), keys)
        self.assertIn(str(dirs["c"] / ".cloudctx"), keys)
