from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
class CicdTestRun(models.Model):
    _name = 'cicd.test.run'

    ttype = fields.Selection([
        ('plain_update', "Plain Update"),
        ('update_existing', 'Update Existing'),
        ('unit-tests-', 'Unit-Tests'),
        ('robot-tests', 'Robot-Tests'),
    ], string="Type", store=True)
    name = fields.Char(compute="_compute_name")
    date = fields.Datetime("Date")
    commit_id = fields.Many2one("cicd.git.commit", "Commit")
    branch_ids = fields.Many2many('cicd.git.branch', related="commit_id.branch_ids")
    repo_short = fields.Char(related="branch_ids.repo_id.short")
    result = fields.Selection([
        ('open', 'Open'),
        ('success', 'Success'),
        ('failed', 'Failed'),
    ], string="Result")
    line_ids = fields.One2many('cicd.test.run.line', 'run_id', string="Lines")

    @api.constrains('branch_ids')
    def _check_branches(self):
        for rec in self:
            if not rec.branch_ids:
                continue
            if not all(x.repo_id == rec.branch_ids[0].repo_id for x in rec.branch_ids):
                raise ValidationError("Branches must be of the same repository.")

    def _compute_name(self):
        for rec in self:
            rec.name = f"{rec.create_date} - {rec.commit_id.name} - {rec.ttype}"

    @api.model
    def _get_ttypes(self, filtered):
        for x in self._fields['ttype'].selection:
            if filtered:
                if x[0] not in filtered:
                    continue
            yield x[0]


class CicdTestRun(models.Model):
    _name = 'cicd.test.run.line'

    ttype = fields.Selection([
        ('unittest', 'Unit-Test'),
        ('robottest', 'Robot-Test'),
        ('migration', 'Migration'),
    ], string="Category")
    name = fields.Char("Name")
    run_id = fields.Many2one('cicd.test.run', string="Run")
    state = fields.Selection([
        ('open', 'Open'),
        ('success', 'Success'),
        ('failed', 'Failed'),
    ])