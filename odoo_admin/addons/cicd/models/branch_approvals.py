from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
class BranchApproval(models.Model):
    _name = 'cicd.branch.approval'

    user_id = fields.Many2one('res.users', string="User", required=True)
    comment = fields.Text("Comment")
    date = fields.Datetime("Date", default=lambda self: fields.Datetime.now())
    branch_id = fields.Many2one('cicd.git.branch', string="Branch", required=True)
    commit_id = fields.Many2one('cicd.git.commit', string="Commit", required=True)
    state = fields.Selection([
        ('ok', 'OK'),
        ('not ok', 'not OK'),
    ], string="OK?", required=True)

    @api.model
    def create(self, vals):
        branch = self.env['cicd.git.branch'].browse(vals['branch_id'])
        vals['commit_id'] = branch.commit_ids.sorted(lambda x: x.date, reverse=True)[0].id
        vals['user_id'] = self.env.user.id
        res = super().create(vals)
        return res

    @api.fieldchange("state")
    def _onchange_state(self):
        for rec in self:
            if rec.state == 'ok':
                if rec.branch_id.state == 'to_approve':
                    rec.branch_id.state == 'approved'
            else:
                if rec.branch_id.state == 'to_approve':
                    rec.branch_id.state == 'rework'

    @api.model
    def create(self, vals):
        res = super().create(vals)
        # res.branch_id.message_post(type="notification", subtype="mt_comment", body="Approval done: " + res.state)
        return res
