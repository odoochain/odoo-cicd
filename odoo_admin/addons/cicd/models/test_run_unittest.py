import re
import json
import random
import threading
from pathlib import Path
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from .test_run import SETTINGS
from .shell_executor import ShellExecutor
from odoo.addons.queue_job.exception import RetryableJobError
import logging
from contextlib import contextmanager, closing

_logger = logging.getLogger()

CONCURRENT_HASH_THREADS = 8  # minimum system load observed
GRAB_OTHERS = 5


class UnitTest(models.Model):
    _inherit = "cicd.test.run.line"
    _name = "cicd.test.run.line.unittest"

    odoo_module = fields.Char("Odoo Module")
    filepaths = fields.Char("Filepath")
    display_filepaths = fields.Text("Filepaths", compute="_compute_display_filepaths")
    hash = fields.Char("Hash", help="For using")
    broken_tests = fields.Char("Broken Tests")
    unittests_per_worker = fields.Integer(default=5)
    tags = fields.Char("Tags")

    @api.depends("filepaths")
    def _compute_display_filepaths(self):
        for rec in self:
            names = (rec.filepaths or "").split(",")
            names = list(map(lambda x: x.split("/")[-1], names))
            rec.display_filepaths = ",".join(names)

    def _ensure_hash(self, shell):
        if not self.hash:
            self.hash = self.test_setting_id._get_hash_for_module(
                shell, self.odoo_module
            )

    @contextmanager
    def get_environment_for_execute(self):
        breakpoint()
        DBNAME = "odoo"
        with self._shell(quick=True) as shell:
            dump_path = self.run_id.branch_id._ensure_dump(
                "base",
                commit=self.run_id.commit_id.name,
                dumptype="wodoobin",
                dbname=DBNAME,
            )
            self.env.cr.commit()  # publish the dump; there is a cache instruction on the branch

            settings = SETTINGS + (f"\nSERVER_WIDE_MODULES=base,web\nDBNAME={DBNAME}")
            assert dump_path

            self._ensure_source_and_machines(
                shell,
                start_postgres=False,
                settings=settings,
            )
            shell.odoo("down", "-v", force=True, allow_error=True)
            breakpoint()

            shell.odoo("up", "-d", "postgres")
            shell.wait_for_postgres()  # wodoo bin needs to check version
            shell.odoo("restore", "odoo-db", dump_path, "--no-dev-scripts", force=True)
            if any(self.mapped('test_setting_id.use_btrfs')):
                shell.odoo("snap", "remove", self.snapname, allow_error=True)
                shell.odoo("snap", "save", self.snapname)
            shell.wait_for_postgres()

            try:
                yield shell, {'dump_path': dump_path}
            finally:
                if any(self.mapped('test_setting_id.use_btrfs')):
                    shell.odoo("snap", "remove", self.snapname, allow_error=True)
                shell.odoo("kill", allow_error=True)
                shell.odoo("rm", allow_error=True)
                shell.odoo("down", "-v", force=True, allow_error=True)

    def _execute(self, shell, runenv):
        breakpoint()
        self.broken_tests = False

        self._ensure_hash(shell)
        self.env.cr.commit()

        if not self.run_id.no_reuse:
            if self.test_setting_id.check_if_test_already_succeeded(
                self.run_id, odoo_module=self.odoo_module, hash=self.hash
            ):
                self.reused = True
                return

        try:
            self._execute_test_at_prepared_environment(shell, runenv)
        finally:
            self.env.cr.commit()
            self._report("Unittest finished")

    def _execute_test_at_prepared_environment(self, shell, runenv):
        self._report(f"Installing module {self.odoo_module}")
        if any(self.mapped('test_setting_id.use_btrfs')):
            shell.odoo("snap", "restore", self.snapname)
        else:
            shell.odoo("restore", "odoo-db", runenv['dump_path'], "--no-dev-scripts", force=True)
        shell.odoo("up", "-d", "postgres")
        shell.wait_for_postgres()
        shell.odoo("update", self.odoo_module, "--no-dangling-check")
        breakpoint()
        logoutput = []

        broken = []
        for path in self.filepaths.split(","):
            self._report(f"Starting Unittest {path}")
            res = shell.odoo(
                "unittest",
                path,
                "--non-interactive",
                timeout=self.test_setting_id.timeout,
                allow_error=True,
            )
            if res["exit_code"]:
                broken.append(path)
                logoutput.append(res['stdout'])
                logoutput.append(res['stderr'])
        if broken:
            self.broken_tests = ",".join(broken)
            logoutput = '\n'.join(logoutput)
            raise Exception(
                f"Broken tests: {self.broken_tests}\n\n"
                f"Consoleoutput: {logoutput}"
                )

    def _compute_name(self):
        for rec in self:
            paths = (rec.filepaths or "").split(",")
            rec.name = f"{rec.odoo_module}[{len(paths)}]"


