import arrow
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError

class CicdTestRun(models.Model):
    _name = 'cicd.test.run'
    _order = 'date desc'

    name = fields.Char(compute="_compute_name")
    date = fields.Datetime("Date", default=lambda self: fields.Datetime.now(), required=True)
    commit_id = fields.Many2one("cicd.git.commit", "Commit", required=True)
    branch_ids = fields.Many2many('cicd.git.branch', related="commit_id.branch_ids")
    repo_short = fields.Char(related="branch_ids.repo_id.short")
    state = fields.Selection([
        ('open', 'Open'),
        ('success', 'Success'),
        ('failed', 'Failed'),
    ], string="Result", store=True, compute="_compute_success_rate", required=True)
    success_rate = fields.Integer("Success Rate [%]", compute="_compute_success_rate")
    line_ids = fields.One2many('cicd.test.run.line', 'run_id', string="Lines")

    @api.depends('line_ids', 'line_ids.state')
    def _compute_success_rate(self):
        for rec in self:
            if 'failed' in rec.mapped('line_ids.state'):
                rec.state = 'failed'
            elif all(x == 'success' for x in rec.mapped('line_ids.state')) and rec.line_ids:
                rec.state = 'success'
            else:
                rec.state = 'open'
            if not self.line_ids:
                self.success_rate = 0
            else:
                rec.success_rate = int(100 / float(len(self.line_ids)) * float(self.line_ids.filtered(lambda x: x.state == 'success')))

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

    def execute(self, shell, task, logsio):
        self.ensure_one()
        b = self.branch_id
        import pudb;pudb.set_trace()

        test_run_fields = [x for x in b._fields if getattr(x, 'test_run_fields', False)]
        if not any(b[f] for f in test_run_fields):
            return

        if b.simulate_install_id or b.simulate_empty_install:
            b._create_empty_db(shell, task, logsio)

        if b.run_unittests:
            started = arrow.get()
            logsio.info(f"Starting Unittests")
            self._run_unittests()
            logsio.info(f"Unittests done after {(arrow.get() - started).total_seconds()}")

        if b.run_robottests:
            started = arrow.get()
            logsio.info(f"Starting Robot-Tests")
            self._run_robot_tests()
            logsio.info(f"Unittests done after {(arrow.get() - started).total_seconds()}")

        if b.simulate_install_id:
            started = arrow.get()
            logsio.info(f"Restoring {self.dump_id.name}")
            task.dump_used = self.dump_id.name
            shell.odoo('-f', 'restore', 'odoo-db', self.dump_id.name)
            shell.odoo('update')
            logsio.info(f"Tested Migrations done after {(arrow.get() - started).total_seconds()}")

    def _run_robot_tests(self, shell, tasks, logsio, **kwargs):
        raise NotImplementedError("get all tests and make lines")
        shell.odoo('robot', '-a')

    def _run_unit_tests(self, shell, tasks, logsio, **kwargs):
        raise NotImplementedError("get all tests and make lines")
        shell.odoo('run-tests')
        


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