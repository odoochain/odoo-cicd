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
    display_name = fields.Char("Name", compute="_compute_display_name", store=False)
    server_id = fields.Many2one("cicd.postgres", string="Postgres", required=True)
    machine_id = fields.Many2one('cicd.machine', compute="_compute_machine")
    matching_branch_ids = fields.Many2many('cicd.git.branch', string="Matching Branches", compute="_compute_branches")


    _sql_constraints = [
        ('name_postgres_unique', "unique(name, server_id)", _("Only one unique entry allowed.")),
    ]

    @api.depends("name", "size_human")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f"{rec.name} [{rec.size_human}]"

    def delete_db(self):
        for rec in self:
            try:
                raise Exception("be compatible")
                with self.server_id._get_conn() as cr:
                    # requires postgres >= 13
                    cr.execute("drop database %s WITH (FORCE);", (rec.name,))

            except Exception:
                with self.server_id._get_conn() as cr:
                    # requires postgres >= 13
                    cr.execute((
                        f"UPDATE pg_database SET "
                        f"datallowconn = 'false' WHERE datname = '{rec.name}'; \n"
                        f"SELECT pg_terminate_backend(pid) "
                        f"FROM pg_stat_activity WHERE datname = '{rec.name}'; \n"
                    ))
                    cr.connection.autocommit = True
                    cr.execute((
                        f"DROP DATABASE IF EXISTS {rec.name}"
                    ))
            rec.sudo().unlink()

    @api.model
    def _cron_update(self):
        for machine in self.env['cicd.machine'].sudo().search([]):
            self.env['base'].flush()
            self.env.cr.commit()

            machine._update_dumps(machine)

    def _compute_machine(self):
        for rec in self:
            machines = self.env['cicd.machine'].search([('postgres_server_id', '=', rec.server_id.id)])
            rec.machine_id = machines[0] if machines else False

    def _compute_branches(self):
        for rec in self:
            rec.matching_branch_ids = self.env['cicd.git.branch'].search([
                "|",
                ('name', 'ilike', rec.name),
                ('technical_branch_name', 'ilike', rec.name)
            ])