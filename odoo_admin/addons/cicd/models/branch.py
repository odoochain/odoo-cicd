import shutil
import os
import git
from git import Repo
from odoo import registry
import subprocess
from pathlib import Path
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from ..tools.tools import _set_owner
class GitBranch(models.Model):
    _name = 'cicd.git.branch'

    machine_id = fields.Many2one(related='repo_id.machine_id')
    name = fields.Char("Git Branch", required=True)
    date_registered = fields.Datetime("Date registered")
    date = fields.Datetime("Date")
    repo_id = fields.Many2one('cicd.git.repo', string="Repository", required=True)
    active = fields.Boolean("Active", default=True)
    commit_ids = fields.Many2many('cicd.git.commit', string="Commits")
    task_ids = fields.One2many('cicd.task', 'branch_id', string="Tasks")
    state = fields.Selection([
        ('new', 'New'),
        ('approved', 'Approved'),
    ], string="State", default="new", required=True)
    build_state = fields.Selection([
        ('new', 'New'),
        ('fail', 'Failed'),
        ('done', 'Done'),
        ('building', 'Building'),
    ], default="new", compute="_compute_build_state")
    dump_id = fields.Many2one("cicd.dump", string="Dump")

    # autobackup = fields.Boolean("Autobackup")

    _sql_constraints = [
        ('name_repo_id_unique', "unique(name, repo_id)", _("Only one unique entry allowed.")),
    ]

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

    def reload_and_restart(self):
        self.ensure_one()
        self._make_task("obj._reload_and_restart()")

    def restore_dump(self):
        self.ensure_one()
        self._make_task("obj._restore_dump()")

    def _make_task(self, execute):
        execute = execute.replace("()", "(task, logsio)")
        if self.task_ids.filtered(lambda x: x.state == 'new' and x.name == execute):
            raise ValidationError(_("Task already exists. Not triggered again."))
        self.env['cicd.task'].sudo().create({
            'name': execute,
            'branch_id': self.id
        })
        return True

    @api.model
    def create(self, vals):
        res = super().create(vals)
        res.make_cron()
        return res

    def make_cron(self):
        self.ensure_one()
        self.env['cicd.task']._make_cron(
            'branches job', self, '_cron_execute_task', active=self.active
        )

    @api.constrains('active')
    def _onchange_active(self):
        for rec in self:
            rec.make_cron()
                

    def _cron_execute_task(self):
        self.ensure_one()
        tasks = self.task_ids.filtered(lambda x: x.state == 'new')
        if not tasks:
            return
        tasks = tasks[-1]
        tasks.perform()

    def _restore_dump(self, task, logsio):
        instance_folder = self._get_instance_folder(self.machine_id)
        self.machine_id._execute_shell([
            'odoo', '--project-name', self.name, 'reload',
        ], cwd=instance_folder, logsio=logsio)
        self.machine_id._execute_shell([
            'odoo', '--project-name', self.name, 'build',
        ], cwd=instance_folder, logsio=logsio)
        self.machine_id._execute_shell([
            'odoo', '--project-name', self.name, 'down',
        ], cwd=instance_folder, logsio=logsio)
        self.machine_id._execute_shell([
            'odoo', '--project-name', self.name,
            '-f', 'restore', 'odoo-db',
            self.dump_id.name
        ], cwd=instance_folder, logsio=logsio)

    def _get_instance_folder(self, machine):
        return machine._get_volume('source') / self.name

    def _reload_and_restart(self, task, logsio):
        self._checkout_latest(self.machine_id, logsio)
        instance_folder = self._get_instance_folder(self.machine_id)
        task.dump_used = self.dump_id.name
        with self.machine_id._shell() as shell:
            self.machine_id._execute_shell([
                'odoo', '--project-name', self.name, 'reload',
            ], cwd=instance_folder, logsio=logsio)

            self.machine_id._execute_shell([
                'odoo', '--project-name', self.name, 'build',
            ], cwd=instance_folder, logsio=logsio)
            self.machine_id._execute_shell([
                'odoo', '--project-name', self.name, 'up', '-d',
            ], cwd=instance_folder, logsio=logsio)
        
    def _checkout_latest(self, machine, logsio):
        instance_folder = self._get_instance_folder(machine)
        tries = 0
        while tries < 3:
            try:
                tries += 1
                with machine._shell() as shell:
                    logsio.write_text(f"Updating instance folder {self.name}")
                    logsio.write_text(f"Cloning {self.name} to {instance_folder}")
                    self.repo_id.clone_repo(machine, instance_folder, logsio)
                    logsio.write_text(f"Checking out {self.name}")
                    machine._execute_shell(["git", "checkout", "-f", self.name], cwd=instance_folder, logsio=logsio)
                    logsio.write_text(f"Pulling {self.name}")
                    machine._execute_shell(["git", "pull"], cwd=instance_folder, logsio=logsio)
                    logsio.write_text(f"Clean git")
                    machine._execute_shell(["git", "clean", "-xdff"], cwd=instance_folder, env={
                        "GIT_TERMINAL_PROMPT": "0",
                    }, logsio=logsio)
                    machine._execute_shell(["git", "submodule", "update", "--init", "--force", "--recursive"], cwd=instance_folder, env={
                        "GIT_TERMINAL_PROMPT": "0",
                    }, logsio=logsio)
                    commit = machine._execute_shell(["git", "rev-parse", "HEAD"], cwd=instance_folder, logsio=logsio).output.strip()
                    return str(commit)

            except Exception as ex:
                if tries < 3:
                    logsio.write_text(str(ex))
                    logsio.warn(ex)
                    logsio.write_text(f"Retrying update instance folder for {self.name}")
                    if instance_folder.exists():
                        shutil.rmtree(instance_folder)
                else:
                    raise
