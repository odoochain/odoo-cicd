import base64
from pathlib import Path
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from .test_run import SETTINGS


def safe_filename(filename):
    for x in "/\\;!()*":
        filename = filename.replace(x, '_')
    return filename


class TestrunUnittest(models.Model):
    _inherit = 'cicd.test.run'

    def _run_robot_tests(self):
        self = self.with_context(testrun=f'{self.id}_prepare_robots')

        with self._shell(quick=False) as shell:
            self._ensure_source_and_machines(
                shell, start_postgres=False, settings="")
            files = shell.odoo('list-robot-test-files')['stdout'].strip()
            files = list(filter(bool, files.split("!!!")[1].split("\n")))

        # there could be errors at install all
        self.branch_id._ensure_dump('full', self.commit_id.name)

        for index, robotfile in enumerate(files):
            self.as_job(f"robottest-{robotfile}")._run_robot_run(
                index, len(files), robotfile
            )

    def _run_robot_run(self, index, count, robot_file):
        breakpoint()
        test = self.branch_id.project_name
        test = self.branch_id.project_name
        safe_robot_file = safe_filename(robot_file)
        self = self.with_context(
            testrun=f"testrun_{self.id}_robot_{safe_robot_file}")

        with self._shell(quick=True) as shell:
            dump_path = self.branch_id._ensure_dump(
                'full', self.commit_id.name)
            settings = SETTINGS + (
                "\n"
                "RUN_ODOO_QUEUEJOBS=1\n"
                "RUN_ODOO_CRONJOBS=1\n"
                "RUN_ROBOT=1\n"
            )
            assert dump_path
            self._ensure_source_and_machines(
                shell, start_postgres=True, settings=settings)
            breakpoint()
            shell.odoo(
                'restore', 'odoo-db', dump_path,
                force=True)
            shell.wait_for_postgres()
            shell.odoo('up', '-d')

            configuration = shell.odoo('config', '--full')[
                'stdout'].splitlines()
            host_run_dir = [x for x in configuration if 'HOST_RUN_DIR:' in x]
            host_run_dir = Path(host_run_dir[0].split(":")[1].strip())
            robot_out = host_run_dir / 'odoo_outdir' / 'robot_output'

            def run(item):
                try:
                    shell.odoo(
                        'robot', '-p', 'password=1',
                        "--install-required-modules",
                        item, timeout=self.timeout_tests)
                    state = 'success'

                except Exception as ex:
                    state = 'failed'
                    self._report(
                        "Robot Test error (but retrying)", exception=ex)
                finally:
                    excel_file = shell.sql_excel((
                        "select id, name, state, exc_info "
                        "from queue_job"
                    ))
                    if excel_file:
                        self.queuejob_log = base64.b64encode(excel_file)

                breakpoint()
                robot_results_tar = shell.grab_folder_as_tar(robot_out)
                robot_results_tar = robot_results_tar and \
                    base64.b64encode(robot_results_tar) or False
                return {
                    'robot_output': robot_results_tar,
                    'state': state,
                }

            self._generic_run(
                shell, [robot_file],
                'robottest', run,
                try_count=self.retry_unit_tests,
                name_prefix=f"({index + 1} / {count}) ",
            )
