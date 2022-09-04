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


class BrokenUnittest(Exception):
    pass


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
            settings = self.env["cicd.git.branch"]._get_settings_isolated_run(
                dbname=DBNAME,
                forcesettings=(
                    f"{SETTINGS}\n" f"SERVER_WIDE_MODULES=base,web\n" f"DBNAME={DBNAME}"
                ),
            )
            self._ensure_source_and_machines(
                shell,
                start_postgres=False,
                settings=settings,
            )
            shell.odoo("down", "-v", force=True, allow_error=True)
            shell.odoo("up", "-d", "postgres")
            shell.wait_for_postgres()  # wodoo bin needs to check version
            if self[0].test_setting_id.use_btrfs:
                shell.odoo("update", "base", "--no-dangling-check")
                shell.odoo("snap", "remove", self.snapname, allow_error=True)
                shell.odoo("snap", "save", self.snapname)
                shell.wait_for_postgres()

            yield shell, {
                "settings": settings,
            }

    def _execute(self, shell, runenv):
        self.broken_tests = False

        self._ensure_hash(shell)
        self.env.cr.commit()

        test_already_succeeded = self.test_setting_id.check_if_test_already_succeeded(
            self.run_id, odoo_module=self.odoo_module, hash=self.hash
        )

        if not self.run_id.no_reuse and test_already_succeeded:
            self.reused = True
            return

        try:
            self._execute_test_at_prepared_environment(shell, runenv)
        finally:
            self.env.cr.commit()
            self._report("Unittest finished")

    def _execute_test_at_prepared_environment(self, shell, runenv):
        self._report(f"Installing module {self.odoo_module}")
        if self[0].test_setting_id.use_btrfs:
            shell.odoo("snap", "restore", self.snapname)
            shell.odoo("up", "-d", "postgres")
            shell.wait_for_postgres()

        else:
            shell.odoo("up", "-d", "postgres")
            shell.wait_for_postgres()
            shell.odoo("update", "base", "--no-dangling-check")

        shell.odoo(
            "update",
            self.odoo_module,
            "--no-dangling-check",
            f"--test-tags={self.tags}",
        )

    def _compute_name(self):
        for rec in self:
            paths = (rec.filepaths or "").split(",")
            rec.name = f"{rec.odoo_module}[{len(paths)}]"


class TestSettingsUnittest(models.Model):
    _inherit = "cicd.test.settings.base"
    _name = "cicd.test.settings.unittest"

    tags = fields.Char(
        "Filter to tags (comma separated, may be empty)",
        required=True,
        default=(
            "-at_install,-standard,"
            "at_install/{module},post_install/{module},"
            "standard/{module}"
        ),
    )
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
                modules = self._get_modules_to_test(shell, precalc=self.precalc_hashes)

            logsio.info("Hashing Modules / Preparing UnitTests Done")
            if not modules:
                return

            for module, info in modules.items():
                hash_value = info["hash"]
                tags = self.tags.format(module=module)

                res.append(
                    self.env["cicd.test.run.line.unittest"].create(
                        self.get_testrun_values(
                            testrun,
                            {
                                "odoo_module": module,
                                "hash": hash_value,
                                "tags": tags,
                            },
                        )
                    )
                )
                del module
        return res

    def _get_unittest_hashes(self, shell, modules):
        result = {}

        thread_limiter = threading.BoundedSemaphore(CONCURRENT_HASH_THREADS)

        class HashThread(threading.Thread):
            def __init__(self, module, testrun, result, thread_limiter):
                super().__init__()
                self.module = module
                self.testrun = testrun
                self.result = result
                self.thread_limiter = thread_limiter

            def run(self):
                self.thread_limiter.acquire()
                try:
                    self.run_me()
                finally:
                    self.thread_limiter.release()

            def run_me(self):
                hash = self.testrun._get_hash_for_module(shell, self.module)
                self.result[self.module] = hash

        threads = []
        for module in modules:
            # ensure mod exists in result
            result[module] = False
            threads.append(
                HashThread(
                    module=module,
                    testrun=self,
                    result=result,
                    thread_limiter=thread_limiter,
                )
            )
            del module

        [x.start() for x in threads]  # pylint: disable=W0106
        [x.join() for x in threads]  # pylint: disable=W0106
        return result

    def _get_all_modules(self, shell):
        res = shell.odoo("list-modules")
        for module in res["stdout"].strip().split("---")[1].splitlines():
            if self.regex:
                if not re.findall(self.regex, module):
                    continue
            if not module:
                continue
            yield module

    def _get_modules_to_test(self, shell, precalc):
        self.ensure_one()
        modules = list(self._get_all_modules(shell))

        def _setdefault(d, m):
            return d.setdefault(m, {"tests": [], "hash": None})

        hashes = {}
        module_infos = {}
        if precalc and not self.parent_id.no_reuse:
            hashes = self._get_unittest_hashes(shell, modules)

        shell.logsio.info("Analyzing following unittests if to run:")
        for module in modules:
            hash = hashes.get(module)
            if not hash:
                test_already_succeeded = False
            else:
                test_already_succeeded = self.check_if_test_already_succeeded(
                    self.parent_id, odoo_module=module, hash=hash
                )

            if self.parent_id.no_reuse or not test_already_succeeded:
                val = _setdefault(module_infos, module)
                val["hash"] = hash
                del val
            del hash
            del module

        return module_infos

    @api.model
    def _get_hash_for_module(self, shell, module_path):
        res = shell.odoo("list-deps", module_path, force=True)
        stdout = res["stdout"]
        deps = json.loads(stdout.split("---", 1)[1])
        return deps["hash"]

    @api.model
    def check_if_test_already_succeeded(self, testrun, odoo_module, hash):
        """
        Compares the hash of the module with an existing
        previous run with same hash.
        """
        tests = self.env["cicd.test.run.line.unittest"].search(
            [
                ("run_id.branch_ids.repo_id", "=", testrun.branch_ids.repo_id.id),
                ("reused", "=", False),
                ("odoo_module", "=", odoo_module),
                ("hash", "=", hash),
                ("state", "in", ["success", "failed"]),
            ],
            order="date_finished desc, id desc",
            limit=1,
        )
        return tests and tests.state == "success"
