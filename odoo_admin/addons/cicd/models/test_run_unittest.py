import time
import json
import threading
from pathlib import Path
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from .test_run import SETTINGS
import logging
_logger = logging.getLogger()

CONCURRENT_HASH_THREADS = 8  # minimum system load observed


class TestrunUnittest(models.Model):
    _inherit = 'cicd.test.run'

    def _run_unit_tests(self, **kwargs):
        self = self.with_context(testrun=f'{self.id}_prepare_unittests')
        self._checkout_source_code(self.machine_id)

        self._report("Hashing Modules / Preparing UnitTests")
        with self._shell(quick=False) as shell:
            unittests_to_run = self._get_unit_tests_to_run(shell)
            self._ensure_source_and_machines(
                shell, start_postgres=False, settings="")

        self._report("Hashing Modules / Preparing UnitTests Done")
        if not unittests_to_run:
            return

        count = len(list(unittests_to_run.keys()))
        for index, (module, tests) in enumerate(unittests_to_run.items()):
            hash = tests['hash']
            tests = tests['tests']

            self.as_job(
                f"unittest-module-{module}")._run_unit_tests_of_module(
                    index, count, module, hash, tests)
            self._report(f"Unittest in module {module}")

    def _run_unit_tests_of_module(self, index, count, module, hash, tests):
        breakpoint()
        self = self.with_context(testrun=f"testrun_{self.id}_{module}")
        with self._shell(quick=True) as shell:
            debug_project_name = self.branch_id.project_name
            dump_path = self.branch_id._ensure_base_dump()
            settings = SETTINGS + (
                "\nSERVER_WIDE_MODULES=base,web\n"
            )
            assert dump_path
            self._ensure_source_and_machines(
                shell, start_postgres=True, settings=settings)
            shell.odoo(
                'restore', 'odoo-db', dump_path,
                '--no-dev-scripts', force=True)
            self._wait_for_postgres(shell)

            def _update(item):
                shell.odoo('update', item, '--no-dangling-check')

            if not self._generic_run(
                shell, [module],
                'unittest', _update,
                name_prefix='install ',
            ):
                return

            def _unittest(item):
                shell.odoo(
                    'unittest', item, "--non-interactive",
                    timeout=self.timeout_tests)

            self._generic_run(
                shell, tests,
                'unittest', _unittest,
                try_count=self.retry_unit_tests,
                name_callback=self._unittest_name_callback,
                name_prefix=f"({index + 1} / {count}) ",
                odoo_module=module,
                hash=hash,
            )

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
            #ensure mod exists in result
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

        for module, tests in unittests_by_module.items():
            hash = hashes.get(module)
            if not hash:
                t = _setdefault(_unittests_by_module, module)
                t['tests'] = tests
                continue

            for test in tests:
                if not self.line_ids.check_if_test_already_succeeded(
                    self,
                    self._get_generic_run_name(
                        test, self._unittest_name_callback),
                    hash,
                ):
                    t = _setdefault(_unittests_by_module, module)
                    t['hash'] = hash
                    t['tests'].append(test)

        return _unittests_by_module

    def _get_unit_tests(self, shell):
        self.ensure_one()
        cmd = ['list-unit-test-files']
        if self.unittest_all:
            cmd += ['--all']
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
        res = shell.odoo("list-deps", module_path)
        stdout = res['stdout']
        deps = json.loads(stdout.split("---", 1)[1])
        return deps['hash']

    def _unittest_name_callback(self, f):
        p = Path(f)
        # TODO checking: 3 mal parent
        return str(p.relative_to(p.parent.parent.parent))
