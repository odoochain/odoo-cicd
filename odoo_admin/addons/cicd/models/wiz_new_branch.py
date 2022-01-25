from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
class NewBranch(models.TransientModel):
    _name = 'cicd.git.branch.new'

    repo_id = fields.Many2one('cicd.git.repo', string="Repo")
    source_branch_id = fields.Many2one('cicd.git.branch', string="Clone From", required=True)
    new_name = fields.Char("New Name", required=True)

    @api.constrains("new_name")
    def _check_name(self):
        for rec in self:
            invalid_chars = '(_)/:?!#*\\ '
            for c in invalid_chars:
                if c in rec.new_name:
                    raise ValidationError(_("Invalid Name: " + rec.new_name))

    def ok(self):

