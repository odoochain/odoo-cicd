from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
class Repository(models.Model):
    _name = 'cicd.git.repo'

    name = fields.Char("URL", required=True)
    _sql_constraints = [
        ('name_unique', "unique(named)", _("Only one unique entry allowed.")),
    ]