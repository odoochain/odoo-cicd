from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
class Approval(models.Model):
    _name = 'cicd.git.approval'

    approver_id = fields.Many2one('res.users', string="Approver", required=True)
    branch_id = fields.Many2one('cicd.git.branch', string="Branch")
    date = fields.Datetime("Date", default=lambda self: fields.Datetime.now())
    commit_id = fields.Many2one('cicd.git.branch', string="Commit")