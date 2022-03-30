from odoo import _, api, fields, models, SUPERUSER_ID
import tempfile
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from contextlib import contextmanager
from ..tools.logsio_writer import LogsIOWriter


class CicdReleaseAction(models.Model):
    _inherit = 'cicd.mixin.size'
    _name = 'cicd.release.action'

    release_id = fields.Many2one('cicd.release', string="Release", required=True)
    machine_id = fields.Many2one('cicd.machine', string="Machine")
    shell_script_before_update = fields.Text("Shell Script Before Update")
    shell_script_at_end = fields.Text("Shell Script At End (finally)")

    def _exec_shellscripts(self, logsio, pos):
        for rec in self:
            script = rec.shell_script_before_update \
                if pos == 'before' else rec.shell_script_at_end

            if not script:
                return

            filepath = tempfile.mktemp(suffix='.')

            with rec._contact_machine(logsio) as shell:
                shell.put(script, filepath)
                try:
                    shell.X(["/bin/bash", filepath])
                finally:
                    shell.rm(filepath)

    @api.model
    def run_action_set(self, release_item, actions, commit_sha):
        breakpoint()
        errors = []

        with release_item._extra_env() as unblocked_item:
            branch_name = unblocked_item.release_id.branch_id.name 

        with LogsIOWriter.GET(branch_name, 'release') as logsio:
            try:
                actions._exec_shellscripts(logsio, "before")
                actions._stop_odoo(logsio)

                actions._update_sourcecode(
                    logsio, release_item, commit_sha)

                actions[0]._run_update(logsio)

            except Exception as ex:
                errors.append(ex)

            finally:
                for action in actions:
                    try:
                        action._start_odoo(logsio=logsio)
                    except Exception as ex:
                        errors.append(ex)

                for action in actions:
                    try:
                        action._exec_shellscripts(logsio, "after")
                    except Exception as ex:
                        errors.append(ex)
            return errors

    @contextmanager
    def _contact_machine(self, logsio):
        self.ensure_one()
        project_name = self.release_id._unblocked('project_name')
        path = self.machine_id._get_volume("source")

        # make sure directory exists
        with self.machine_id._shell(
            cwd=path, logsio=logsio,
            project_name=project_name
        ) as shell:
            if not shell.exists(path):
                shell.X(["mkdir", "-p", path])
            yield shell

    def _stop_odoo(self, logsio):
        for self in self:
            with self._contact_machine(logsio) as shell:
                if not shell:
                    return
                shell.odoo("kill")

    def _update_sourcecode(self, logsio, release_item, merge_commit_id):
        breakpoint()
        with self._extra_env() as x_self:
            repo = x_self[0].release_id.repo_id
            zip_content = repo._get_zipped(logsio, merge_commit_id)
        temppath = tempfile.mktemp(suffix='.')

        for rec in self:
            with rec._contact_machine(logsio) as shell:
                filename = f"/tmp/release_{release_item.id}"
                shell.put(zip_content, filename)
                temppath = tempfile.mktemp(suffix='.')
                shell.X(['mkdir', '-p', temppath])
                shell.X(["tar", "xfz", filename], cwd=temppath)
                shell.X(["rsync", str(temppath) + "/", str(shell.cwd) + "/", "-ar", "--delete-after"])
                shell.rm(temppath)

    def _run_update(self, logsio):
        self.ensure_one()
        with self._contact_machine(logsio) as shell:
            shell.odoo("reload")
            shell.odoo("build")
            shell.odoo("update") # , "--i18n")

    def _start_odoo(self, logsio):
        for self in self:
            with self._contact_machine(logsio) as shell:
                shell.odoo("up", "-d")