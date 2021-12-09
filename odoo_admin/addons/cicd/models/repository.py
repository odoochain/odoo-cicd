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

    _sql_constraints = [
        ('name_unique', "unique(named)", _("Only one unique entry allowed.")),
        ('url_unique', "unique(url)", _("Only one unique entry allowed.")),
    ]

    def _compute_shortname(self):
        for rec in self:
            rec.short = rec.name.split("/")[-1]

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
    def _get_ssh_command(self, shell):
        self.ensure_one()
        file = Path(tempfile.mktemp(suffix='.'))
        env = {}

        try:
            env['GIT_SSH_COMMAND'] = f'ssh -o StrictHostKeyChecking=no'
            if self.login_type == 'key':
                env['GIT_SSH_COMMAND'] += f'   -i {file}  '
                shell.write_text(file, self.key)
                shell.run(["chmod", '400', str(file)])
            else:
                pass
            yield env
        finally:
            if shell.exists(file):
                shell.remove(file)

    def _get_main_repo(self, tempfolder=False, destination_folder=False, logsio=None, machine=None):
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

            with repo.machine_id._shell() as spurplus_shell:
                with repo._get_ssh_command(spurplus_shell) as env2:
                    env.update(env2)
                    with repo.machine_id._shellexec(cwd=repo_path, logsio=logsio, env=env) as shell:
                        try:
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

                            if not new_commits and not updated_branches:
                                continue

                            self.with_delay(
                                identity_key=f"cron_fetch_update_branches: {repo.id}",
                            )._cron_fetch_update_branches({
                                'repo': repo,
                                'new_commits': dict((x, list(y)) for x, y in new_commits.items()),
                                'updated_branches': list(updated_branches),
                            })

                        except Exception as ex:
                            msg = traceback.format_exc()
                            logsio.error(msg)
                            logger.error(msg)
                            raise

    def _cron_fetch_update_branches(self, data):
        new_commits = data['new_commits']
        repo = data['repo']
        updated_branches = data['updated_branches']
        logsio = LogsIOWriter(repo.name, 'fetch')
        repo_path = repo._get_main_repo(logsio=logsio)
        env = self._get_git_non_interactive()
        # pg_advisory_lock(self.env.cr, f"fetch_update_{repo.id}")

        with repo.machine_id._shellexec(cwd=repo_path, logsio=logsio, env=env) as shell:
            all_remote_branches = shell.X(["git", "branch", "-r"]).output.strip().split("\n")
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
                repo.branch_ids.filtered(lambda x: x.name in updated_branches)._trigger_rebuild_after_fetch()

    def _lock_git(self): 
        for rec in self:
            lock = rec.name
            if not pg_try_advisory_lock(self.env.cr, lock):
                raise ValidationError(_("Git is in other use at the moment"))

    def clone_repo(self, machine, path, logsio):
        with machine._shell() as shell:
            with self._get_ssh_command(shell) as env:
                if not shell.exists(path):
                    machine._execute_shell(
                        ["git", "clone", self.url, path],
                        env=env,
                        logsio=logsio,
                    )

    def _collect_latest_tested_commits(self, source_branches, target_branch, logsio, critical_date):
        """
        Iterate all branches and get the latest commit that fall into the countdown criteria.
        """
        self.ensure_one()

        # we use a working repo
        assert target_branch._name == 'cicd.git.branch'
        assert target_branch
        assert source_branches._name == 'cicd.git.branch'
        machine = self.machine_id
        repo_path = self._get_main_repo(tempfolder=True)
        env = self._get_git_non_interactive()
        commits = self.env['cicd.git.commit']
        with machine._shell() as spurplus_shell:
            with self._get_ssh_command(spurplus_shell) as env2:
                env.update(env2)
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
                                if critical_date:
                                    if commit.date.strftime("%Y-%m-%d %H:%M:%S") > critical_date.strftime("%Y-%m-%d %H:%M:%S"):
                                        continue

                                if not commit.force_approved and (commit.test_state != 'success' or commit.approval_state != 'approved'):
                                    continue

                                commits |= commit

                                # we use git functions to retrieve deltas, git sorting and so;
                                # we want to rely on stand behaviour git.
                                shell.X(["/usr/bin/git", "checkout", "-f", branch.name])
                                shell.X(["/usr/bin/git", "reset", "--hard", commit.name])
                                shell.X(["/usr/bin/git", "checkout", "-f", target_branch.name])
                                shell.X(["/usr/bin/git", "merge", "-f", branch.name])
                                shell.X(["/usr/bin/git", "push", "-f", 'origin', target_branch.name])


                    finally:
                        shell.rmifexists(repo_path)
        return commits

    def _merge(self, source, dest, set_tags, logsio=None):
        assert source._name == 'cicd.git.branch'
        assert dest._name == 'cicd.git.branch'
        source.ensure_one()
        dest.ensure_one()

        machine = self.machine_id
        repo_path = self._get_main_repo(tempfolder=True)
        env = self._get_git_non_interactive()
        with machine._shellexec(cwd=repo_path, logsio=logsio, env=env) as shell:
            try:
                shell.X(["/usr/bin/git", "checkout", "-f", dest.name])
                commitid = shell.X(["/usr/bin/git", "log", "-n1", "--format=%H"]).output.strip()
                branches = [self._clear_branch_name(x) for x in shell.X(["/usr/bin/git", "branch", "--contains", commitid]).output.strip().split("\n")]
                if source.name in branches:
                    return False
                shell.X(["/usr/bin/git", "checkout", "-f", source.name])
                shell.X(["/usr/bin/git", "checkout", "-f", dest.name])
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
