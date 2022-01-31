import psycopg2
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from ..tools.tools import get_host_ip
from contextlib import contextmanager
import humanize

class PostgresServer(models.Model):
    _inherit = ['cicd.mixin.size']
    _name = 'cicd.postgres'

    name = fields.Char("Name")

    db_host = fields.Char("DB Host", default="cicd_postgres", required=True)
    db_user = fields.Char("DB User", default="cicd", required=True)
    db_pwd = fields.Char("DB Password", default="cicd_is_cool", required=True)
    db_port = fields.Integer("DB Port", default=5432, required=True)
    database_ids = fields.One2many('cicd.database', 'server_id', string="Databases")
    size = fields.Float(compute="_compute_size")
    ttype = fields.Selection([
        ('production', "Production"),
        ('dev', 'Dev'),
    ], string="Type", required=True)

    @api.depends("database_ids", "database_ids.size")
    def _compute_size(self):
        for rec in self:
            self.size = sum(rec.mapped('database_ids.size'))

    @api.model
    def default_get(self, fields):
        res = super().default_get(fields)
        res['db_host'] = get_host_ip()
        return res

    @contextmanager
    @api.model
    def _get_conn(self):
        conn = psycopg2.connect(
            user=self.db_user,
            host=self.db_host,
            port=self.db_port,
            password=self.db_pwd,
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

    def update_databases(self):
        with self._get_conn() as cr:
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
                db_db = self.database_ids.sudo().filtered(lambda x: x.name == dbname)
                if not db_db:
                    db_db = self.database_ids.sudo().create({
                        'server_id': self.id,
                        'name': dbname
                    })
                db_db.size = dbsize

            for db in self.database_ids:
                if db.name not in all_dbs:
                    db.sudo().unlink()