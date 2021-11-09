import shutil
import os
import git
from git import Repo
from odoo import registry
import subprocess
from pathlib import Path
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
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
        if self.task_ids.filtered(lambda x: x.state == 'new' and x.name == execute):
            raise ValidationError(_("Task already exists. Not triggered again."))
        execute = execute.replace("()", "(task, logsio)")
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
        log = self.machine_id._execute_shell([
            'odoo', '--project-name', self.name, 'reload',
        ])
        log += self.machine_id._execute_shell([
            'odoo', '--project-name', self.name, 'build',
        ])
        log += self.machine_id._execute_shell([
            'odoo', '--project-name', self.name, 'down',
        ])
        log += self.machine_id._execute_shell([
            'odoo', '--project-name', self.name, '-f', 'restore', 'odoo-db', self.dump_id.name
        ])

    def _reload_and_restart(self, task, logsio):
        self._checkout_latest(logsio)
        task.dump_used = self.dump_id.name
        log = self.machine_id._execute_shell([
            'odoo', '--project-name', self.name, 'reload',
        ])
        log += self.machine_id._execute_shell([
            'odoo', '--project-name', self.name, 'build',
        ])
        log += self.machine_id._execute_shell([
            'odoo', '--project-name', self.name, 'up', '-d',
        ])
        task.log = log
        
    def _checkout_latest(self, logsio):
        from . import WORKSPACE
        instance_folder = Path(WORKSPACE / self.name)
        tries = 0
        while tries < 3:
            try:
                tries += 1
                logsio.write_text(f"Updating instance folder {self.name}")
                logsio.write_text(f"Cloning {self.name} {self.url} to {instance_folder}")
                repo = self.clone_repo(instance_folder)
                logsio.write_text(f"Checking out {self.branch}")
                repo.git.checkout(self.branch, force=True)
                logsio.write_text(f"Pulling {self.branch}")
                repo.git.pull()
                logsio.write_text(f"Clean git")
                run = subprocess.run(
                    ["git", "clean", "-xdff"],
                    capture_output=True,
                    cwd=instance_folder,
                    env=dict(os.environ, GIT_TERMINAL_PROMPT="0")
                    )

                run = subprocess.run(
                    ["git", "submodule", "update", "--init", "--force", "--recursive"],
                    capture_output=True,
                    cwd=instance_folder,
                    env=dict(os.environ, GIT_TERMINAL_PROMPT="0")
                    )
                if run.returncode:
                    msg = run.stdout.decode('utf-8') + "\n" + run.stderr.decode('utf-8')
                    logsio.write_text(rolling_file, msg)
                    raise Exception(msg)
                commit = repo.refs[self.anme].commit
                user_id = self.machine_id._get_sshuser_id()
                logsio.write_text(f"Setting access rights in {instance_folder} to {user_id}")
                subprocess.check_call(["/usr/bin/chown", f"{user_id}:{user_id}", "-R", str(instance_folder)])
                return str(commit)

            except Exception as ex:
                if tries < 3:
                    logsio.write_text(str(ex))
                    logsio.warn(ex)
                    logsio.write_text(f"Retrying update instance folder for {branch}")
                    if instance_folder.exists():
                        shutil.rmtree(instance_folder)
                else:
                    raise
