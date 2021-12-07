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
    server_id = fields.Many2one("cicd.postgres", string="Postgres")

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

    def _update_dbs(self, machine):
        with self.server_id._get_conn() as cr:
            cr.execute("""
                SELECT datname, pg_database_size(datname)
                FROM pg_database
                WHERE datistemplate = false
                AND datname not in ('postgres');
            """)
            dbs = cr.fetchall()
            all_dbs = set()
            for db in dbs:
                dbname = db[0]
                dbsize = db[1]
                all_dbs.add(dbname)
                db_db = machine.database_ids.sudo().filtered(lambda x: x.name == dbname)
                if not db_db:
                    db_db = machine.database_ids.sudo().create({
                        'machine_id': machine.id,
                        'name': dbname
                    })
                db_db.size = dbsize

            for db in machine.database_ids:
                if db.name not in all_dbs:
                    db.sudo().unlink()