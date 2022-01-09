from odoo import _, api, fields, models, SUPERUSER_ID
import tempfile
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
    def run_action_set(self, release_item, actions):
        errors = []
        logsio = LogsIOWriter(self.release_id.branch_id.name, 'release')
        try:
            actions._stop_odoo(logsio)

            actions._update_source(logsio, release_item)

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

    def _update_sourcecode(self, logsio, release_item, collect_error=[]):
        repo = self[0].release_id.repo_id
        zip_content = repo._get_zipped(logsio, release_item.commit_id.name)
        temppath = tempfile.mktemp(suffix='.')
        
        for self in self:
            with self._contact_machine(logsio) as shell:
                filename = f"/tmp/release_{release_item.id}"
                shell.put(zip_content, filename)
                shell.X(["tar", "xfz", filename], cwd=temppath)
                shell.X(["rsync", str(temppath) + "/", str(shell.cwd) + "/", "-arP", "--delete-after"])
                shell.rmifexists(temppath)

    def _run_udpate(self, logsio, collect_error=[]):
        self.ensure_one()
        with self._contact_machine(logsio) as shell:
            shell.odoo("update")

    def _start_odoo(self, logsio, collect_error=[]):
        for self in self:
            with self._contact_machine(logsio) as shell:
                try:
                    shell.odoo("up", "-d")
                except Exception as ex:
                    collect_error.append(ex)