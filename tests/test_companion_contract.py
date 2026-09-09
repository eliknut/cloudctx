"""Companion contract tests.

Other tools drive cloudctx from the outside (pimctl is the first). Every
surface they may rely on is listed in docs/companions.md and pinned here, so
changing one fails the suite and has to be a deliberate, versioned change.

Run with: python3 -m unittest discover -s tests -v
"""
import os
import subprocess

from test_cloudctx import CLI, Base

# The exact set of environment variables cloudctx manages. `cloudctx clear`
# unsets all of them and `cloudctx exec` strips inherited ones before
# overlaying the target context's. A companion that spawns a "no context"
# child (pimctl's --bare-az) copies this list, so a change here is a contract
# change: update docs/companions.md and tell the companions.
MANAGED_VARS = [
    "CLOUDCTX_CONTEXT",
    "CLOUDCTX_AZURE_LABEL",
    "CLOUDCTX_STORE",
    "AZURE_CONFIG_DIR",
    "AWS_CONFIG_FILE",
    "AWS_SHARED_CREDENTIALS_FILE",
    "AWS_PROFILE",
]


class ContractBase(Base):
    def setUp(self):
        super().setUp()
        self.run_cli("new", "acme", "--display", "Acme AB",
                     "--azure-tenant", "t-acme", "--no-login")
        self.run_cli("new", "globex", "--azure-tenant", "t-globex", "--no-login")

    def run_bin(self, *args, env_extra=None):
        """Run the real binary as a companion would: a child process."""
        env = dict(os.environ)
        env["CLOUDCTX_HOME"] = self.tmp
        if env_extra:
            env.update(env_extra)
        return subprocess.run([CLI, *args], env=env, capture_output=True, text=True)


class TestListNames(ContractBase):
    def test_names_are_sorted_one_per_line_and_nothing_else(self):
        code, out = self.run_cli("list", "--names")
        self.assertEqual(code, 0)
        self.assertEqual(out, "acme\nglobex\n")

    def test_names_matches_internal_alias(self):
        _, public = self.run_cli("list", "--names")
        _, internal = self.run_cli("_names")
        self.assertEqual(public, internal)

    def test_names_and_verbose_are_exclusive(self):
        r = self.run_bin("list", "--names", "-v")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")


class TestListNamesEmpty(Base):
    def test_empty_registry_prints_nothing(self):
        code, out = self.run_cli("list", "--names")
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_human_list_sentence_is_pinned(self):
        # pimctl matches this sentence to tell "no contexts" from a parse
        # failure; companions should prefer --names, but the sentence is
        # part of the contract until they all do.
        _, out = self.run_cli("list")
        self.assertEqual(out.strip(), "no contexts. Create one with: cloudctx new <name>")


class TestShowFormat(ContractBase):
    def test_header_then_key_equals_value_lines(self):
        code, out = self.run_cli("show", "acme")
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(lines[0], "[acme]")
        self.assertIn("azure_tenant = t-acme", lines)
        self.assertIn("display = Acme AB", lines)

    def test_store_path_is_printed(self):
        _, out = self.run_cli("show", "acme")
        store_lines = [ln for ln in out.splitlines() if ln.startswith("store:")]
        self.assertEqual(len(store_lines), 1, msg=out)
        self.assertEqual(store_lines[0].split(None, 1)[1].strip(),
                         str(self.cc.context_dir("acme")))


class TestStoreVariable(ContractBase):
    def test_env_exports_store_path(self):
        _, out = self.run_cli("_env", "acme")
        store = str(self.cc.context_dir("acme").resolve())
        self.assertIn(f"export CLOUDCTX_STORE='{store}'", out)

    def test_clear_unsets_store(self):
        _, out = self.run_cli("_env", "--clear")
        self.assertIn("unset CLOUDCTX_STORE", out)

    def test_managed_variable_list_is_pinned(self):
        self.assertEqual(list(self.cc.CLEARABLE_VARS), MANAGED_VARS)

    def test_exec_child_sees_store_and_not_callers(self):
        r = self.run_bin("exec", "acme", "--", "sh", "-c", 'printf "%s" "$CLOUDCTX_STORE"',
                         env_extra={"CLOUDCTX_STORE": "/stale/other"})
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(r.stdout, str(self.cc.context_dir("acme").resolve()))

    def test_exec_overlays_context_and_strips_managed_vars(self):
        r = self.run_bin("exec", "acme", "--", "sh", "-c",
                         'printf "%s|%s" "$CLOUDCTX_CONTEXT" "$AWS_PROFILE"',
                         env_extra={"CLOUDCTX_CONTEXT": "globex", "AWS_PROFILE": "stale"})
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(r.stdout, "acme|")


class TestErrorContract(ContractBase):
    def test_unknown_context_phrase_and_exit_code(self):
        for args in (("exec", "nope", "--", "true"), ("show", "nope")):
            r = self.run_bin(*args)
            self.assertEqual(r.returncode, 1, msg=args)
            self.assertEqual(r.stdout, "", msg=args)
            self.assertIn("cloudctx: error: unknown context 'nope'", r.stderr, msg=args)


class TestDeleteSweepsCompanionState(ContractBase):
    def _plant(self):
        d = self.cc.context_dir("acme") / "pimctl"
        d.mkdir(parents=True, exist_ok=True)
        (d / "token-acme.json").write_text("{}")
        return d

    def test_delete_removes_state_under_store(self):
        d = self._plant()
        code, _ = self.run_cli("delete", "acme", "--force")
        self.assertEqual(code, 0)
        self.assertFalse(d.exists())

    def test_keep_store_keeps_it(self):
        d = self._plant()
        code, _ = self.run_cli("delete", "acme", "--force", "--keep-store")
        self.assertEqual(code, 0)
        self.assertTrue((d / "token-acme.json").exists())
