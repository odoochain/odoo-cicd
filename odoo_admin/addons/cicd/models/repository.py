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
from . import pg_try_advisory_lock
from ..tools.tools import _execute_shell
from odoo.addons.queue_job.exception import (
    RetryableJobError,
    JobError,
)
import logging

logger = logging.getLogger(__name__)

class NewBranch(Exception): pass
class Repository(models.Model):
    _name = 'cicd.git.repo'

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
    ]

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
                env['GIT_SSH_COMMAND'] += ['f-i {file}']
                file.write_text(self.key)
            else:
                pass
            yield env
        finally:
            if file.exists():
                file.unlink()


    def _get_main_repo(self, tempfolder=False, destination_folder=False):
        self.ensure_one()
        from . import WORKSPACE
        from . import MAIN_FOLDER_NAME
        path = WORKSPACE / MAIN_FOLDER_NAME
        repo = self.clone_repo(path)

        if destination_folder:
            temppath = destination_folder
        elif tempfolder:
            temppath = tempfile.mktemp()
        else:
            temppath = None
        if temppath:
            subprocess.check_call(['rsync', f"{path}/", f"{temppath}/", "-ar"])
            repo = Repo(temppath)
                
        return repo

    @api.model
    def _cron_fetch(self):
        for repo in self.search([]):

            self._lock_git()
                
            repo = self._get_main_repo()

            for remote in repo.remotes:
                with self._get_ssh_command() as env:
                    fetch_info = remote.fetch(env=env)
                    for fi in fetch_info:
                        name = fi.ref.name.split("/")[-1]
                        for skip in (self.skip_paths or '').split(","):
                            if skip in fi.ref.name: # e.g. '/release/'
                                continue
                        sha = fi.commit

                        if not (branch := self.branch_ids.filtered(lambda x: x.name == name)):
                            branch = self.branch_ids.create({
                                'name': name,
                                'date_registered': arrow.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                                'repo_id': self.id,
                            })

                        new_commit = False
                        if not (commit := branch.commit_ids.filtered(lambda x: x.name == name)):
                            new_commit = True
                            commit = branch.commit_ids.create({
                                'name': name,
                                'date_registered': arrow.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                                'branch_ids': [[4, branch.id]],
                            })

                        if new_commit:
                            try:
                                repo.git.checkout(name, force=True)
                                repo.git.pull(env=env)
                            except Exception as ex:
                                logger.error(ex)

    def _lock_git(self): 
        def retry(lock):
            raise RetryableJobError(f'Could not acquire advisory lock (stock move line {lock})', seconds=random.randint(5, 15), ignore_retry=True)

        for rec in self:
            lock = rec.name
            if not pg_try_advisory_lock(self.env.cr, lock):
                retry(lock)

    def clone_repo(self, path):
        with self._get_ssh_command() as env:
            if not path.exists():
                git.Repo.clone_from(self.url, path, env=env)
            try:
                repo = Repo(path)
            except git.exc.InvalidGitRepositoryError:
                shutil.rmtree(path)
                git.Repo.clone_from(self.url, path, env=env)
                repo = Repo(path)
        return repo
