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
    machine_id = fields.Many2one("cicd.machine", string="Machine", required=True)

    _sql_constraints = [
        ('name_machine_unique', "unique(name, machine_id)", _("Only one unique entry allowed.")),
    ]

    # def unlink(self):
    #     for rec in self:
    #         with self.machine_id._shell() as shell:
    #             if shell.exists(rec.name):
    #                 shell.unlink(rec.name)

    #     return super().unlink()

    def delete_db(self):
        for rec in self:
            with self._get_conn(rec.machine_id) as cr:
                # requires postgres >= 13
                cr.execute("drop database %s WITH (FORCE);", (rec.name,))
            rec.sudo().unlink()


    @contextmanager
    @api.model
    def _get_conn(self, machine):
        conn = psycopg2.connect(
            user=machine.db_user,
            host=machine.db_host,
            port=machine.db_port,
            password=machine.db_pwd,
            dbname='postgres',
        )
        try:
            try:
                cr = conn.cursor()
                yield cr
                conn.commit()
            except:
                conn.rollback()
        finally:
            conn.close()


    @api.model
    def _cron_update(self):
        for machine in self.env['cicd.machine'].sudo().search([]):
            self._update_dumps(machine)

    def _update_dbs(self, machine):
        with self._get_conn(machine) as cr:
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
                db_db = machine.database_ids.filtered(lambda x: x.name == dbname)
                if not db_db:
                    db_db = machine.database_ids.sudo().create({
                        'machine_id': machine.id,
                        'name': dbname
                    })
                db_db.size = dbsize

            for db in machine.database_ids:
                if db.name not in all_dbs:
                    db.sudo().unlink()