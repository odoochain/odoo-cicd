from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError

class Registry(models.Model):
    _name = 'cicd.registry'

    name = fields.Char("Name")
    host = fields.Char("Host", default="http://registry.server/")
    username = fields.Char("Username")
    password = fields.Char("Password")