class TestSettingsUnittest(models.Model):
    _inherit = "cicd.test.settings.base"
    _name = "cicd.test.settings.unittest"

    tags = fields.Char("Filter to tags (comma separated, may be empty)")
    regex = fields.Char("Regex", default=".*")
    precalc_hashes = fields.Boolean("Pre-Calculate Hashes")

    def get_name(self):
        """Generate a unique name used in test lines generated.

        Returns:
            string: the name
        """
        return f"{self._name}:{self.id} - {self.tags or 'no tags'}"

    def produce_test_run_lines(self, testrun):
        """Creates lines that define a test run each based on Settings.
        For example a configuration for robottests with setting 1,2,5 will create
        3 test lines where the parallelity is set to 1 and 2 and 5.

        Args:
            testrun (cicd.test.run<Model>): A testrun, which is the parent of the lines.
        """
        res = []

        # pylint: disable=W0212
        super().produce_test_run_lines(testrun)
        with self.parent_id._logsio() as logsio:
            logsio.info("Hashing Modules / Preparing UnitTests")
            with self.parent_id._get_source_for_analysis() as shell:
                unittests_to_run = self._get_unit_tests_to_run(
                    shell, precalc=self.precalc_hashes
                )

            logsio.info("Hashing Modules / Preparing UnitTests Done")
            if not unittests_to_run:
                return

            for module, tests in unittests_to_run.items():
                hash_value = tests["hash"]
                tests = tests["tests"]

                used_tests = []
                for test in tests:
                    if self.regex:
                        if not re.findall(self.regex, test):
                            continue
                    used_tests.append(test)
                    del test

                res.append(
                    self.env["cicd.test.run.line.unittest"].create(
                        self.get_testrun_values(
                            testrun,
                            {
                                "odoo_module": module,
                                "filepaths": ",".join(sorted(used_tests)),
                                "hash": hash_value,
                            },
                        )
                    )
                )
                del module
                del tests
        return res

    def _get_unittest_hashes(self, shell, modules):
        result = {}

        thread_limiter = threading.BoundedSemaphore(CONCURRENT_HASH_THREADS)

        class HashThread(threading.Thread):
            def run(self):
                self.thread_limiter.acquire()
                try:
                    self.run_me()
                finally:
                    self.thread_limiter.release()

            def run_me(self):
                global result
                hash = self.testrun._get_hash_for_module(shell, self.module)
                self.result[self.module] = hash

        threads = []
        for mod in modules:
            # ensure mod exists in result
            result[mod] = False
            t = HashThread()
            t.module = mod
            t.testrun = self
            t.result = result
            t.thread_limiter = thread_limiter
            threads.append(t)
            t.start()

        [x.join() for x in threads]  # pylint: disable=W0106
        return result

    def _get_unit_tests_to_run(self, shell, precalc):
        breakpoint()
        self.ensure_one()
        unittests = self._get_unit_tests(shell)
        unittests_by_module = self._get_unit_tests_by_modules(unittests)
        _unittests_by_module = {}

        def _setdefault(d, m):
            return d.setdefault(m, {"tests": [], "hash": None})

        hashes = {}
        if precalc and not self.parent_id.no_reuse:
            hashes = self._get_unittest_hashes(shell, unittests_by_module.keys())

        shell.logsio.info("Analyzing following unittests if to run:")
        for module, tests in unittests_by_module.items():
            shell.logsio.info(f"Module: {module}")
            for test in tests:
                shell.logsio.info(f"  - {test}")
                del test

        for module, tests in unittests_by_module.items():
            hash = hashes.get(module)
            if not hash:
                test_already_succeeded = False
            else:
                test_already_succeeded = self.check_if_test_already_succeeded(
                    self.parent_id, odoo_module=module, hash=hash
                )

            if self.parent_id.no_reuse or not test_already_succeeded:
                for test in tests:
                    val = _setdefault(_unittests_by_module, module)
                    val["hash"] = hash
                    val["tests"].append(test)
                    del val
            del hash
            del module

        return _unittests_by_module

    def _get_unit_tests(self, shell):
        self.ensure_one()
        cmd = ["list-unit-test-files"]
        files = shell.odoo(*cmd)["stdout"].strip()
        return list(filter(bool, files.split("!!!")[1].splitlines()))

    def _get_unit_tests_by_modules(self, files):
        tests_by_module = {}
        for fpath in files:
            f = Path(fpath)
            # TODO perhaps check for manifest; framework would have that info
            module = str(f.parent.parent.name)
            tests_by_module.setdefault(module, [])
            if fpath not in tests_by_module[module]:
                tests_by_module[module].append(fpath)
        return tests_by_module

    @api.model
    def _get_hash_for_module(self, shell, module_path):
        res = shell.odoo("list-deps", module_path)
        stdout = res["stdout"]
        deps = json.loads(stdout.split("---", 1)[1])
        return deps["hash"]

    def _unittest_name_callback(self, f):
        p = Path(f)
        # TODO checking: 3 mal parent
        return str(p.relative_to(p.parent.parent.parent))

    @api.model
    def check_if_test_already_succeeded(self, testrun, odoo_module, hash):
        """
        Compares the hash of the module with an existing
        previous run with same hash.
        """
        res = self.env["cicd.test.run.line.unittest"].search_count(
            [
                ("run_id.branch_ids.repo_id", "=", testrun.branch_ids.repo_id.id),
                ("reused", "=", False),
                ("odoo_module", "=", odoo_module),
                ("hash", "=", hash),
                ("state", "=", "success"),
            ]
        )
        return bool(res)
