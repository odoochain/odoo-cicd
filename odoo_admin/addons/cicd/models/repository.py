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

    short = fields.Char(compute="_compute_shortname", string="Name", compute_sudo=True)
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
    url = fields.Char(compute="_compute_url", compute_sudo=True)
    default_branch = fields.Char(default="master", required=True)
    ticketsystem_id = fields.Many2one('cicd.ticketsystem', string="Ticket System")
    release_ids = fields.One2many('cicd.release', 'repo_id', string="Releases")
    default_simulate_install_id_dump_id = fields.Many2one('cicd.dump', string="Default Simluate Install Dump")
    never_cleanup = fields.Boolean("Never Cleanup")
    cleanup_untouched = fields.Integer("Cleanup after days", default=20, required=True)
    autofetch = fields.Boolean("Autofetch", default=True)
    garbage_collect = fields.Boolean("Garbage Collect to reduce size", default=True)
    initialize_new_branches = fields.Boolean("Initialize new Branches")
    release_tag_prefix = fields.Char("Release Tag Prefix", default="release-", required=True)
    remove_web_assets_after_restore = fields.Boolean("Remove Webassets", default=True)
    ttype = fields.Selection([
        ('gitlab', 'GitLab'),
        ('bitbucket', 'BitBucket'),
        ('github', 'GitHub'),
    ], string="Type")

    make_dev_dumps = fields.Boolean("Make Dev Dumps")
    ticketsystem_id = fields.Many2one("cicd.ticketsystem", string="Ticket-System")

    _sql_constraints = [
        ('name_unique', "unique(named)", _("Only one unique entry allowed.")),
        ('url_unique', "unique(url)", _("Only one unique entry allowed.")),
    ]

    def _get_lockname(self):
        self.ensure_one()
        return f"repo_{self.id}"

    @api.constrains("username")
    def _check_username(self):
        for rec in self:
            if rec.username and '@' in rec.username:
                raise ValidationError(_("Please use the login username instead of email address"))

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

    def _get_zipped(self, logsio, commit):
        machine = self.machine_id
        repo_path = self._get_main_repo(logsio=logsio, tempfolder=True, machine=machine)
        filename = Path(tempfile.mktemp(suffix='.'))
        try:
            with machine._shellexec(repo_path, logsio=logsio) as shell:
                try:
                    shell.checkout_commit(commit)
                    shell.X(["git", "clean", "-xdff"])
                    shell.X(["tar", "cfz", filename, "-C", repo_path, '.'])
                    content = shell.get(filename)
                    shell.X(["rm", filename])
                    return content
                finally:
                    shell.rm(repo_path)

        finally:
            if filename.exists():
                filename.unlink()

    def _get_main_repo(self, tempfolder=False, destination_folder=False, logsio=None, machine=None, limit_branch=None):
        self.ensure_one()
        from . import MAIN_FOLDER_NAME
        machine = machine or self.machine_id
        if not machine.workspace:
            raise ValidationError(_("Please configure a workspace!"))
        path = Path(machine.workspace) / (MAIN_FOLDER_NAME + "_" + self.short)
        self.clone_repo(machine, path, logsio)

        temppath = path
        if destination_folder:
            temppath = destination_folder
        elif tempfolder:
            temppath = tempfile.mktemp()
        if temppath and temppath != path:
            with machine._shell(cwd=self.machine_id.workspace, logsio=logsio) as shell:
                if not self._is_healthy_repository(shell, temppath):
                    shell.rm(temppath)

                    if limit_branch:
                        # make sure branch exists in source repo
                        with machine._shell(cwd=path, logsio=logsio) as tempshell:
                            tempshell.checkout_branch(limit_branch)

                    cmd = ["git", "clone"]
                    if limit_branch:
                        cmd += ["--branch", limit_branch]
                    cmd += [path, temppath]
                    shell.X(cmd)
        return temppath

    def _get_remotes(self, shell):
        remotes = shell.X(["git", "remote", "-v"])['stdout'].strip().split("\n")
        remotes = list(filter(bool, [x.split("\t")[0] for x in remotes]))
        return list(set(remotes))

    @api.model
    def _clear_branch_name(self, branch):
        branch = branch.strip()

        if " (" in branch:
            branch = branch.split(" (")[0]

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
        repos = self
        if not repos:
            repos = self.search([('autofetch', '=', True)])

        for repo in repos:
            try:
                if not repo.login_type:
                    raise ValidationError(f"Login-Type missing for {repo.name}")
                with LogsIOWriter.GET(repo.name, 'fetch') as logsio:
                    repo_path = repo._get_main_repo(logsio=logsio)

                    with repo.machine_id._gitshell(repo=repo, cwd=repo_path, logsio=logsio) as shell:
                        updated_branches = set()

                        for remote in repo._get_remotes(shell):
                            fetch_info = list(filter(lambda x: " -> " in x, shell.X(["git", "fetch", remote, '--dry-run'])['stdout'].strip().split("\n")))
                            for fi in fetch_info:
                                while "  " in fi:
                                    fi = fi.replace("  ", " ")
                                fi = fi.strip()
                                if '[new tag]' in fi:
                                    continue
                                elif '[new branch]' in fi:
                                    branch = fi.replace("[new branch]", "").split("->")[0].strip()
                                else:
                                    branch = fi.split("/")[-1]
                                try:
                                    branch = repo._clear_branch_name(branch)
                                except InvalidBranchName:
                                    logsio.error("Invalid Branch name: {branch}")
                                    continue

                                if not branch.startswith(repo.release_tag_prefix):
                                    updated_branches.add(branch)

                            del fetch_info

                        if not updated_branches:
                            continue

                        repo.with_delay()._cron_fetch_update_branches({
                            'updated_branches': list(updated_branches),
                        })

            except Exception as ex:
                msg = traceback.format_exc()
                if logsio:
                    logsio.error(msg)
                logger.error('error', exc_info=True)
                if len(self) == 1:
                    raise
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
        # checkout latest / pull latest
        updated_branches = data['updated_branches']

        with LogsIOWriter.GET(repo.name, 'fetch') as logsio:




            repo_path = repo._get_main_repo(logsio=logsio)
            machine = repo.machine_id
            repo = repo.with_context(active_test=False)

            with pg_advisory_lock(self.env.cr, self._get_lockname()):
                with repo.machine_id._gitshell(repo, cwd=repo_path, logsio=logsio) as shell:
                    for branch in updated_branches:
                        logsio.info(f"Pulling {branch}...")
                        shell.X(["git", "fetch", "origin", branch])
                        with pg_advisory_lock(self.env.cr, repo._get_lockname()):
                            shell.checkout_branch(branch)
                            shell.X(["git", "pull"])
                            shell.X(["git", "submodule", "update", "--init", "--recursive"])

                    # if completely new then all branches:
                    if not repo.branch_ids:
                        for branch in shell.X(["git", "branch"])['stdout'].strip().split("\n"):
                            branch = self._clear_branch_name(branch)
                            updated_branches.append(branch)

                    for branch in updated_branches:
                        shell.checkout_branch(branch)
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

                        shell.checkout_branch(repo.default_branch)
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

    def _is_healthy_repository(self, shell, path):
        healthy = False
        if shell.exists(path):
            try:
                res = shell.X(["git", "status", "-s"], cwd=path, logoutput=False)
                if res['stdout'].strip():
                    healthy = False
                else:
                    healthy = True
            except:
                pass
        return healthy

    def clone_repo(self, machine, path, logsio):
        with machine._gitshell(self, cwd="", logsio=logsio) as shell:
            with pg_advisory_lock(self.env.cr, self._get_lockname()):
                if not self._is_healthy_repository(shell, path):
                    shell.rm(path)
                    shell.X([
                        "git", "clone", self.url,
                        path
                    ])

    def _collect_latest_tested_commits(self, source_branches, target_branch_name, logsio, critical_date, make_info_commit_msg):
        """
        Iterate all branches and get the latest commit that fall into the countdown criteria.

        "param make_info_commit_msg": if set, then an empty commit with just a message is made
        """
        with pg_advisory_lock(self.env.cr, self._get_lockname()):
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
                    if shell.branch_exists(target_branch_name):
                        shell.checkout_branch(self.default_branch)
                        shell.X(["git", "branch", "-D", target_branch_name])
                    logsio.info("Making target branch {target_branch.name}")
                    shell.checkout_branch(repo.default_branch)
                    shell.X(["git", "checkout", "--no-guess", "-b", target_branch_name])

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
                            shell.checkout_branch(target_branch_name)
                            shell.X(["git", "merge", commit.name])
                            break

                    if commits:

                        # pushes to mainrepo locally not to web because its cloned to temp directory
                        shell.X(["git", "remote", "set-url", 'origin', self.url])
                        shell.X(["git", "push", "--set-upstream", "-f", 'origin', target_branch_name])

                        message_commit_sha = None
                        if make_info_commit_msg:
                            shell.X(["git", "commit", "--allow-empty", "-m", make_info_commit_msg])
                            message_commit_sha = shell.X(["git", "log", "-n1", "--format=%H"])['stdout'].strip()
                        shell.X(["git", "push", "-f", 'origin', target_branch_name])

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
                    shell.rm(repo_path)

            return message_commit, commits

    def _merge(self, source, dest, set_tags, logsio=None):
        assert source._name == 'cicd.git.branch'
        assert dest._name == 'cicd.git.branch'
        source.ensure_one()
        dest.ensure_one()

        machine = self.machine_id
        repo_path = self._get_main_repo(tempfolder=True)
        with machine._gitshell(self, cwd=repo_path, logsio=logsio) as shell:
            try:
                shell.checkout_branch(dest.name)
                commitid = shell.X(["git", "log", "-n1", "--format=%H"])['stdout'].strip()
                branches = [self._clear_branch_name(x) for x in shell.X(["git", "branch", "--contains", commitid])['stdout'].strip().split("\n")]
                if source.name in branches:
                    return False
                shell.checkout_branch(source.name)
                shell.checkout_branch(dest.name)
                count_lines = len(shell.X(["git", "diff", "-p", source.name])['stdout'].strip().split("\n"))
                shell.X(["git", "merge", source.name])
                for tag in set_tags:
                    shell.X(["git", "tag", '-f', tag.replace(':', '_').replace(' ', '_')])
                shell.X(["git", "remote", "set-url", 'origin', self.url])
                shell.X(["git", "push", '--tags'])

                return count_lines

            finally:
                shell.rm(repo_path)

    @api.model
    def _cron_cleanup(self):
        for repo in self.search([
            ('never_cleanup', '=', False),
        ]):
            with pg_advisory_lock(self.env.cr, repo._get_lockname()):
                dt = arrow.get().shift(days=-1 * repo.cleanup_untouched).strftime("%Y-%m-%d %H:%M:%S")
                # try nicht unbedingt notwendig; bei __exit__ wird ein close aufgerufen
                db_registry = registry(self.env.cr.dbname)
                branches = repo.branch_ids.filtered(lambda x: (x.last_access or x.date_registered).strftime("%Y-%m-%d %H:%M:%S") < dt)
                with db_registry.cursor() as cr:
                    env = api.Environment(cr, SUPERUSER_ID)
                    for branch in branches.with_env(env):
                        branch.active = False

    def new_branch(self):
        return {
            'view_type': 'form',
            'res_model': 'cicd.git.branch.new',
            'context': {
                'default_repo_id': self.id,
            },
            'views': [(False, 'form')],
            'type': 'ir.actions.act_window',
            'flags': {'form': {
                'action_buttons': False,
                'initial_mode': 'edit',
                #'footer_to_buttons': False,
                #'not_interactiable_on_create': False,
                #'disable_autofocus': False,
                #'headless': False,  9.0 and others?
            }},
            'options': {
                # needs module web_extended_actions
                'hide_breadcrumb': True,
                'replace_breadcrumb': True,
                'clear_breadcrumbs': True,
            },
            'target': 'new',
        }

    def _get_base_url(self):
        self.ensure_one()
        url = self.url
        if not url.endswith("/"):
            url += '/'
        if url.startswith("ssh://git@"):
            url = url.replace("ssh://git@", "https://")
        return url

    def _get_url(self, ttype, object, object2=None):
        self.ensure_one()
        if self.ttype == 'gitlab':
            if ttype == 'commit':
                return self._get_base_url() + "-/commit/" + object.name
            elif ttype == 'compare':
                return self._get_base_url() + "-/compare?from=" + object + "&to=" + object2
            else:
                raise NotImplementedError()
        elif self.ttype == 'bitbucket':
            if ttype == 'commit':
                return self._get_base_url() + "/commits/" + object.name
            else:
                raise NotImplementedError()
        else:
            raise NotImplementedError()