from contextlib import contextmanager
import os
import psycopg2
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from pathlib import Path
import humanize
from contextlib import contextmanager

class Database(models.Model):
    _inherit = ['cicd.mixin.size']
    _name = 'cicd.database'

    name = fields.Char("Name", required=True)
    server_id = fields.Many2one("cicd.postgres", string="Postgres", required=True)

    _sql_constraints = [
        ('name_postgres_unique', "unique(name, server_id)", _("Only one unique entry allowed.")),
    ]

    def delete_db(self):
        for rec in self:
            with self.server_id._get_conn() as cr:
                # requires postgres >= 13
                cr.execute("drop database %s WITH (FORCE);", (rec.name,))
            rec.sudo().unlink()


    @api.model
    def _cron_update(self):
        for machine in self.env['cicd.machine'].sudo().search([]):
            self._update_dumps(machine)
