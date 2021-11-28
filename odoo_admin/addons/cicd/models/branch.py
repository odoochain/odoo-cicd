import arrow
import os
import requests
from odoo import registry
from pathlib import Path
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from ..tools.logsio_writer import LogsIOWriter
from contextlib import contextmanager
import humanize

class GitBranch(models.Model):
    _inherit = ['mail.thread']
    _name = 'cicd.git.branch'

    project_name = fields.Char(compute="_compute_project_name", store=False)
    approver_ids = fields.Many2many("res.users", "cicd_git_branch_approver_rel", "branch_id", "user_id", string="Approver")
    machine_id = fields.Many2one(related='repo_id.machine_id')
    last_access = fields.Datetime("Last Access")
    cycle_down_after_seconds = fields.Integer("Cycle Down After Seconds", default=3600)
    name = fields.Char("Git Branch", required=True)
    date_registered = fields.Datetime("Date registered")
    date = fields.Datetime("Date")
    repo_id = fields.Many2one('cicd.git.repo', string="Repository", required=True)
    repo_short = fields.Char(related="repo_id.short")
    active = fields.Boolean("Active", default=True)
    commit_ids = fields.Many2many('cicd.git.commit', string="Commits")
    task_ids = fields.One2many('cicd.task', 'branch_id', string="Tasks")
    docker_state = fields.Selection([
        ('up', 'Up'),
        ('down', 'Down'),
    ], string="Docker State")
    state = fields.Selection([
        ('new', 'New'),
        ('approved', 'Approved'),
        ('to_deploy', 'To Deploy'),
        ('Live', 'Live'),
    ], string="State", default="new", required=True)
    build_state = fields.Selection([
        ('new', 'New'),
        ('fail', 'Failed'),
        ('done', 'Done'),
        ('building', 'Building'),
    ], default="new", compute="_compute_build_state")
    dump_id = fields.Many2one("cicd.dump", string="Dump")
    db_size = fields.Integer("DB Size Bytes")
    db_size_humanize = fields.Char("DB Size", compute="_compute_human")
    reload_config = fields.Text("Reload Config")
    autobackup = fields.Boolean("Autobackup") # TODO implement
    enduser_summary = fields.Text("Enduser Summary")
    release_ids = fields.One2many("cicd.release", "branch_id", string="Releases")

    run_unittests = fields.Boolean("Run Unittests", default=False)
    run_robottests = fields.Boolean("Run Robot-Tests", default=False)
    simulate_empty_install = fields.Boolean("Simulate Empty Install")
    simulate_install_id = fields.Many2one("cicd.dump", string="Simulate Install")

    test_run_ids = fields.Many2many('cicd.test.run', string="Test Runs", compute="_compute_test_runs")
    after_code_review = fields.Selection([
        ('deploy', "Deploy"),
        ('return', "Return to Sender"),
    ], string="After Code Review", default="deploy", required=True)

    _sql_constraints = [
        ('name_repo_id_unique', "unique(name, repo_id)", _("Only one unique entry allowed.")),
    ]

    def _compute_test_runs(self):
        for rec in self:
            rec.test_run_ids = rec.mapped('commit_ids.test_run_ids')

    @api.depends("db_size")
    def _compute_human(self):
        for rec in self:
            rec.db_size_humanize = humanize.naturalsize(rec.db_size)

    @api.depends('task_ids', 'task_ids.state')
    def _compute_build_state(self):
        for rec in self:
            if 'new' in rec.mapped('task_ids.state'): 
                rec.build_state = 'building'
            else:
                if rec.task_ids and rec.task_ids[0].state == 'fail':
                    rec.build_state = 'failed'
                elif rec.task_ids and rec.task_ids[0].state == 'done':
                    rec.build_state = 'done'
                else:
                    rec.build_state = 'new'

    def _make_task(self, execute, now=False, machine=None):
        if not now and self.task_ids.filtered(lambda x: x.state == 'new' and x.name == execute):
            raise ValidationError(_("Task already exists. Not triggered again."))
        task = self.env['cicd.task'].sudo().create({
            'model': self._name,
            'res_id': self.id,
            'name': execute,
            'branch_id': self.id,
            'machine_id': (machine and machine.id) or self.machine_id.id,
        })
        task.perform(now=now)
        return True

    @api.model
    def _cron_update_docker_states(self):
        self.search([])._docker_get_state()

    def _cron_execute_task(self):
        self.ensure_one()
        tasks = self.task_ids.filtered(lambda x: x.state == 'new')
        if not tasks:
            return
        tasks = tasks[-1]
        tasks.perform()

    def _get_instance_folder(self, machine):
        return machine._get_volume('source') / self.name

    @contextmanager
    def _shellexec(self, task, logsio, cwd=None):
        instance_folder = self._get_instance_folder(task.machine_id)
        with self.machine_id._shellexec(
            cwd=cwd or instance_folder,
            logsio=logsio,
            project_name=self.project_name,
        ) as shell:
            yield shell

    def make_instance_ready_to_login(self):
        def test_request():
            try:
                response = requests.get("http://" + self._get_odoo_proxy_container_name() + "/web/login")
            except requests.exceptions.ConnectionError:
                return False

            return response.status_code == 200

        if not test_request():
            if self.task_ids.filtered(lambda x: not x.is_done):
                raise ValidationError(_("Instance did not respond. Undone task exists. Please retry later!"))

            self._make_task("_reload_and_restart", now=True)
            if not test_request():
                raise ValidationError(_("Instance did not respond. It was tried to start the application but this did not succeed. Please check task logs."))

    def _get_odoo_proxy_container_name(self): 
        return f"{self.project_name}_proxy"


    def _compute_project_name(self):
        for rec in self:
            rec.project_name = os.environ['CICD_PROJECT_NAME'] + "_" + rec.repo_id.short + "_" + rec.name

    def _get_new_logsio_instance(self, source):
        self.ensure_one()
        rolling_file = LogsIOWriter(f"{self.project_name}", source)
        rolling_file.write_text(f"Started: {arrow.get()}")
        return rolling_file
