from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError

class GitCommit(models.Model):
    _inherit = ['mail.thread']
    _name = 'cicd.git.commit'
    _order = 'date desc'

    name = fields.Char("SHA", required=True)
    branch_ids = fields.Many2many('cicd.git.branch', string="Repo", required=True)
    date_registered = fields.Datetime("Date registered")
    date = fields.Datetime("Date")
    author = fields.Char("Author")
    text = fields.Text("Text")
    test_run_ids = fields.Many2many('cicd.test.run', string="Test Runs", store=True)
    test_state = fields.Selection([
        ('success', 'Success'),
        ('failed', 'Failed'),
    ])# TODO undo, compute="_compute_test_state")
    approval_state = fields.Selection([
        ('approved', 'Approved'),
        ('declined', 'Declined'),
    ])
    force_approved = fields.Boolean("Force Approved")

    _sql_constraints = [
        ('name', "unique(name)", _("Only one unique entry allowed.")),
    ]

    @api.depends('test_run_ids', 'test_run_ids.state')
    def _compute_test_state(self):
        for rec in self:
            testruns = rec.test_run_ids.sorted(lambda x: x.id)
            if not testruns or testruns[0].state == 'open':
                rec.test_state = False
                continue
            rec.test_state = testruns[0].state

    def run_tests(self, filtered=None):
        for ttype in self.env['cicd.test.run']._get_types(filtered):
            # run tests on machine
            raise NotImplementedError("Need machine to run")

    @api.model
    def create(self, vals):
        import pudb;pudb.set_trace()
        res = super().create(vals)
        self._evaluate_message()
        return res

    def _evaluate_message(self):
        for rec in self:
            if "REVIEW" in rec.text:
                rec.branch_ids.set_state('to_review')
        pass

    def open_window(self):
        return {
            'view_type': 'form',
            'res_model': 'cicd.git.commit',
            'res_id': self.id,
            'views': [(False, 'form')],
            'type': 'ir.actions.act_window',
            'target': 'current',
        }