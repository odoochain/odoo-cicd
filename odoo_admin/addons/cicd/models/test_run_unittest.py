from pathlib import Path
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError


class TestrunUnittest(models.Model):
    _inherit = 'cicd.test.run'

    def _run_unit_tests(self, shell, logsio, **kwargs):
        cmd = ['list-unit-test-files']
        if self.branch_id.unittest_all:
            cmd += ['--all']
        files = shell.odoo(*cmd)['stdout'].strip()
        files = list(filter(bool, files.split("!!!")[1].split("\n")))

        tests_by_module = self._get_unit_tests_by_modules(files)
        i = 0

        def name_callback(f):
            p = Path(f)
            return str(p.relative_to(p.parent.parent.parent))

        for module, tests in tests_by_module.items():
            i += 1
            shell.odoo("snap", "restore", shell.project_name)
            self._wait_for_postgres(shell)

            def _update(item):
                shell.odoo('update', item)

            success = self._generic_run(
                shell, logsio, [module],
                'unittest', _update,
                name_prefix='install '
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
                name_callback=name_callback,
                name_prefix=f"({i} / {len(tests_by_module)}) "
            )

    def _get_unit_tests_by_modules(self, files):
        tests_by_module = {}
        for fpath in files:
            f = Path(fpath)
            module = str(f.parent.parent.name)
            tests_by_module.setdefault(module, [])
            if fpath not in tests_by_module[module]:
                tests_by_module[module].append(fpath)
        return tests_by_module

    @api.model
    def _get_hash_for_module(self, module_path):