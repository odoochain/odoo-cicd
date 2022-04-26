import psycopg2
from odoo import _, api, fields, models
from odoo import SUPERUSER_ID
from ..tools.tools import get_host_ip
from contextlib import contextmanager, closing
from odoo import registry
import logging
_logger = logging.getLogger(__name__)


class PostgresServer(models.Model):
    _inherit = ['cicd.mixin.size']
    _name = 'cicd.postgres'

    name = fields.Char("Name")

    db_host = fields.Char("DB Host", default="cicd_postgres", required=True)
    db_user = fields.Char("DB User", default="cicd", required=True)
    db_pwd = fields.Char("DB Password", default="cicd_is_cool", required=True)
    db_port = fields.Integer("DB Port", default=5432, required=True)
    database_ids = fields.One2many('cicd.database', 'server_id', string="Databases")
    size = fields.Float()
    ttype = fields.Selection([
        ('production', "Production"),
        ('dev', 'Dev'),
    ], string="Type", required=True)

    @api.depends("database_ids", "database_ids.size")
    def _compute_size(self):
        for rec in self:
            rec.size = sum(rec.mapped('database_ids.size'))

    @api.model
    def default_get(self, fields):
        res = super().default_get(fields)
        res['db_host'] = get_host_ip()
        return res

    @contextmanager
    @api.model
    def _get_conn(self):
        with self._extra_env() as self:
            params = {
                'user': self.db_user,
                'host': self.db_host,
                'port': self.db_port,
                'password': self.db_pwd,
                'dbname': 'postgres',
                'connect_timeout': 5,
                'options': '-c statement_timeout=10000',
            }

        conn = psycopg2.connect(**params)
        conn.autocommit = True
        try:
            try:
                cr = conn.cursor()
                cr.execute("SET statement_timeout = 30000")
                yield cr
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        finally:
            conn.close()

    @api.model
    def _cron_update_databases(self):
        for rec in self.search([]):
            rec.with_delay(
                identity_key=f"update_databases_{rec.name}-{rec.id}"
            ).update_databases()

    def update_databases(self):
        with self._extra_env() as lock_rec:
            lock_rec.env.cr.execute((
                "select id from cicd_postgres "
                "where id=%s "
                "for update nowait "
            ), (lock_rec.id,))

            self.ensure_one()
            self.env.cr.commit()
            with self._get_conn() as cr:
                cr.execute("""
                    SELECT datname, pg_database_size(datname)
                    FROM pg_database
                    WHERE datistemplate = false
                    AND datname not in ('postgres');
                """)
                dbs = cr.fetchall()
            
            changed = False
            all_dbs = set()
            
            for db in dbs:
                dbname = db[0]
                dbsize = db[1]
                all_dbs.add(dbname)
                self.env.cr.commit()
                with self._extra_env() as x_rec:
                    db_db = x_rec.database_ids.sudo().filtered(
                        lambda x: x.name == dbname)

                    if not db_db:
                        db_db = x_rec.database_ids.sudo().create({
                            'server_id': x_rec.id,
                            'name': dbname,
                            'size': dbsize,
                        })
                        changed = True
                    else:
                        if db_db.size != dbsize:
                            changed = True
                            db_db.size = dbsize
                    x_rec.env.cr.commit()

            for db in self.database_ids:
                if db.name not in all_dbs:
                    db.sudo().unlink()
                    self.env.cr.commit()
                    changed = True
            
            if changed:
                with self._extra_env() as x_rec:
                    x_rec._compute_size()
                    x_rec.env.cr.commit()
