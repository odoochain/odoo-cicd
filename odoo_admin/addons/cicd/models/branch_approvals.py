from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
class BranchApproval(models.Model):
    _name = 'cicd.branch.approval'

    user_id = fields.Many2one('res.users', string="User", required=True)
    comment = fields.Text("Comment")
    branch_id = fields.Many2one('cicd.branch', string="Branch", required=True)
    commit_id = fields.Many2one('cicd.git.commit', string="Commit", required=True)
    state = fields.Selection([
        ('ok', 'OK'),
        ('not ok', 'not OK'),
    ], string="OK?", required=True)

    @api.fieldchange("state")
    def _onchange_state(self):
        for rec in self:
            if rec.state == 'ok':
                if rec.branch_id.state == 'to_approve':
                    rec.branch_id.state == 'approved'
            else:
                if rec.branch_id.state == 'to_approve':
                    rec.branch_id.state == 'rework'
