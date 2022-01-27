import traceback
import time
import arrow
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError

class CicdTestRun(models.Model):
    _name = 'cicd.test.run'
    _order = 'date desc'

    name = fields.Char(compute="_compute_name")
    date = fields.Datetime("Date", default=lambda self: fields.Datetime.now(), required=True)
    commit_id = fields.Many2one("cicd.git.commit", "Commit", required=True)
    commit_id_short = fields.Char(related="commit_id.short", store=True)
    branch_id = fields.Many2one('cicd.git.branch', string="Initiating branch", required=True)
    branch_id_name = fields.Char(related='branch_id.name', store=False)
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

    def _wait_for_postgres(self, shell):
        timeout = 60
        deadline = arrow.get().shift(seconds=timeout)

        while True:
            try:
                shell.odoo("psql", "--sql", "select * from information_schema.tables;")
            except Exception:
                if arrow.get() < deadline:
                   time.sleep(0.5)
                else:
                    raise
            else:
                break

    # ----------------------------------------------
    # Entrypoint
    # ----------------------------------------------
    def execute(self, shell, task, logsio):
        self.ensure_one()
        b = self.branch_id
        started = arrow.get()

        if not b.any_testing:
            self.success_rate = 100
            self.state = 'success'
            b._compute_state()
            return

        self = self.with_context(testrun=f"_testrun_{self.id}")
        shell.project_name = self.branch_id.project_name # is computed by context
        shell.cwd = shell.cwd.parent / shell.project_name
        self.line_ids = [5]

        logsio.info("Reloading")
        settings = """
RUN_POSTGRES=1
        """
        self.branch_id._reload(shell, task, logsio, project_name=shell.project_name, settings=settings)
        try:
            shell.odoo('build')
            shell.odoo('kill', allow_error=True)
            shell.odoo('rm', allow_error=True)
            logsio.info("Upping postgres...............")
            shell.odoo('up', '-d', 'postgres')
            self._wait_for_postgres(shell)
            logsio.info("DB Reset...........................")
            shell.odoo('-f', 'db', 'reset')
            self._wait_for_postgres(shell)
            logsio.info("Update")
            shell.odoo('update')
            logsio.info("Storing snapshot")
            shell.odoo('snap', 'save', shell.project_name, force=True)
            self._wait_for_postgres(shell)

            if b.run_unittests:
                logsio.info("Running unittests")
                self._run_unit_tests(shell, task, logsio)
                self.env.cr.commit()

            if b.run_robottests:
                logsio.info("Running robot-tests")
                self._run_robot_tests(shell, task, logsio)
                self.env.cr.commit()

            if b.simulate_install_id:
                logsio.info("Running Simulating Install")
                self._run_update_db(shell, task, logsio)
                self.env.cr.commit()

            self.duration = (arrow.get() - started).total_seconds()
            logsio.info(f"Duration was {self.duration}")
            self._compute_success_rate()
        finally:
            shell.odoo('kill', allow_error=True)
            shell.odoo('rm', force=True, allow_error=True)
            shell.odoo('down', "-v", force=True, allow_error=True)
            shell.rmifexists(shell.cwd)


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
        self.branch_id._make_task("_run_tests", silent=True, update_state=True, testrun_id=self.id)

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
            self._wait_for_postgres(shell)
            task.dump_used = self.branch_id.dump_id.name
            shell.odoo('-f', 'restore', 'odoo-db', self.branch_id.dump_id.name)
            self._wait_for_postgres(shell)
            shell.odoo('update')
            self._wait_for_postgres(shell)

        self._generic_run(
            shell, logsio, [None], 
            'emptydb', _x
        )

    def _run_robot_tests(self, shell, tasks, logsio, **kwargs):
        files = shell.odoo('list-robot-test-files').output.strip()
        files = list(filter(bool, files.split("!!!")[1].split("\n")))

        shell.odoo('build')
        def _x(item):
            shell.odoo("snap", "restore", shell.project_name)
            self._wait_for_postgres(shell)
            shell.odoo('robot', item)

        self._generic_run(
            shell, logsio, files, 
            'robottest', _x,
        )

    def _run_unit_tests(self, shell, tasks, logsio, **kwargs):
        files = shell.odoo('list-unit-test-files').output.strip()
        files = list(filter(bool, files.split("!!!")[1].split("\n")))

        shell.odoo("snap", "restore", shell.project_name)
        self._wait_for_postgres(shell)

        self._generic_run(
            shell, logsio, files, 
            'unittest',
            lambda item: shell.odoo('unittest', item)
        )

    def _generic_run(self, shell, logsio, todo, ttype, execute_run):
        for item in todo:
            started = arrow.get()
            run_record = self.line_ids.create({
                'name': item,
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

    def open_form(self):
        return {
            'name': self.name,
            'view_type': 'form',
            'res_model': self._name,
            'res_id': self.id,
            'views': [(False, 'form')],
            'type': 'ir.actions.act_window',
            'target': 'current',
        }