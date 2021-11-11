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
    repo_short = fields.Char(related="repo_id.short")
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

    def _make_task(self, execute):
        execute = execute.replace("()", "(task, logsio)")
        if self.task_ids.filtered(lambda x: x.state == 'new' and x.name == execute):
            raise ValidationError(_("Task already exists. Not triggered again."))
        self.env['cicd.task'].sudo().create({
            'name': execute,
            'branch_id': self.id
        })
        return True

    def _cron_execute_task(self):
        self.ensure_one()
        tasks = self.task_ids.filtered(lambda x: x.state == 'new')
        if not tasks:
            return
        tasks = tasks[-1]
        tasks.perform()

    def _get_instance_folder(self, machine):
        return machine._get_volume('source') / self.name

    def _checkout_latest(self, machine, logsio):
        instance_folder = self._get_instance_folder(machine)
        with machine._shell() as shell:
            with machine._shellexec(
                cwd=instance_folder,
                logsio=logsio,
                env={
                    "GIT_TERMINAL_PROMPT": "0",
                }

            ) as shell_exec:
                logsio.write_text(f"Updating instance folder {self.name}")

                logsio.write_text(f"Cloning {self.name} to {instance_folder}")
                self.repo_id.clone_repo(machine, instance_folder, logsio)

                logsio.write_text(f"Checking out {self.name}")
                shell_exec.X(["git", "checkout", "-f", self.name])

                logsio.write_text(f"Pulling {self.name}")
                shell_exec.X(["git", "pull"])

                logsio.write_text(f"Clean git")
                shell_exec.X(["git", "clean", "-xdff"])

                logsio.write_text("Updating submodules")
                shell_exec.X(["git", "submodule", "update", "--init", "--force", "--recursive"])

                logsio.write_text("Getting current commit")
                commit = shell_exec.X(["git", "rev-parse", "HEAD"]).output.strip()
                logsio.write_text(commit)

                return str(commit)

    # *************************************************************8
    # Button Actions
    # *************************************************************8
    def reload_and_restart(self):
        self.ensure_one()
        self._make_task("obj._reload_and_restart()")

    def restore_dump(self):
        self.ensure_one()
        self._make_task("obj._restore_dump()")


    # *************************************************************8
    # Worker Scripts
    # *************************************************************8

    def _reload_and_restart(self, task, logsio):
        self._checkout_latest(self.machine_id, logsio)
        instance_folder = self._get_instance_folder(self.machine_id)
        task.dump_used = self.dump_id.name
        with self.machine_id._shellexec(
            cwd=instance_folder,
            logsio=logsio,

        ) as shell:
            shell.X(['odoo', '--project-name', self.name, 'reload'])
            shell.X(['odoo', '--project-name', self.name, 'build'])
            shell.X(['odoo', '--project-name', self.name, 'up', '-d'])

    def _restore_dump(self, task, logsio):
        instance_folder = self._get_instance_folder(self.machine_id)
        with self.machine_id._shellexec(
            cwd=instance_folder,
            logsio=logsio) as shell:

            shell.X(['odoo', '--project-name', self.name, 'reload'])
            shell.X(['odoo', '--project-name', self.name, 'build'])
            shell.X(['odoo', '--project-name', self.name, 'down'])
            shell.X([
                'odoo', '--project-name', self.name,
                '-f', 'restore', 'odoo-db',
                self.dump_id.name
            ])
