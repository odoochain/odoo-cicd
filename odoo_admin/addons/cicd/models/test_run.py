import traceback
import arrow
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError

class CicdTestRun(models.Model):
    _name = 'cicd.test.run'
    _order = 'date desc'

    name = fields.Char(compute="_compute_name")
    date = fields.Datetime("Date", default=lambda self: fields.Datetime.now(), required=True)
    commit_id = fields.Many2one("cicd.git.commit", "Commit", required=True)
    branch_id = fields.Many2one('cicd.git.branch', string="Initiating branch", required=True)
    branch_ids = fields.Many2many('cicd.git.branch', related="commit_id.branch_ids", string="Branches")
    repo_short = fields.Char(related="branch_ids.repo_id.short")
    state = fields.Selection([
        ('open', 'Open'),
        ('success', 'Success'),
        ('failed', 'Failed'),
    ], string="Result", store=True, compute="_compute_success_rate", required=True, default='open')
    success_rate = fields.Integer("Success Rate [%]", compute="_compute_success_rate")
    line_ids = fields.One2many('cicd.test.run.line', 'run_id', string="Lines")
    duration = fields.Integer("Duration [s]")

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
                rec.success_rate = 0
            else:
                rec.success_rate = int(100 / float(len(self.line_ids)) * float(len(self.line_ids.filtered(lambda x: x.state == 'success'))))

    @api.constrains('branch_ids')
    def _check_branches(self):
        for rec in self:
            if not rec.branch_ids:
                continue
            if not all(x.repo_id == rec.branch_ids[0].repo_id for x in rec.branch_ids):
                raise ValidationError("Branches must be of the same repository.")

    def _compute_name(self):
        for rec in self:
            date = rec.create_date.strftime("%Y-%m-%d %H:%M:%S")[:10]
            rec.name = f"{date} - {rec.branch_id.name}"

    @api.model
    def _get_ttypes(self, filtered):
        for x in self._fields['ttype'].selection:
            if filtered:
                if x[0] not in filtered:
                    continue
            yield x[0]

    def rerun(self):
        if self.branch_id.state not in ['testable', 'tested', 'dev']:
            raise ValidationError(_("State of branch does not all a repeated test run"))
        self = self.sudo()
        self.state = 'open'
        self.branch_id._make_task("_run_tests", silent=True, update_state=True)

    def execute(self, shell, task, logsio):
        self.ensure_one()
        b = self.branch_id
        started = arrow.get()

        if not b.any_testing:
            self.success_rate = 100
            self.state = 'success'
            b._compute_state()
            return

        if b.simulate_install_id or b.simulate_empty_install:
            self._run_create_empty_db(shell, task, logsio)
            self.env.cr.commit()

        if b.run_unittests:
            self._run_unit_tests(shell, task, logsio)

        if b.run_robottests:
            self._run_robot_tests(shell, task, logsio)

        if b.simulate_install_id:
            self._run_update_db(shell, task, logsio)
            self.env.cr.commit()

        self.duration = (arrow.get() - started).total_seconds()
        self._compute_success_rate()

    def _run_create_empty_db(self, shell, task, logsio):
        self._generic_run(
            shell, logsio, [None], 
            'emptydb',
            lambda item: self.branch_id._create_empty_db(shell, task, logsio),
        )

    def _run_update_db(self, shell, task, logsio):

        def _x(item):
            logsio.info(f"Restoring {self.branch_id.dump_id.name}")
            self.branch_id._create_empty_db(shell, task, logsio),
            task.dump_used = self.branch_id.dump_id.name
            shell.odoo('-f', 'restore', 'odoo-db', self.branch_id.dump_id.name)
            shell.odoo('update')

        self._generic_run(
            shell, logsio, [None], 
            'emptydb', _x
        )

    def _run_robot_tests(self, shell, tasks, logsio, **kwargs):
        files = shell.odoo('list-robot-test-files').output.strip()
        files = list(filter(bool, files.split("!!!")[1].split("\n")))
        self._generic_run(
            shell, logsio, files, 
            'robottest',
            lambda item: shell.odoo('robot', item)
        )

    def _run_unit_tests(self, shell, tasks, logsio, **kwargs):
        files = shell.odoo('list-unit-test-files').output.strip()
        files = list(filter(bool, files.split("!!!")[1].split("\n")))
        self._generic_run(
            shell, logsio, files, 
            'unittest',
            lambda item: shell.odoo('unittest', item)
        )

    def _generic_run(self, shell, logsio, todo, ttype, execute_run):
        for item in todo:
            started = arrow.get()
            run_record = self.line_ids.create({
                'name': todo,
                'ttype': ttype, 
                'run_id': self.id
            })
            try:
                logsio.info(f"Running {item}")
                execute_run(item)
            except Exception as ex:
                msg = traceback.format_exc()
                logsio.error(f"Error happened: {msg}")
                run_record.state = 'failed'
                run_record.exc_info = msg
            else:
                run_record.state = 'success'
            end = arrow.get()
            run_record.duration = (end - started).total_seconds()
            self.env.cr.commit()

class CicdTestRun(models.Model):
    _name = 'cicd.test.run.line'

    ttype = fields.Selection([
        ('unittest', 'Unit-Test'),
        ('robottest', 'Robot-Test'),
        ('migration', 'Migration'),
        ('emptydb', 'Migration'),
    ], string="Category")
    name = fields.Char("Name")
    run_id = fields.Many2one('cicd.test.run', string="Run")
    exc_info = fields.Text("Exception Info")
    duration =  fields.Integer("Duration")
    state = fields.Selection([
        ('open', 'Open'),
        ('success', 'Success'),
        ('failed', 'Failed'),
    ], default='open', required=True)