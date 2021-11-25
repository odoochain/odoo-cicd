from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError

class GitCommit(models.Model):
    _name = 'cicd.git.commit'
    _order = 'date desc'

    name = fields.Char("SHA", required=True)
    branch_ids = fields.Many2many('cicd.git.branch', string="Repo", required=True)
    date_registered = fields.Datetime("Date registered")
    date = fields.Datetime("Date")
    author = fields.Char("Author")
    text = fields.Text("Text")
    test_run_ids = fields.Many2many('cicd.test.run', string="Test Runs")

    _sql_constraints = [
        ('name', "unique(name)", _("Only one unique entry allowed.")),
    ]

    def run_tests(self, filtered=None):
        for ttype in self.env['cicd.test.run']._get_types(filtered):
            # run tests on machine
            raise NotImplementedError("Need machine to run")
