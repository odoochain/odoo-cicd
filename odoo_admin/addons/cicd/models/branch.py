from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
class GitBranch(models.Model):
    _name = 'cicd.git.branch'

    name = fields.Char("Git Branch", required=True)
    repo_id = fields.Many2one('cicd.git.repo', string="Repository", required=True)

    _sql_constraints = [
        ('name_repo_id_unique', "unique(name, repo_id)", _("Only one unique entry allowed.")),
        
    ]