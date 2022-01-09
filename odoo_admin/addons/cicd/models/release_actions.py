from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from contextlib import contextmanager
from ..tools.logsio_writer import LogsIOWriter

class CicdReleaseAction(models.Model):
    _name = 'cicd.release.action'

    release_id = fields.Many2one('cicd.release', string="Release", required=True)
    machine_id = fields.Many2one('cicd.machine', string="Machine")
    shell_script_before_udpate = fields.Text("Shell Script Before Update")
    shell_script_at_end = fields.Text("Shell Script At End (finally)")

    @api.model
    def run_action_set(self, release, actions):
        errors = []
        logsio = LogsIOWriter(self.release_id.branch_id.name, 'release')
        try:
            actions._stop_odoo(logsio)

            actions._update_source(logsio)

            actions[0]._run_udpate(logsio)

        except Exception as ex:
            errors.append(ex)

        finally:
            actions._start_odoo(collect_errors=errors)

    @contextmanager
    def _contact_machine(self, logsio):
        self.ensure_one()
        project_name = self.release_id.project_name
        path = self.machine_id._get_volume("source") / project_name
        with self.machine_id._shellexec(
            cwd=path,
            logsio=logsio,
            project_name=project_name
        ) as shell:
            yield shell

    def _stop_odoo(self, logsio):
        for self in self:
            with self._contact_machine(logsio) as shell:
                if not shell:
                    return
                shell.odoo("kill")

    def _update_sourcecode(self, logsio, collect_error=[]):
        repo = self[0].release_id.repo_id
        repo._get_main_repo()
        for self in self:
            with self._contact_machine(logsio) as shell:
                shell.odoo("update")

    def _run_udpate(self, logsio, collect_error=[]):
        self.ensure_one()
        with self._contact_machine(logsio) as shell:
            shell.odoo("update")
                release.repo_id._get_main_repo(destination_folder=path, machine=machine)

    def _start_odoo(self, logsio, collect_error=[]):
        for self in self:
            with self._contact_machine(logsio) as shell:
                try:
                    shell.odoo("up", "-d")
                except Exception as ex:
                    collect_error.append(ex)