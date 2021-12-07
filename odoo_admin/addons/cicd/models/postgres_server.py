from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from ..tools.tools import get_host_ip

class PostgresServer(models.Model):
    _name = 'cicd.postgres'

    name = fields.Char("Name")

    db_host = fields.Char("DB Host", default="cicd_postgres")
    db_user = fields.Char("DB User", default="cicd")
    db_pwd = fields.Char("DB Password", default="cicd_is_cool")
    db_port = fields.Integer("DB Port", default=5432)
    database_ids = fields.One2many('cicd.database', 'machine_id', string="Databases")

    def update_databases(self):
        self.env['cicd.database']._update_dbs(self)

    @api.model
    def default_get(self, fields):
        res = super().default_get(fields)
        res['db_host'] = get_host_ip()
        return res