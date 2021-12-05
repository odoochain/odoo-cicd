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
    default_branch = fields.Char(default="master", required=True)
    ticket_system_base_url = fields.Char("Ticket System Base URL")
    ticket_system_regex = fields.Char("Ticket System Regex")
    release_ids = fields.One2many('cicd.release', 'repo_id', string="Releases")

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

    def _get_main_repo(self, tempfolder=False, destination_folder=False, logsio=None, machine=None):
        self.ensure_one()
        from . import MAIN_FOLDER_NAME
        machine = machine or self.machine_id
        path = Path(machine.workspace) / (MAIN_FOLDER_NAME + "_" + self.short)
        self.clone_repo(machine, path, logsio)

        temppath = path
        if destination_folder:
            path = destination_folder
        elif tempfolder:
            temppath = tempfile.mktemp()
        if temppath and temppath != path:
            with machine._shellexec(self.machine_id.workspace, logsio=logsio) as shell:
                shell.X(['rsync', f"{path}/", f"{temppath}/", "-ar"])
        return temppath

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
        return branch.strip()

    def fetch(self):
        self._cron_fetch()

    @api.model
    def _get_git_non_interactive(self):
        env = {
            "GIT_ASK_YESNO": "false",
            "GIT_SSH_COMMAND": f'ssh -o StrictHostKeyChecking=no',
            "GIT_TERMINAL_PROMPT": "0",
        }
        return env

    @api.model
    def _cron_fetch(self):
        for repo in self.search([]):
            self._lock_git()
            logsio = LogsIOWriter(repo.name, 'fetch')
                
            repo_path = repo._get_main_repo(logsio=logsio)
            env = self._get_git_non_interactive()
            with repo.machine_id._shellexec(cwd=repo_path, logsio=logsio, env=env) as shell:
                shell.X(["git", "clean", "-xdff"])
                all_remote_branches = shell.X(["git", "branch", "-r"]).output.strip().split("\n")
                new_commits, updated_branches = {}, set()

                for remote in self._get_remotes(shell):
                    fetch_info = list(filter(lambda x: " -> " in x, shell.X(["git", "fetch", remote]).stderr_output.strip().split("\n")))
                    for fi in fetch_info:
                        while "  " in fi:
                            fi = fi.replace("  ", " ")
                        fi = fi.strip()
                        if '[new branch]' in fi:
                            branch = fi.replace("[new branch]", "").split("->")[0].strip()
                            start_commit = None
                            end_commit = None
                        else:
                            branch = fi.split("/")[-1]
                            start_commit = fi.split("..")[0]
                            end_commit = fi.split("..")[1].split(" ")[0]
                        branch = self._clear_branch_name(branch)
                        updated_branches.add(branch)
                        new_commits.setdefault(branch, set())
                        if start_commit and end_commit:
                            start_commit = shell.X(["git", "rev-parse", start_commit]).output.strip()
                            end_commit = shell.X(["git", "rev-parse", end_commit]).output.strip()
                            new_commits[branch] |= set(shell.X(["git", "rev-list", "--ancestry-path", f"{start_commit}..{end_commit}"]).output.strip().split("\n"))
                        else:
                            new_commits[branch] |= set(shell.X(["git", "log", "--format=%H"]).output.strip().split("\n"))

                # if completely new then all branches:
                if not repo.branch_ids:
                    for branch in shell.X(["git", "branch"]).output.strip().split("\n"):
                        branch = self._clear_branch_name(branch)
                        updated_branches.add(branch)
                        new_commits[branch] = None # for the parameter laster as None

                for branch in updated_branches:
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

                    shell.X(["git", "checkout", "-f", repo.default_branch])
                    del name

                if updated_branches:
                    repo.clear_caches() # for contains_commit function; clear caches tested in shell and removes all caches; method_name
                    repo.branch_ids._compute_state()
                    repo.release_ids.collect_branches_on_candidate()
                del updated_branches

    def _lock_git(self): 
        for rec in self:
            lock = rec.name
            if not pg_try_advisory_lock(self.env.cr, lock):
                raise ValidationError(_("Git is in other use at the moment"))

    def clone_repo(self, machine, path, logsio):
        with self._get_ssh_command() as env:
            with machine._shell() as shell:
                if not shell.exists(path):
                    machine._execute_shell(
                        ["git", "clone", self.url, path],
                        env=env,
                        logsio=logsio,
                    )

    def _collect_branches(self, source_branches, target_branch, logsio):
        """
        Iterate all branches and get the latest commit that fall into the countdown criteria.
        """
        self.ensure_one()

        # we use a working repo
        assert target_branch._name == 'cicd.git.branch'
        assert target_branch
        assert source_branches._name == 'cicd.git.branch'
        machine = self.machine_id
        import pudb;pudb.set_trace()
        repo_path = self._get_main_repo(tempfolder=True)
        env = self._get_git_non_interactive()
        with machine._shellexec(cwd=repo_path, logsio=logsio, env=env) as shell:
            try:

                # clear the current candidate
                res = shell.X(["/usr/bin/git", "show-ref", "--verify", "--quiet", "refs/heads/" + target_branch.name], allow_error=True)
                if not res.return_code:
                    shell.X(["/usr/bin/git", "branch", "-D", target_branch.name])
                logsio.info("Making target branch {target_branch.name}")
                shell.X(["/usr/bin/git", "checkout", "-b", target_branch.name])

                for branch in source_branches:
                    for commit in branch.commit_ids.sorted(lambda x: x.date, reverse=True):
                        if self.final_curtain:
                            if commit.date > self.final_curtain:
                                continue

                        if not commit.force_approved and (commit.test_state != 'success' or commit.approval_state != 'approved'):
                            continue

                        self.commit_ids = [[4, commit.id]]

                        # we use git functions to retrieve deltas, git sorting and so;
                        # we want to rely on stand behaviour git.
                        shell.X(["/usr/bin/git", "checkout", "-f", branch.name])
                        shell.X(["/usr/bin/git", "reset", "--hard", commit.name])
                        shell.X(["/usr/bin/git", "checkout", "-f", target_branch.name])
                        shell.X(["/usr/bin/git", "merge", "-f", branch.name])
                        shell.X(["/usr/bin/git", "push", "-f", 'origin', target_branch.name])


            finally:
                import pudb;pudb.set_trace()
                shell.X(["rm", "-Rf", repo_path])
