from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError

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

    def docker_get_state(self):
        self.ensure_one()
        self._make_task("_docker_get_state", True)

    def dump(self):
        self.ensure_one()
        self._make_task("_dump", True)

    def turn_into_dev(self):
        self.ensure_one()
        self._make_task("_turn_into_dev", True)

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
        self._make_task("_restore_dump")

    def run_robot_tests(self):
        self.ensure_one()
        self._make_task("_run_robot_tests")

    def run_unit_tests(self):
        self.ensure_one()
        self._make_task("_run_unit_tests")
        
    def start(self):
        return {
            'type': 'ir.actions.act_url',
            'url': '/start/' + self.name,
            'target': 'new'
        }

    def update_git_commits(self):
        self.ensure_one()
        self._make_task("_update_git_commits")

    def transform_input_dump(self):
        self.ensure_one()
        self._make_task("_transform_input_dump")

    def open_shell(self):
        self.ensure_one()
        import pudb;pudb.set_trace()

    def debug_webcontainer(self):
        self.ensure_one()
        import pudb;pudb.set_trace()

    def start_webmailer(self):
        self.ensure_one()
        import pudb;pudb.set_trace()