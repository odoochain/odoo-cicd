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
    commit_id = fields.Many2one("cicd.git.commit", "Commit")
    branch_ids = fields.Many2many('cicd.git.branch', related="commit_id.branch_ids")
    result = fields.Selection([
        ('open', 'Open'),
        ('success', 'Success'),
        ('failed', 'Failed'),
    ], string="Result")

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