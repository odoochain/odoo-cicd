import base64
from pathlib import Path
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError

class TestrunUnittest(models.Model):
    _inherit = 'cicd.test.run'

    def _run_robot_tests(self, shell, **kwargs):
        files = shell.odoo('list-robot-test-files')['stdout'].strip()
        files = list(filter(bool, files.split("!!!")[1].split("\n")))

        configuration = shell.odoo('config', '--full')['stdout'].splitlines()
        host_run_dir = [x for x in configuration if 'HOST_RUN_DIR:' in x]
        host_run_dir = Path(host_run_dir[0].split(":")[1].strip())
        robot_out = host_run_dir / 'odoo_outdir' / 'robot_output'

        try:
            self._report("Installing all modules from MANIFEST...")
            SNAP_NAME = "robot_tests"
            shell.odoo("snap", "restore", shell.project_name)
            shell.odoo('up', '-d', 'postgres')
            self._wait_for_postgres(shell)
            # only base db exists no installed modules
            shell.odoo("update")
            shell.odoo("robot", "--all", "--install-required-modules")

            shell.odoo("snap", "save", SNAP_NAME)
            shell.odoo("kill")
            self._report("Installed all modules from MANIFEST")
        except Exception as ex:
            self._report("Error at preparing robot tests", exception=ex)
            self.env.cr.commit()
            raise

        def _run_robot_run(item):

            shell.odoo("snap", "restore", SNAP_NAME)
            self._report("Restored snapshot - driving up db.")
            shell.odoo("kill", allow_error=True)
            shell.odoo("rm", allow_error=True)
            shell.odoo('up', '-d', 'postgres')
            self._wait_for_postgres(shell)
            shell.odoo('up', '-d')

            try:
                shell.odoo(
                    'robot', '-p', 'password=admin',
                    item, timeout=self.timeout_tests)
                state = 'success'
            except Exception as ex:
                state = 'failed'
                self._report("Robot Test error (but retrying)", exception=ex)
                raise
            finally:
                excel_file = shell.sql_excel((
                    "select id, name, state, exc_info "
                    "from queue_job"
                ))
                self.queuejob_log = base64.b64encode(excel_file)
                self.env.cr.commit()

            robot_results_tar = shell.grab_folder_as_tar(robot_out)
            robot_results_tar = base64.b64encode(robot_results_tar)
            return {
                'robot_output': robot_results_tar,
                'state': state,
            }

        self._generic_run(
            shell, files,
            'robottest', _run_robot_run,
            try_count=self.retry_unit_tests,
        )
