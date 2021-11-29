import os
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from ..tools import _get_shell_url

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
        self._make_task("_dump")

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
            'url': '/start/' + self.project_name,
            'target': 'self'
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

    def debug_webcontainer(self):
        self.ensure_one()
        logsio = self._get_new_logsio_instance("debugging")

        dest_folder = self.machine_id._get_volume('source') / self.project_name
        with self.machine_id._shellexec(dest_folder, logsio=logsio, project_name=self.project_name) as shell:
            logsio.info("Killing odoo web containers")
            shell.odoo("kill", "odoo")
            shell.odoo("kill", "odoo_debug")

            shell_url = _get_shell_url([
                "cd", f"/{os.environ['WEBSSH_CICD_WORKSPACE']}/{self.project_name}", ";",
                "/usr/bin/python3",  "/opt/odoo/odoo", "-f", "--project-name", self.project_name, "debug", "odoo", "--command", "/odoolib/debug.py",
            ])
            return {
                'type': 'ir.actions.act_url',
                'url': shell_url,
                'target': 'new'
            }

    def pgcli(self):
        import pudb;pudb.set_trace()
        shell_url = _get_shell_url([
            "cd", f"/{os.environ['WEBSSH_CICD_WORKSPACE']}/{self.project_name}", ";",
            "/usr/bin/python3",  "/opt/odoo/odoo", "-f", "--project-name", self.project_name, "pgcli",
            "--host", os.environ['DB_HOST'],
            "--user", os.environ['DB_USER'],
            "--password", os.environ['DB_PASSWORD'],
            "--port", os.environ['DB_PORT'],
        ])
        return {
            'type': 'ir.actions.act_url',
            'url': shell_url,
            'target': 'new'
        }

    def shell_instance(self):
        # kill existing container and start odoo with debug command
        def _get_shell_url(command):
            pwd = base64.encodestring('odoo'.encode('utf-8')).decode('utf-8')
            shellurl = f"/console/?encoding=utf-8&term=xterm-256color&hostname=127.0.0.1&username=root&password={pwd}&command="
            shellurl += ' '.join(command)
            return shellurl

        containers = docker.containers.list(all=True, filters={'name': [name]})
        containers = [x for x in containers if x.name == name]
        shell_url = _get_shell_url([
            "cd", f"/{os.environ['WEBSSH_CICD_WORKSPACE']}/{site_name}", ";",
            "/usr/bin/python3",  "/opt/odoo/odoo", "-f", "--project-name", site_name, "debug", "odoo_debug", "--command", "/odoolib/shell.py",
        ])
        # TODO make safe; no harm on system, probably with ssh authorized_keys

        return {
            'type': 'ir.actions.act_url',
            'url': 'shell_url',
            'target': 'self'
        }

