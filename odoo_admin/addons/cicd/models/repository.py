from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
class Repository(models.Model):
    _name = 'cicd.git.repo'

    name = fields.Char("URL")