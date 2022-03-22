from contextlib import contextmanager
import os
import psycopg2
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from pathlib import Path
from contextlib import contextmanager, closing
from odoo import registry

class Database(models.Model):
    _inherit = ['cicd.mixin.size']
    _name = 'cicd.database'
    _rec_name = 'display_name'

    name = fields.Char("Name", required=True)
    display_name = fields.Char("Name", compute="_compute_display_name", store=True)
    server_id = fields.Many2one("cicd.postgres", string="Postgres", required=True)
    machine_id = fields.Many2one('cicd.machine', compute="_compute_machine")

    _sql_constraints = [
        ('name_postgres_unique', "unique(name, server_id)", _("Only one unique entry allowed.")),
    ]

    @api.depends("name", "size_human")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f"{rec.name} [{rec.size_human}]"

    def delete_db(self):
        for rec in self:
            with self.server_id._get_conn() as cr:
                # requires postgres >= 13
                cr.execute("drop database %s WITH (FORCE);", (rec.name,))
            rec.sudo().unlink()

    @api.model
    def _cron_update(self):
        for machine in self.env['cicd.machine'].sudo().search([]):
            self.env['base'].flush()
            self.env.cr.commit()

            machin._update_dumps(machine)

    def _compute_machine(self):
        for rec in self:
            machines = self.env['cicd.machine'].search([('postgres_server_id', '=', rec.server_id.id)])
            rec.machine_id = machines[0] if machines else False