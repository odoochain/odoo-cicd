from odoo import api, SUPERUSER_ID
from odoo import registry
from odoo import fields

def migrate(cr, version):
    # try nicht unbedingt notwendig; bei __exit__ wird ein close aufgerufen
    db_registry = registry(cr.dbname)
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['cicd.git.branch'].search([]).write({
        'last_access': fields.Datetime.now()
    })