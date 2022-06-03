import base64
from pathlib import Path
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from .test_run import SETTINGS


class TestSettingsMigrations(models.Model):
    _inherit = "cicd.test.settings.base"
    _name = 'cicd.test.settings.migrations'

    dump_id = fields.Many2one('cicd.dump', string="Dump")

    def get_name(self):
        return f"{self.id} - {self.dump_id.name}"

    def _run_update_db(self, shell, logsio, **kwargs):

        def _update(item):  # NOQA
            logsio.info(f"Restoring {self.branch_id.dump_id.name}")

            shell.odoo('-f', 'restore', 'odoo-db', self.branch_id.dump_id.name)
            shell.wait_for_postgres()
            shell.odoo('update', timeout=self.timeout_migration)
            shell.wait_for_postgres()

        self._generic_run(
            shell, logsio, [None],
            'migration', _update,
        )
