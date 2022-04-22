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

    def _unittest_name_callback(self, f):
        p = Path(f)
        # TODO checking: 3 mal parent
        return str(p.relative_to(p.parent.parent.parent))

    def _run_unit_tests(self, shell, logsio, **kwargs):
        self._report("Hashing Modules / Preparing UnitTests")
        unittests_to_run = self._get_unit_tests_to_run(shell)
        self._report("Hashing Modules / Preparing UnitTests Done")
        if not unittests_to_run:
            return

        # deactivate queuejob module
        breakpoint()
        self._reload(
            shell, SETTINGS + (
                "SERVER_WIDE_MODULES=base,web\n"
            ),
            str(Path(shell.cwd).parent)
        )

        i = 0
        for module, tests in unittests_to_run.items():
            hash = tests['hash']
            tests = tests['tests']

            self._abort_if_required()
            i += 1
            shell.odoo("snap", "restore", shell.project_name)
            self._wait_for_postgres(shell)

            def _update(item):
                shell.odoo('update', item)

            success = self._generic_run(
                shell, logsio, [module],
                'unittest', _update,
                name_prefix='install ',
            )
            if not success:
                continue

            self._wait_for_postgres(shell)

            def _unittest(item):
                shell.odoo(
                    'unittest', item, "--non-interactive",
                    timeout=self.branch_id.timeout_tests)

            self._generic_run(
                shell, logsio, tests,
                'unittest', _unittest,
                try_count=self.branch_id.retry_unit_tests,
                name_callback=self._unittest_name_callback,
                name_prefix=f"({i} / {len(unittests_to_run)}) ",
                unique_name=module,
                hash=hash,
            )

    def _get_unittest_hashes(self, shell, modules):
        threads = []
        modules = list(modules)
        result = {}
        breakpoint()

        def _get_hash(module):
            while len([x for x in threads if x.is_alive()]) > CONCURRENT_HASH_THREADS:
                time.sleep(0.5)
            try:
                hash = self._get_hash_for_module(shell, module)
                result[module] = hash
            except Exception as ex:
                _logger.error(ex, exc_info=True)
            finally:
                modules.remove(module)

        for mod in modules:
            t = threading.Thread(target=_get_hash, args=(mod,))
            threads.append(t)
            t.start()

        while modules:
            time.sleep(0.5)

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
            hash = hashes[module]
            if not hash:
                t = _setdefault(_unittests_by_module, module)
                t['tests'] = tests
                continue

            for test in tests:
                if not self.line_ids._check_if_test_already_succeeded(
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
        if self.branch_id.unittest_all:
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
