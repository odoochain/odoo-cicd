import re
import base64
from pathlib import Path
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from .test_run import SETTINGS

settings = SETTINGS + (
    "\n"
    "RUN_ODOO_QUEUEJOBS=1\n"
    "RUN_ODOO_CRONJOBS=1\n"
    "RUN_ROBOT=1\n"
)


def safe_filename(filename):
    for x in "/\\;!()*":
        filename = filename.replace(x, '_')
    return filename


class RobotTest(models.Model):
    _inherit = "cicd.test.run.line"
    _name = "cicd.test.run.line.robottest"

    filepath = fields.Char("Filepath")
    robot_output = fields.Binary("Robot Output", attachment=True)
    parallel = fields.Char("In Parallel")

    def _execute(self):
        breakpoint()
        # there could be errors at install all
        self.branch_id._ensure_dump('full', self.commit_id.name)

        safe_robot_file = safe_filename(self.filepath)
        self = self.with_context(
            testrun=f"testrun_{self.id}_robot_{safe_robot_file}")

        with self._shell(quick=True) as shell:
            dump_path = self.branch_id._ensure_dump(
                'full', self.commit_id.name)
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
                    shell.odoo('up', '-d', 'postgres')
                    shell.wait_for_postgres()
                    shell.odoo(
                        'robot', '-p', 'password=1',
                        "--install-required-modules",
                        item, timeout=self.timeout_tests)

                    excel_file = shell.sql_excel((
                        "select id, name, state, exc_info "
                        "from queue_job"
                    ))
                    if excel_file:
                        self.queuejob_log = base64.b64encode(excel_file)
                    state = 'success'

                except Exception as ex:  # pylint: disable=broad-except
                    state = 'failed'
                    self._report(
                        "Robot Test error (but retrying)", exception=ex)
                finally:
                    shell.odoo("kill", allow_error=True)
                    shell.odoo("rm", allow_error=True)
                    shell.odoo("down", "-v", force=True, allow_error=True)

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

    def robot_results(self):
        return {
            'type': 'ir.actions.act_url',
            'url': f'/robot_output/{self.id}',
            'target': 'new'
        }

class TestSettingsRobotTests(models.Model):
    _inherit = "cicd.test.settings.base"
    _name = 'cicd.test.settings.robottest'

    tags = fields.Char(
        "Filter to tags (comma separated, may be empty)", default="load-test")
    parallel = fields.Char(
        "In Parallel", required=True, default="1,2,5,10,20,50")
    regex = fields.Char("Regex", default=".*")

    def get_name(self):
        return f"{self.id} - {self.tags}"

    def produce_test_run_lines(self, testrun):
        breakpoint()
        with self._shell(quick=False) as shell:
            self._ensure_source_and_machines(
                shell, start_postgres=False, settings=settings)
            files = shell.odoo('list-robot-test-files')['stdout'].strip()
            files = list(filter(bool, files.split("!!!")[1].split("\n")))

        for robotfile in sorted(files):
            if self.regrex:
                if not re.findall(self.regex, robotfile):
                    continue
            self.env['cicd.test.run.line.robottest'].create({
                'run_id': testrun.id,
                'filepath': robotfile,
            })