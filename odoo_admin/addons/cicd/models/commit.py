from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError

class GitCommit(models.Model):
    _name = 'cicd.git.commit'

    name = fields.Char("SHA", required=True)
    branch_ids = fields.Many2many('cicd.git.branch', string="Repo", required=True)
    date_registered = fields.Datetime("Date registered")
    date = fields.Datetime("Date")

    _sql_constraints = [
        ('name', "unique(name)", _("Only one unique entry allowed.")),
    ]