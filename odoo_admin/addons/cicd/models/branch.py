from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
class GitBranch(models.Model):
    _name = 'cicd.git.branch'

    name = fields.Char("Git Branch")