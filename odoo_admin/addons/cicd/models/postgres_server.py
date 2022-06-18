import psycopg2
from pathlib import Path
import json
from . import pg_advisory_xact_lock
from odoo import _, api, fields, models
from odoo import SUPERUSER_ID
from ..tools.tools import get_host_ip
from contextlib import contextmanager, closing
from odoo import registry
import logging

_logger = logging.getLogger(__name__)


class PostgresServer(models.Model):
    _inherit = ["cicd.mixin.size"]
    _name = "cicd.postgres"

    name = fields.Char("Name")

    db_host = fields.Char("DB Host", default="cicd_postgres", required=True)
    db_user = fields.Char("DB User", default="cicd", required=True)
    db_pwd = fields.Char("DB Password", default="cicd_is_cool", required=True)
    db_port = fields.Integer("DB Port", default=5432, required=True)
    database_ids = fields.One2many("cicd.database", "server_id", string="Databases")
    size = fields.Float()
    ttype = fields.Selection(
        [
            ("production", "Production"),
            ("dev", "Dev"),
        ],
        string="Type",
        required=True,
    )

    def _compute_size(self):
        for rec in self:
            size = sum(rec.mapped("database_ids.size"))
            if size != rec.size:
                rec.size = size

    @api.model
    def default_get(self, fields):
        res = super().default_get(fields)
        res["db_host"] = get_host_ip()
        return res

    @contextmanager
    @api.model
    def _get_conn(self):
        with self._extra_env() as self:
            params = {
                "user": self.db_user,
                "host": self.db_host,
                "port": self.db_port,
                "password": self.db_pwd,
                "dbname": "postgres",
                "connect_timeout": 5,
                "options": "-c statement_timeout=10000",
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
                identity_key=(f"store-db-sizes-in-jsonfile-{rec.id}")
            )._store_db_sizes_in_file()

        self.search([]).with_delay(identity_key="update_databases").update_databases()

    @property
    def filedbsizes(self):
        return Path(f"/opt/out_dir/dbsizes.{self.id}")

    def _store_db_sizes_in_file(self):
        for rec in self:
            with rec._get_conn() as cr:
                cr.execute(
                    """
                    SELECT datname, pg_database_size(datname)
                    FROM pg_database
                    WHERE datistemplate = false
                    AND datname not in ('postgres');
                """
                )
                dbs = [{"db": x[0], "size": x[1]} for x in cr.fetchall()]
                rec.filedbsizes.write_text(json.dumps(dbs, indent=4))

    def update_databases(self):
        self.ensure_one()
        for rec in self:
            if not rec.filedbsizes.exists():
                continue
            try:
                dbs = json.loads(rec.filedbsizes.read_text())
            except json.decoder.JSONDecodeError:
                # file written in that moment ignore
                continue

            with rec._singleton("update_databases"):
                all_dbs = list(map(lambda x: x["db"], dbs))
                for db in dbs:
                    dbname, dbsize = db["db"], db["size"]
                    rec.env.cr.commit()
                    db = self.env["cicd.database"].search(
                        [("server_id", "=", rec.id), ("name", "=", dbname)]
                    )

                    if not db:
                        db = db.sudo().create(
                            {
                                "server_id": rec.id,
                                "name": dbname,
                                "size": dbsize,
                            }
                        )
                    else:
                        if db.size != dbsize:
                            db.size = dbsize
                    rec.env.cr.commit()

                for db in self.env["cicd.database"].search(
                    [("server_id", "=", rec.id)]
                ):
                    if db.name not in all_dbs:
                        db.sudo().unlink()
                        rec.env.cr.commit()

                rec._compute_size()
