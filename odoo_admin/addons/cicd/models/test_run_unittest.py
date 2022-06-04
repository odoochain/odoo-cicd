import time
import json
import threading
from pathlib import Path
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from .test_run import SETTINGS
from .shell_executor import ShellExecutor
from odoo.addons.queue_job.exception import RetryableJobError
import logging
_logger = logging.getLogger()

CONCURRENT_HASH_THREADS = 8  # minimum system load observed


class UnitTest(models.Model):
    _inherit = "cicd.test.run.line"
    _name = "cicd.test.run.line.unittest"

    odoo_module = fields.Char("Odoo Module")
    filepath = fields.Char("Filepath")

    def execute(self):
        import pudb;pudb.set_trace()
        self = self.with_context(testrun=f"testrun_{self.id}_{module}")
        with self._shell(quick=True) as shell:
            dump_path = self.branch_id._ensure_dump(
                'base', commit=self.commit_id.name)
            settings = SETTINGS + (
                "\nSERVER_WIDE_MODULES=base,web\n"
            )
            assert dump_path
            self._ensure_source_and_machines(
                shell, start_postgres=False, settings=settings)
            shell.odoo("down", "-v", force=True, allow_error=True)
            shell.odoo("up", "-d", "postgres")
            shell.odoo(
                'restore', 'odoo-db', dump_path,
                '--no-dev-scripts', force=True)
            shell.wait_for_postgres()

            def _update(item):
                shell.odoo('update', item, '--no-dangling-check')

            if not self._generic_run(
                shell, [module],
                'unittest', _update,
                name_prefix='install ',
            ):
                return

            def _unittest(item):
                try:
                    shell.odoo(
                        'unittest', item, "--non-interactive",
                        timeout=self.timeout_tests)
                finally:
                    shell.odoo("kill", allow_error=True)
                    shell.odoo("rm", allow_error=True)
                    shell.odoo("down", "-v", force=True, allow_error=True)

            self._generic_run(
                shell, tests,
                'unittest', _unittest,
                try_count=self.retry_unit_tests,
                name_callback=self._unittest_name_callback,
                name_prefix=f"({index + 1} / {count}) ",
                odoo_module=module,
                hash=hash,
            )

class TestSettingsUnittest(models.Model):
    _inherit = "cicd.test.settings.base"
    _name = 'cicd.test.settings.unittest'

    tags = fields.Char("Filter to tags (comma separated, may be empty)")
    regex = fields.Char("Regex", default=".*")

    def get_name(self):
        return f"{self.id} - {self.tags}"

    def produce_test_run_lines(self, testrun):
        testrun._report("Hashing Modules / Preparing UnitTests")
        with testrun._shell(quick=False) as shell:
            testrun._ensure_source_and_machines(
                shell, start_postgres=False, settings="")
            unittests_to_run = testrun._get_unit_tests_to_run(shell)

        testrun._report("Hashing Modules / Preparing UnitTests Done")
        if not unittests_to_run:
            return

        # make sure dump exists for all
        testrun.branch_id._ensure_dump('base', commit=testrun.commit_id.name)

        count = len(list(unittests_to_run.keys()))
        for index, (module, tests) in enumerate(unittests_to_run.items()):
            hash = tests['hash']
            tests = tests['tests']

            for test in tests:
                self.env['cicd.test.run.line.unittest'].create({
                    'run_id': testrun.id,
                    'odoo_module': module,
                    'filepath': test,
                })

    def _get_unittest_hashes(self, shell, modules):
        result = {}

        threadLimiter = threading.BoundedSemaphore(CONCURRENT_HASH_THREADS)

        class HashThread(threading.Thread):
            def run(self):
                self.threadLimiter.acquire()
                try:
                    self.run_me()
                finally:
                    self.threadLimiter.release()

            def run_me(self):
                global result
                hash = self.testrun._get_hash_for_module(
                    shell, self.module)
                self.result[self.module] = hash

        threads = []
        for mod in modules:
            # ensure mod exists in result
            result[mod] = False
            t = HashThread()
            t.module = mod
            t.testrun = self
            t.result = result
            t.threadLimiter = threadLimiter
            threads.append(t)
            t.start()

        [x.join() for x in threads]
        return result

    def _get_unit_tests_to_run(self, shell):
        self.ensure_one()
        unittests = self._get_unit_tests(shell)
        unittests_by_module = self._get_unit_tests_by_modules(unittests)
        _unittests_by_module = {}

        def _setdefault(d, m):
            return d.setdefault(m, {'tests': [], 'hash': None})

        hashes = self._get_unittest_hashes(
            shell, unittests_by_module.keys())

        shell.logsio.info("Analyzing following unittests if to run:")
        for module, tests in unittests_by_module.items():
            shell.logsio.info(f"Module: {module}")
            for test in tests:
                shell.logsio.info(f"  - {test}")

        for module, tests in unittests_by_module.items():
            hash = hashes.get(module)
            if not hash:
                t = _setdefault(_unittests_by_module, module)
                t['tests'] = tests
                continue

            for test in tests:
                generic_run_name = self._get_generic_run_name(
                    test, self._unittest_name_callback)

                test_already_succeeded = \
                    self.line_ids.check_if_test_already_succeeded(
                        self, generic_run_name, hash,
                )

                if self.no_reuse or (
                    not self.no_reuse and
                    not test_already_succeeded
                ):
                    t = _setdefault(_unittests_by_module, module)
                    t['hash'] = hash
                    t['tests'].append(test)

        return _unittests_by_module

    def _get_unit_tests(self, shell):
        self.ensure_one()
        cmd = ['list-unit-test-files']
        files = shell.odoo(*cmd)['stdout'].strip()
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
        breakpoint()
        res = shell.odoo("list-deps", module_path)
        stdout = res['stdout']
        deps = json.loads(stdout.split("---", 1)[1])
        return deps['hash']

    def _unittest_name_callback(self, f):
        p = Path(f)
        # TODO checking: 3 mal parent
        return str(p.relative_to(p.parent.parent.parent))
