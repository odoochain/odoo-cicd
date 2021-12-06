import os
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from ..tools.tools import _get_shell_url

class Branch(models.Model):
    _inherit = 'cicd.git.branch'

    def build(self):
        self.ensure_one()
        self._make_task("_build")

    def cleardb(self):
        self.ensure_one()
        self._make_task("_clear_db")

    def docker_start(self):
        self.ensure_one()
        self._make_task("_docker_start", True)

    def docker_stop(self):
        self.ensure_one()
        self._make_task("_docker_stop", True)

    def docker_get_state(self, now=True):
        for rec in self:
            rec._make_task("_docker_get_state", now=now)

    def create_empty_db(self):
        self.ensure_one()
        self._make_task("_create_empty_db")

    def _check_dump_requirements(self):
        if not self.backup_machine_id:
            raise ValidationError(_("Please choose a machine where dump/restoring happens."))

    def dump(self):
        self.ensure_one()
        self._check_dump_requirements()
        self._make_task("_dump", machine=self.backup_machine_id)

    def turn_into_dev(self):
        self.ensure_one()
        self._make_task("_turn_into_dev")

    def reload(self):
        self.ensure_one()
        self._make_task("_reload", True)

    def reload_and_restart(self):
        self.ensure_one()
        self._make_task("_reload_and_restart")

    def remove_web_assets(self):
        self.ensure_one()
        self._make_task("_remove_web_assets")

    def restore_dump(self):
        self.ensure_one()
        self._check_dump_requirements()

        self._make_task("_restore_dump", machine=self.backup_machine_id)

    def run_tests(self, update_state=True):
        self.ensure_one()
        self._make_task("_run_tests", kwargs={'update_state': True})

    def start(self):
        return {
            'type': 'ir.actions.act_url',
            'url': '/start/' + self.project_name,
            'target': 'self'
        }

    def update_git_commits(self):
        self.ensure_one()
        self._make_task("_update_git_commits")

    def transform_input_dump(self):
        self.ensure_one()
        self._make_task("_transform_input_dump")

    def start_webmailer(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': '/start/' + self.name + "/mailer/",
            'target': 'new'
        }

    def start_logs(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': '/start/' + self.name + "/logs/",
            'target': 'new'
        }

    def _shell_url(self, cmd, machine=None):
        machine = self.machine_id
        machine.make_login_possible_for_webssh_container()
        path = machine._get_volume('source')
        path = path / self.project_name 
        shell_url = _get_shell_url(
            machine.effective_host,
            machine.ssh_user_cicdlogin,
            machine.ssh_user_cicdlogin_password,
            [
                f"export CICD_WORKSPACE={self.machine_id._get_volume('source')};",
                f"export PROJECT_NAME={self.project_name};",
            ] + cmd
        )
        return {
            'type': 'ir.actions.act_url',
            'url': shell_url,
            'target': 'new'
        }

    def pgcli(self):
        return self._shell_url(["odoo", "pgcli"])

    def open_odoo_shell(self):
        return self._shell_url(["odoo", "shell"])

    def debug_webcontainer(self):
        return self._shell_url(["odoo", "debug", "odoo"])

    def open_shell(self):
        return self._shell_url(["odoo", "ps"])