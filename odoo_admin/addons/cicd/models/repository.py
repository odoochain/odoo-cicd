import arrow
import os
import shutil
import subprocess
import git
from git import Repo
import random
from contextlib import contextmanager
from pathlib import Path
import tempfile
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from ..tools.logsio_writer import LogsIOWriter
from . import pg_try_advisory_lock
from odoo.addons.queue_job.exception import (
    RetryableJobError,
    JobError,
)
import logging

logger = logging.getLogger(__name__)

class NewBranch(Exception): pass
class Repository(models.Model):
    _name = 'cicd.git.repo'

    short = fields.Char(compute="_compute_shortname", string="Name")
    machine_id = fields.Many2one('cicd.machine', string="Machine", compute="_compute_machine")
    name = fields.Char("URL", required=True)
    login_type = fields.Selection([
        ('username', 'Username'),
        ('key', 'Key'),
    ])
    key = fields.Text("Key")
    username = fields.Char("Username")
    password = fields.Char("Password")
    skip_paths = fields.Char("Skip Paths", help="Comma separated list")
    branch_ids = fields.One2many('cicd.git.branch', 'repo_id', string="Branches")
    url = fields.Char(compute="_compute_url")

    _sql_constraints = [
        ('name_unique', "unique(named)", _("Only one unique entry allowed.")),
        ('url_unique', "unique(url)", _("Only one unique entry allowed.")),
    ]

    def _compute_shortname(self):
        for rec in self:
            rec.short = rec.name.split("/")[-1]

    def _compute_machine(self):
        for rec in self:
            rec.machine_id = self.machine_id.sudo().search([], limit=1)

    def _compute_url(self):
        for rec in self:
            if rec.login_type == 'username':
                url = ""
                for prefix in [
                    'https://',
                    'http://',
                    'ssh://',
                    'ssh+git://'
                ]:
                    if rec.name.startswith(prefix):
                        url = f'{prefix}{rec.username}:{rec.password}@{rec.name[len(prefix):]}'
                rec.url = url
            else:
                rec.url = rec.name

    @contextmanager
    def _get_ssh_command(self):
        self.ensure_one()
        file = Path(tempfile.mktemp(suffix='.'))
        env = {}

        try:
            env['GIT_SSH_COMMAND'] = f'ssh -o StrictHostKeyChecking=no'
            if self.login_type == 'key':
                env['GIT_SSH_COMMAND'] += [f'-i {file}']
                file.write_text(self.key)
            else:
                pass
            yield env
        finally:
            if file.exists():
                file.unlink()


    def _get_main_repo(self, tempfolder=False, destination_folder=False, logsio=None):
        self.ensure_one()
        from . import MAIN_FOLDER_NAME
        path = Path(self.machine_id.workspace) / MAIN_FOLDER_NAME
        self.clone_repo(self.machine_id, path, logsio)


        if destination_folder:
            temppath = destination_folder
        elif tempfolder:
            temppath = tempfile.mktemp()
        else:
            temppath = None
        if temppath:
            subprocess.check_call(['rsync', f"{path}/", f"{temppath}/", "-ar"])
            repo = Repo(temppath)
        return path

    def _get_remotes(self, shell):
        remotes = shell.X(["git", "remote", "-v"]).output.strip().split("\n")
        remotes = [x.split("\t")[0] for x in remotes]
        return list(set(remotes))

    @api.model
    def _clear_branch_name(self, branch):
        branch = branch.strip()

        if "->" in branch:
            branch = branch.split("->")[-1].strip()

        if "* " in branch:
            branch = branch.replace("* ", "")
        return branch

    @api.model
    def _cron_fetch(self):
        for repo in self.search([]):

            self._lock_git()
            logsio = LogsIOWriter(repo.name, 'fetch')
                
            repo_path = repo._get_main_repo(logsio=logsio)

            env = {
                "GIT_ASK_YESNO": "false",
                "GIT_SSH_COMMAND": f'ssh -o StrictHostKeyChecking=no',
                "GIT_TERMINAL_PROMPT": "0",
            }
            with repo.machine_id._shellexec(cwd=repo_path, logsio=logsio, env=env) as shell:
                shell.X(["git", "clean", "-xdff"])
                all_remote_branches = shell.X(["git", "branch", "-r"]).output.strip().split("\n")
                new_commits, updated_branches = {}, set()

                for remote in self._get_remotes(shell):
                    shell.X(["git", "fetch", remote])
                    for branch in all_remote_branches:
                        branch = self._clear_branch_name(branch)
                        only_branch = branch.split("/")[1]
                        new_commits.setdefault(only_branch, set())
                        if "Switched to a new branch" in shell.X(["git", "checkout", only_branch]).output.strip():
                            updated_branches.add(only_branch)
                            new_commits[only_branch] |= set(shell.X(["git", "log", "--format=%H"]).output.strip().split("\n"))
                        else:
                            new_commits[only_branch] |= set([x.split(" ")[1] for x in shell.X(["git", "cherry", only_branch, f"{remote}/{only_branch}"]).output.strip().split("\n") if x.startswith("+ ")])
                        if new_commits:
                            updated_branches.add(only_branch)
                        shell.X(["git", "checkout", "-B", only_branch, f"{remote}/{only_branch}"])  # recreate branch
                        del only_branch
                        del branch

                for branch in updated_branches:
                    branch = self._clear_branch_name(branch)
                    branch = branch.split("/")[-1]
                    shell.X(["git", "checkout", "-f", branch])
                    name = branch
                    del branch

                    if name in all_remote_branches:
                        shell.X(["git", "pull"])
                    if not (branch := repo.branch_ids.filtered(lambda x: x.name == name)):
                        branch = repo.branch_ids.create({
                            'name': name,
                            'date_registered': arrow.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                            'repo_id': repo.id,
                        })
                        branch._update_git_commits(shell, logsio, force_instance_folder=repo_path, force_commits=new_commits[name])

                    shell.X(["git", "checkout", "-f", "master"])
                    del name

    def _lock_git(self): 
        def retry(lock):
            raise RetryableJobError(f'Could not acquire advisory lock (stock move line {lock})', seconds=random.randint(5, 15), ignore_retry=True)

        for rec in self:
            lock = rec.name
            if not pg_try_advisory_lock(self.env.cr, lock):
                retry(lock)

    def clone_repo(self, machine, path, logsio):
        with self._get_ssh_command() as env:
            with machine._shell() as shell:
                if not shell.exists(path):
                    machine._execute_shell(
                        ["git", "clone", self.url, path],
                        env=env,
                        logsio=logsio,
                    )