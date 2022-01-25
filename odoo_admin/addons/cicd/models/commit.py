from odoo import _, api, fields, models, SUPERUSER_ID, tools
import spur
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from ..tools.logsio_writer import LogsIOWriter

class GitCommit(models.Model):
    _inherit = ['mail.thread']
    _name = 'cicd.git.commit'
    _order = 'date desc'

    short = fields.Char(compute="_compute_short")
    name = fields.Char("SHA", required=True)
    branch_ids = fields.Many2many('cicd.git.branch', string="Repo", required=True)
    date_registered = fields.Datetime("Date registered")
    date = fields.Datetime("Date")
    author = fields.Char("Author")
    text = fields.Text("Text")
    test_run_ids = fields.One2many('cicd.test.run', 'commit_id', string="Test Runs")
    test_state = fields.Selection([
        ('success', 'Success'),
        ('failed', 'Failed'),
    ], compute="_compute_test_state", track_visibility="onchange", string="Test State")
    approval_state = fields.Selection([
        ('check', "Check"),
        ('approved', 'Approved'),
        ('declined', 'Declined'),
    ], track_visibility="onchange", string="Approval")
    force_approved = fields.Boolean("Force Approved", track_visibility="onchange")

    _sql_constraints = [
        ('name', "unique(name)", _("Only one unique entry allowed.")),
    ]

    def _compute_short(self):
        for rec in self:
            rec.short = rec.name[:8]

    def _event_new_test_state(self, new_state):
        pass # implement!

    def set_to_check(self):
        self.approval_state = 'check'

    def set_approved(self):
        self.approval_state = 'approved'

    def set_declined(self):
        self.approval_state = 'declined'

    @api.recordchange('force_approved')
    def _force_approved_changed(self):
        for rec in self:
            if rec.force_approved:
                if rec.approval_state != 'approved':
                    rec.approval_state = 'approved'

    @api.depends('test_run_ids', 'test_run_ids.state')
    def _compute_test_state(self):
        for rec in self:
            testruns = rec.test_run_ids.sorted(lambda x: x.id, reverse=True)
            if not testruns or testruns[0].state == 'open':
                rec.test_state = False
                continue
            new_state = testruns[0].state
            if new_state != rec.test_state:
                rec.test_state = new_state
                rec._event_new_test_state(new_state)

    def run_tests(self, filtered=None):
        for ttype in self.env['cicd.test.run']._get_types(filtered):
            # run tests on machine
            raise NotImplementedError("Need machine to run")

    @api.model
    def create(self, vals):
        res = super().create(vals)
        self._evaluate_message()
        return res

    def _evaluate_message(self):
        for rec in self:
            if "REVIEW" in rec.text:
                rec.approval_state = 'check'

    def open_window(self):
        return {
            'view_type': 'form',
            'res_model': 'cicd.git.commit',
            'res_id': self.id,
            'views': [(False, 'form')],
            'type': 'ir.actions.act_window',
            'target': 'current',
        }

    @tools.ormcache('self.id', 'commit')
    def contains_commit(self, commit):
        self.ensure_one()
        repo = self.mapped('branch_ids.repo_id')

        logsio = LogsIOWriter("contains_commit", "Check")

        repo_path = repo._get_main_repo(logsio=logsio, machine=repo.machine_id)
        with repo.machine_id._shellexec(repo_path, logsio=logsio) as shell:
            try:
                test = shell.X(['git', 'merge-base', commit.name, self.name], allow_error=False)  # order seems to be irrelevant
            except spur.results.RunProcessError as ex:
                if 'fatal: Not a valid commit name' in ex.stderr_output:
                    return False
                else:
                    raise
            return not test.return_code