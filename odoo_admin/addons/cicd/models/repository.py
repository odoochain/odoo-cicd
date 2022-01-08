import traceback
from . import pg_advisory_lock
from odoo import registry
import arrow
from git import Repo
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

class InvalidBranchName(Exception): pass
class NewBranch(Exception): pass
class Repository(models.Model):
    _name = 'cicd.git.repo'

    short = fields.Char(compute="_compute_shortname", string="Name")
    machine_id = fields.Many2one('cicd.machine', string="Development Machine", required=True, domain=[('ttype', '=', 'dev')])
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
    default_simulate_install_id_dump_id = fields.Many2one('cicd.dump', string="Default Simluate Install Dump")
    never_cleanup = fields.Boolean("Never Cleanup")
    cleanup_untouched = fields.Integer("Cleanup after days", default=20, required=True)
    autofetch = fields.Boolean("Autofetch", default=True)
    garbage_collect = fields.Boolean("Garbage Collect to reduce size", default=True)

    make_dev_dumps = fields.Boolean("Make Dev Dumps")

    _sql_constraints = [
        ('name_unique', "unique(named)", _("Only one unique entry allowed.")),
        ('url_unique', "unique(url)", _("Only one unique entry allowed.")),
    ]

    def _compute_shortname(self):
        for rec in self:
            rec.short = rec.name.split("/")[-1]

    @api.depends('username', 'password', 'name')
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
                        break
                rec.url = url
            else:
                rec.url = rec.name

    def _get_main_repo(self, tempfolder=False, destination_folder=False, logsio=None, machine=None, limit_branch=None):
        self.ensure_one()
        from . import MAIN_FOLDER_NAME
        machine = machine or self.machine_id
        path = Path(machine.workspace) / (MAIN_FOLDER_NAME + "_" + self.short)
        self.clone_repo(machine, path, logsio)

        temppath = path
        if destination_folder:
            temppath = destination_folder
        elif tempfolder:
            temppath = tempfile.mktemp()
        if temppath and temppath != path:
            with machine._shellexec(self.machine_id.workspace, logsio=logsio) as shell:
                if not shell.exists(temppath):
                    if limit_branch:
                        # make sure branch exists in source repo
                        with machine._shellexec(path, logsio=logsio) as tempshell:
                            tempshell.X(["git", "checkout", "--no-guess", "-f", limit_branch])

                    cmd = ["git", "clone"]
                    if limit_branch:
                        cmd += ["--branch", limit_branch]
                    cmd += [path, temppath]
                    shell.X(cmd)
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
        branch = branch.strip()

        if any(x in branch for x in "():?*/\\!\"\'"):
            raise InvalidBranchName(branch)
        return branch

    def fetch(self):
        self._cron_fetch()

    @api.model
    def _cron_fetch(self):
        logsio = None
        for repo in self.search([('autofetch', '=', True)]):
            try:
                repo._lock_git()
                logsio = LogsIOWriter(repo.name, 'fetch')
                    
                repo_path = repo._get_main_repo(logsio=logsio)

                with repo.machine_id._gitshell(repo=repo, cwd=repo_path, logsio=logsio) as shell:
                    updated_branches = set()

                    for remote in repo._get_remotes(shell):
                        fetch_info = list(filter(lambda x: " -> " in x, shell.X(["git", "fetch", remote, '--dry-run']).stderr_output.strip().split("\n")))
                        for fi in fetch_info:
                            while "  " in fi:
                                fi = fi.replace("  ", " ")
                            fi = fi.strip()
                            if '[new branch]' in fi:
                                branch = fi.replace("[new branch]", "").split("->")[0].strip()
                            else:
                                branch = fi.split("/")[-1]
                            try:
                                branch = repo._clear_branch_name(branch)
                            except InvalidBranchName:
                                logsio.error("Invalid Branch name: {branch}")
                                continue
                            updated_branches.add(branch)

                        del fetch_info

                    if not updated_branches:
                        continue

                    # checkout latest / pull latest
                    for branch in updated_branches:
                        logsio.info(f"Pulling {branch}...")
                        shell.X(["git", "fetch", "origin", branch])
                        shell.X(["git", "checkout", "--no-guess", "-f", branch])
                        shell.X(["git", "pull"])
                        shell.X(["git", "submodule", "update", "--init", "--recursive"])

                    repo.with_delay()._cron_fetch_update_branches({
                        'updated_branches': list(updated_branches),
                    })


            except Exception as ex:
                msg = traceback.format_exc()
                if logsio:
                    logsio.error(msg)
                logger.error(msg)
                continue

    def _clean_remote_branches(self, branches):
        """
        origin/pre_master1']  --> pre_master1
        """
        for branch in branches:
            if '->' in branch:
                continue
            yield branch.split("/")[-1].strip()

    def _cron_fetch_update_branches(self, data):
        repo = self
        updated_branches = data['updated_branches']
        logsio = LogsIOWriter(repo.name, 'fetch')
        repo_path = repo._get_main_repo(logsio=logsio)
        repo._lock_git()
        machine = repo.machine_id
        repo = repo.with_context(active_test=False)

        with repo.machine_id._gitshell(repo, cwd=repo_path, logsio=logsio) as shell:
            # if completely new then all branches:
            if not repo.branch_ids:
                for branch in shell.X(["git", "branch"]).output.strip().split("\n"):
                    branch = self._clear_branch_name(branch)
                    updated_branches.append(branch)

            for branch in updated_branches:
                shell.X(["git", "checkout", "--no-guess", "-f", branch])
                shell.X(["git", "submodule", "update", "--init", "--force", "--recursive"])
                name = branch
                del branch

                if not (branch := repo.branch_ids.filtered(lambda x: x.name == name)):
                    branch = repo.branch_ids.create({
                        'name': name,
                        'date_registered': arrow.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                        'repo_id': repo.id,
                    })
                    branch._checkout_latest(shell, logsio=logsio, machine=machine)
                    branch._update_git_commits(shell, logsio, force_instance_folder=repo_path)

                if not branch.active:
                    branch.active = True

                shell.X(["git", "checkout", "--no-guess", "-f", repo.default_branch])
                del name

            if not repo.branch_ids and not updated_branches:
                if repo.default_branch:
                    updated_branches.append(repo.default_branch)

            if updated_branches:
                repo.clear_caches() # for contains_commit function; clear caches tested in shell and removes all caches; method_name
                branches = repo.branch_ids.filtered(lambda x: x.name in updated_branches)
                for branch in branches:
                    branch._checkout_latest(shell, logsio=logsio, machine=machine)
                    branch._update_git_commits(shell, logsio)
                    branch._compute_state()
                    branch._trigger_rebuild_after_fetch(machine=machine)

    def _lock_git(self): 
        for rec in self:
            lock = rec.name
            if not pg_try_advisory_lock(self.env.cr, lock):
                raise RetryableJobError(_("Git is in other use at the moment"), seconds=10, ignore_retry=True)

    def clone_repo(self, machine, path, logsio):
        with machine._gitshell(self, cwd="", logsio=logsio) as shell:
            if not shell.exists(path):
                self._lock_git()
                shell.X([
                    "git", "clone", self.url,
                    path
                ])

    def _collect_latest_tested_commits(self, source_branches, target_branch_name, logsio, critical_date, make_info_commit_msg):
        """
        Iterate all branches and get the latest commit that fall into the countdown criteria.

        "param make_info_commit_msg": if set, then an empty commit with just a message is made
        """
        self.ensure_one()

        # we use a working repo
        assert target_branch_name
        assert source_branches._name == 'cicd.git.branch'
        machine = self.machine_id
        orig_repo_path = self._get_main_repo()
        repo_path = self._get_main_repo(tempfolder=True)
        commits = self.env['cicd.git.commit']
        repo = self.with_context(active_test=False)
        message_commit = None # commit sha of the created message commit
        with machine._gitshell(self, cwd=repo_path, logsio=logsio) as shell:
            try:

                # clear the current candidate
                res = shell.X(["/usr/bin/git", "show-ref", "--verify", "--quiet", "refs/heads/" + target_branch_name], allow_error=True)
                if not res.return_code:
                    shell.X(["/usr/bin/git", "branch", "-D", target_branch_name])
                logsio.info("Making target branch {target_branch.name}")
                shell.X(["/usr/bin/git", "checkout", "--no-guess", repo.default_branch])
                shell.X(["/usr/bin/git", "checkout", "--no-guess", "-b", target_branch_name])

                for branch in source_branches:
                    for commit in branch.commit_ids.sorted(lambda x: x.date, reverse=True):
                        if critical_date:
                            if commit.date.strftime("%Y-%m-%d %H:%M:%S") > critical_date.strftime("%Y-%m-%d %H:%M:%S"):
                                continue

                        if not commit.force_approved and (commit.test_state != 'success' or commit.approval_state != 'approved'):
                            continue

                        commits |= commit

                        # we use git functions to retrieve deltas, git sorting and so;
                        # we want to rely on stand behaviour git.
                        shell.X(["/usr/bin/git", "checkout", "--no-guess", "-f", target_branch_name])
                        shell.X(["/usr/bin/git", "merge", commit.name])
                        # pushes to mainrepo locally not to web because its cloned to temp directory
                        shell.X(["/usr/bin/git", "push", "-f", 'origin', target_branch_name])
                        break
                
                url = shell.X(["/usr/bin/git", "remote", "get-url", 'origin'], cwd=orig_repo_path).output.strip()

                shell.X(["/usr/bin/git", "remote", "set-url", 'origin', url])
                shell.X(["/usr/bin/git", "push", "--set-upstream", 'origin', target_branch_name])
                message_commit_sha = None
                if make_info_commit_msg:
                    shell.X(["/usr/bin/git", "commit", "--allow-empty", "-m", make_info_commit_msg])
                    message_commit_sha = shell.X(["/usr/bin/git", "log", "-n1", "--format=%H"]).output.strip()
                shell.X(["/usr/bin/git", "push", "-f", 'origin', target_branch_name])

                if not (target_branch := repo.branch_ids.filtered(lambda x: x.name == target_branch_name)):
                    target_branch = repo.branch_ids.create({
                        'repo_id': repo.id,
                        'name': target_branch_name,
                    })
                if not target_branch.active:
                    target_branch.active = True
                target_branch._update_git_commits(shell, logsio, force_instance_folder=repo_path)
                if message_commit_sha:
                    message_commit = target_branch.commit_ids.filtered(lambda x: x.name == message_commit_sha)
                    message_commit.ensure_one()

            finally:
                shell.rmifexists(repo_path)

        return message_commit, commits

    def _merge(self, source, dest, set_tags, logsio=None):
        assert source._name == 'cicd.git.branch'
        assert dest._name == 'cicd.git.branch'
        source.ensure_one()
        dest.ensure_one()

        machine = self.machine_id
        repo_path = self._get_main_repo(tempfolder=True)
        with machine._gitshell(self, cwd=repo_path, logsio=logsio, env=env) as shell:
            try:
                shell.X(["/usr/bin/git", "checkout", "--no-guess", "-f", dest.name])
                commitid = shell.X(["/usr/bin/git", "log", "-n1", "--format=%H"]).output.strip()
                branches = [self._clear_branch_name(x) for x in shell.X(["/usr/bin/git", "branch", "--contains", commitid]).output.strip().split("\n")]
                if source.name in branches:
                    return False
                shell.X(["/usr/bin/git", "checkout", "--no-guess", "-f", source.name])
                shell.X(["/usr/bin/git", "checkout", "--no-guess", "-f", dest.name])
                count_lines = len(shell.X(["/usr/bin/git", "diff", "-p", source.name]).output.strip().split("\n"))
                shell.X(["/usr/bin/git", "merge", source.name])
                for tag in set_tags:
                    shell.X(["/usr/bin/git", "tag", '-f', tag])
                shell.X(["/usr/bin/git", "push", '--follow-tags', '-f'])

                return count_lines

            finally:
                shell.rmifexists(repo_path)

    @api.model
    def _cron_cleanup(self):
        for repo in self.search([
            ('never_cleanup', '=', False),
        ]):
            dt = arrow.get().shift(days=-1 * repo.cleanup_untouched).strftime("%Y-%m-%d %H:%M:%S")
            # try nicht unbedingt notwendig; bei __exit__ wird ein close aufgerufen
            db_registry = registry(self.env.cr.dbname)
            branches = repo.branch_ids.filtered(lambda x: (x.last_access or x.date_registered).strftime("%Y-%m-%d %H:%M:%S") < dt)
            with api.Environment.manage(), db_registry.cursor() as cr:
                for branch in branches:
                    env = api.Environment(cr, SUPERUSER_ID, {})
                    branch = branch.with_env(env)
                    branch.active = False
                    env.cr.commit()

    # def _cron_make_dev_dumps(self):
    #     for rec in self.search([('make_dev_dumps', '=', True)]):
    #         if not rec.default_branch: # or a release branch more?
    #             continue

    #         repo_path = rec._get_main_repo()
