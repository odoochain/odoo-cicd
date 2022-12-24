import traceback
import re
import json
from . import pg_advisory_lock
from odoo import registry
import arrow
from pathlib import Path
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from ..tools.logsio_writer import LogsIOWriter
from odoo.addons.queue_job.exception import (
    RetryableJobError,
    JobError,
)
import logging
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT as DTF
from contextlib import contextmanager, closing

logger = logging.getLogger(__name__)


class InvalidBranchName(Exception):
    pass


class NewBranch(Exception):
    pass


class Repository(models.Model):
    _inherit = ["mail.thread", "cicd.test.settings"]
    _name = "cicd.git.repo"
    _rec_name = "short"

    registry_id = fields.Many2one("cicd.registry", string="Docker Registry")
    needs_codereview = fields.Boolean("Needs Codereview", default=True)
    short = fields.Char(
        compute="_compute_shortname", string="Name", compute_sudo=True, store=True
    )
    webhook_id = fields.Char("Webhook ID", help="/trigger/repo/<this id>")
    webhook_secret = fields.Char("Webhook Secret")
    update_ribbon_in_instance = fields.Boolean(
        "Update Ribbon after restore", default=True
    )
    machine_id = fields.Many2one(
        "cicd.machine",
        string="Development Machine",
        required=True,
        domain=[("ttype", "=", "dev")],
    )
    update_i18n = fields.Boolean("Update I18N at updates")
    name = fields.Char("URL", required=True)
    login_type = fields.Selection(
        [
            ("nothing", "Nothing - just url"),
            ("username", "Username"),
            ("key", "Key"),
        ]
    )
    analyze_last_n_commits = fields.Integer("Anlayze last n commits", default=200)
    key = fields.Text("Key")
    username = fields.Char("Username")
    password = fields.Char("Password")
    skip_paths = fields.Char("Skip Paths", help="Comma separated list")
    branch_ids = fields.One2many("cicd.git.branch", "repo_id", string="Branches")
    url = fields.Char(compute="_compute_url", compute_sudo=True)
    default_branch = fields.Char(default="master", required=True)
    ticketsystem_id = fields.Many2one("cicd.ticketsystem", string="Ticket System")
    release_ids = fields.One2many("cicd.release", "repo_id", string="Releases")
    default_simulate_install_id_dump_id = fields.Many2one(
        "cicd.dump", string="Default Simluate Install Dump"
    )
    never_cleanup = fields.Boolean("Never Cleanup")
    cleanup_untouched = fields.Integer("Cleanup after days", default=20, required=True)
    autofetch = fields.Boolean("Autofetch", default=True)
    garbage_collect = fields.Boolean("Garbage Collect to reduce size", default=False)
    initialize_new_branches = fields.Boolean("Initialize new Branches")
    release_tag_prefix = fields.Char(
        "Release Tag Prefix", default="release-", required=True
    )
    remove_web_assets_after_restore = fields.Boolean("Remove Webassets", default=True)
    ttype = fields.Selection(
        [
            ("gitlab", "GitLab"),
            ("bitbucket", "BitBucket"),
            ("github", "GitHub"),
        ],
        string="Type",
    )

    make_dev_dumps = fields.Boolean("Make Dev Dumps")
    ticketsystem_id = fields.Many2one("cicd.ticketsystem", string="Ticket-System")
    revive_branch_on_push = fields.Boolean("Revive Branch on push")

    _sql_constraints = [
        ("name_unique", "unique(name)", _("Only one unique entry allowed.")),
    ]

    def _get_repo_path(self, machine):
        from . import MAIN_FOLDER_NAME

        self.ensure_one()
        path = Path(machine._unblocked("workspace")) / (
            MAIN_FOLDER_NAME + "_" + self._unblocked("short")
        )
        return path

    def _get_lockname(self, machine=None, path=None):
        self.ensure_one()
        with self._extra_env() as x_self:
            machine = machine or x_self.machine_id
            path = path or x_self._get_repo_path(machine)
            return f"repo_{machine.id}_{path}"

    @api.constrains("username")
    def _check_username(self):
        for rec in self:
            if rec.username and "@" in rec.username:
                raise ValidationError(
                    _("Please use the login username instead of email address")
                )

    @api.depends("name")
    def _compute_shortname(self):
        for rec in self:
            short = rec.name.split("/")[-1]
            if short.endswith(".git"):
                short = short.replace(".git", "")
            for c in ".?()/#@!":
                short = short.replace(c, "_")
            rec.short = short

    @api.depends("username", "password", "name")
    def _compute_url(self):
        for rec in self:
            if rec.login_type == "username":
                url = ""
                for prefix in ["https://", "http://", "ssh://", "ssh+git://"]:
                    if rec.name.startswith(prefix):
                        url = f"{prefix}{rec.username}:{rec.password}@{rec.name[len(prefix):]}"
                        break
                rec.url = url
            else:
                rec.url = rec.name

    def _get_zipped(self, logsio, commit, with_git=False):
        machine = self.machine_id
        with self._temp_repo(machine=machine) as repo_path:
            with machine._shell(repo_path, logsio=logsio) as shell:
                try:
                    shell.checkout_commit(commit)
                    shell.X(["git-cicd", "clean", "-xdff"])
                    excludes = []
                    if not with_git:
                        excludes.append(".git")
                        shell.put(commit, ".sha")
                    return shell.get_zipped(repo_path, excludes)
                finally:
                    shell.rm(repo_path)

    @api.model
    def _update_local_mirrors(self):
        for repo in self or self.search([]):
            path = repo.mirror_path
            with repo.machine_id._gitshell(
                repo,
                cwd=repo.machine_id.workspace,
            ) as shell:
                tmppath = path.parent / (path.name + ".tmp")
                shell.remove(tmppath)
                if not shell.exists(path):
                    shell.X(["git-cicd", "clone", repo.url, tmppath])
                    shell.safe_move_directory(tmppath, path)
                with shell.clone(cwd=path) as shell2:
                    shell2.X(["git-cicd", "remote", "update"])
                    # --force needed for robottest release: feature1 file existed there and
                    # so no pull happened; in any case this directory should be clean
                    shell2.X(["git-cicd", "pull", "--force", "--all", "--no-edit"])

    @property
    def mirror_path(self):
        path = self.machine_id._get_volume("source") / (self.short + ".mirror")
        return path

    @contextmanager
    def _ensure_mirror_path(self):
        path = self.mirror_path
        assert bool(path), "A mirror path must be set"
        with self.machine_id._shell() as shell:
            if not shell.exists(path):
                self._update_local_mirrors()

        yield

    def _technical_clone_repo(
        self, path, machine, logsio=None, branch=None, depth=None, maxage=None,
    ):
        with machine._gitshell(
            self, cwd=self.machine_id.workspace, logsio=logsio
        ) as shell:
            with shell.machine._temppath(usage="clone_repo", maxage=maxage) as temppath:
                with self._ensure_mirror_path():
                    shell.X(["git-cicd", "clone", self.mirror_path, temppath])
                    with shell.clone(cwd=temppath) as shell2:
                        shell2.X(["git-cicd", "remote", "remove", "origin"])
                        shell2.X(["git-cicd", "remote", "add", "origin", self.url])
                        shell2.X(["git-cicd", "fetch"])
                        if branch:
                            shell2.X(["git-cicd", "checkout", "-f", branch])
                            shell2.X(
                                [
                                    "git-cicd",
                                    "branch",
                                    f"--set-upstream-to=origin/{branch}",
                                    branch,
                                ]
                            )
                            shell2.X(
                                ["git-cicd", "reset", "--hard", f"origin/{branch}"]
                            )

                    if shell.exists(path):
                        shell.remove(path)
                    shell.safe_move_directory(temppath, path)
                    shell.git_safe_directory(path)

    @contextmanager
    def _temp_repo(
        self, machine, logsio=None, branch=None, depth=None, pull=False, maxage=None
    ):

        with machine._temppath(usage="temporary_repo") as path:
            self._technical_clone_repo(
                path, machine, branch=branch, depth=depth, maxage=maxage
            )
            yield path

    @api.model
    def _sshenv_pullclone(self, defaults=None):
        defaults = defaults or {}
        env = {}
        env.update(defaults)
        env.update({
            "GIT_ASK_YESNO": "false",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_SSH_COMMAND": "ssh -o Batchmode=yes -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no",
        })
        return env

    @contextmanager
    def _get_main_repo(self, logsio=None, machine=None):
        """
        Returns path to _main folder
        """

        self.ensure_one()
        machine = machine or self.machine_id
        if not machine.workspace:
            raise ValidationError(_("Please configure a workspace!"))
        path = self._get_repo_path(machine)

        with machine._shell(cwd=self.machine_id.workspace, logsio=logsio) as shell:
            if not self._is_healthy_repository(shell, path):
                with shell.machine._temppath(usage="clone_repo") as temppath:
                    shell.X(["git-cicd", "clone", self.url, temppath], env=self._sshenv_pullclone())
                    shell.safe_move_directory(temppath, path)
                    del temppath
        yield path

    def _get_remotes(self, shell):
        remotes = shell.X(["git-cicd", "remote", "-v"])["stdout"].strip().split("\n")
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

        if any(x in branch for x in "():?*/\\!\"'"):
            raise InvalidBranchName(branch)
        return branch

    def fetch(self):
        self._cron_fetch()
        return True

    def create_all_branches(self):
        self.ensure_one()
        with LogsIOWriter.GET(self.name, "fetch") as logsio:
            self.env.cr.commit()
            machine = self.machine_id
            with self._get_main_repo(logsio=logsio) as repo_path:
                with pg_advisory_lock(
                    self.env.cr,
                    self._get_lockname(machine, repo_path),
                    detailinfo=(f"create_all_branches"),
                ):
                    with machine._gitshell(
                        repo=self, cwd=repo_path, logsio=logsio
                    ) as shell:
                        branches = (
                            shell.X(["git-cicd", "branch", "-a"])["stdout"]
                            .strip()
                            .splitlines()
                        )
                        for branch in branches:
                            branch = branch.split("/")[
                                -1
                            ]  # remotes/origin/isolated_unittests --> isolated_unittests
                            branch = self._clear_branch_name(branch)

                            branches = (
                                self.env["cicd.git.branch"]
                                .with_context(active_test=False)
                                .search(
                                    [("name", "=", branch), ("repo_id", "=", self.id)]
                                )
                            )
                            if not branches:
                                branches.create(
                                    {
                                        "repo_id": self.id,
                                        "name": branch,
                                    }
                                )
        return True

    @api.model
    def _cron_fetch(self):
        repos = self
        if not repos:
            repos = self.search([("autofetch", "=", True)])

        for repo in repos:
            repo.with_delay(
                identity_key=(f"queuejob-fetch-{repo.short}")
            )._queuejob_fetch()

    def _queuejob_fetch(self):
        self.ensure_one()
        logsio = None
        try:
            if not self.login_type:
                raise ValidationError(f"Login-Type missing for {self.name}")
            with LogsIOWriter.GET(self.name, "fetch") as logsio:
                self.env.cr.commit()

                with self._get_main_repo(logsio=logsio) as repo_path:
                    with self.machine_id._gitshell(
                        repo=self, cwd=repo_path, logsio=logsio
                    ) as shell:
                        self.env.cr.commit()
                        updated_branches = set()

                        for remote in self._get_remotes(shell):

                            fetch_output = (
                                shell.X(["git-cicd", "fetch", remote])["stderr"]
                                .strip()
                                .split("\n")
                            )

                            fetch_info = list(
                                filter(lambda x: " -> " in x, fetch_output)
                            )

                            for fi in fetch_info:
                                while "  " in fi:
                                    fi = fi.replace("  ", " ")
                                fi = fi.strip()
                                if "[new tag]" in fi:
                                    continue
                                elif "[new branch]" in fi:
                                    branch = (
                                        fi.replace("[new branch]", "")
                                        .split("->")[0]
                                        .strip()
                                    )
                                else:
                                    branch = fi.split("/")[-1]
                                try:
                                    branch = self._clear_branch_name(branch)
                                except InvalidBranchName:
                                    logsio.error("Invalid Branch name: {branch}")
                                    continue

                                if not branch.startswith(self.release_tag_prefix):
                                    updated_branches.add(branch)

                            del fetch_info

                        if not updated_branches:
                            return

                        for branch in set(updated_branches):
                            self._fetch_branch(branch)
                            del branch

        except Exception:
            msg = traceback.format_exc()
            if logsio:
                logsio.error(msg)
            logger.error("error", exc_info=True)
            raise

    def _fetch_branch(self, branch):
        assert isinstance(branch, str)
        self.ensure_one()
        self.with_delay(
            identity_key=(
                "fetch_updated_branch_" f"{self.short or self.name}_branch:{branch}"
            ),
        )._cron_fetch_update_branches(
            {
                "updated_branches": [branch],
            }
        )

    def _clean_remote_branches(self, branches):
        """
        origin/pre_master1']  --> pre_master1
        """
        for branch in branches:
            if "->" in branch:
                continue
            yield branch.split("/")[-1].strip()

    def _checkout_branch_recreate_repo_on_need(self, shell, branch):
        try:
            shell.checkout_branch(branch, nosubmodule_update=True)
        except Exception as ex:
            shell.logsio.error(ex)

            severity = self._exception_meaning(ex)
            if severity == "broken":
                shell.logsio.info("Recreating main folder")
                shell.rm(shell.cwd)
                self.clone_repo(shell.machine, shell.cwd, shell.logsio)
                shell.checkout_branch(branch, nosubmodule_update=True)
            else:
                raise

    def _pull_hard_reset(self, shell, branch):
        """
        * avoid fast forward git extra commits
        * deletes local existance of branch

        """
        shell.X(["git-cicd", "checkout", self.default_branch, "-f"])
        if shell.branch_exists(branch):
            shell.X(["git-cicd", "branch", "-D", branch], allow_error=True)
        shell.X(["git-cicd", "checkout", branch, "--"])
        shell.X(["git-cicd", "reset", "--hard", f"origin/{branch}"])
        shell.X(["git-cicd", "clean", "-xdff"])

        # # remove existing instance folder to refetch
        # not needed; the branch tries to pull - on an error
        # it is rebuilt then

    def _pull(self, shell, branch):
        # option P makes .git --> .git/
        shell.X(["ls -pA |grep -v \\.git\\/ |xargs rm -Rf"])
        shell.X(["git-cicd", "pull"], env=self._sshenv_pullclone())
        shell.X(["git-cicd", "checkout", "-f", branch])

    def _prepare_pulled_branch(self, shell, branch):
        try:
            logsio = shell.logsio
            logsio.info(f"Pulling {branch}...")
            shell.X(["git-cicd", "fetch", "origin", branch], env=self._sshenv_pullclone())
            logsio.info(f"pulled {branch}...")

            self._checkout_branch_recreate_repo_on_need(shell, branch)
            self._pull_hard_reset(shell, branch)

        except Exception as ex:
            logger.error("error", exc_info=True)
            if self._exception_meaning(ex) == "retry":
                raise RetryableJobError(str(ex), seconds=10, ignore_retry=True) from ex
            raise

    def _cron_fetch_update_branches(self, data):
        repo = self.sudo()  # may be triggered by queuejob
        # checkout latest / pull latest
        updated_branches = data["updated_branches"]

        with LogsIOWriter.GET(repo.name, "fetch") as logsio:
            repo = repo.with_context(active_test=False)
            repo._update_local_mirrors()
            machine = repo.machine_id

            with self._temp_repo(machine, logsio=logsio) as repo_path:
                with repo.machine_id._gitshell(
                    repo, cwd=repo_path, logsio=logsio
                ) as shell:

                    for branch in updated_branches:
                        repo._prepare_pulled_branch(shell, branch)

                    # if completely new then all branches:
                    if not repo.branch_ids:
                        for branch in (
                            shell.X(["git-cicd", "branch"])["stdout"]
                            .strip()
                            .split("\n")
                        ):
                            branch = self._clear_branch_name(branch)
                            updated_branches.append(branch)

                    for branch in updated_branches:
                        shell.checkout_branch(
                            branch, cwd=repo_path, nosubmodule_update=True
                        )
                        name = branch
                        del branch

                        date_registered = arrow.utcnow().strftime(DTF)
                        if not (
                            branch := repo.branch_ids.filtered(lambda x: x.name == name)
                        ):
                            branch = repo.branch_ids.create(
                                {
                                    "name": name,
                                    "date_registered": date_registered,
                                    "repo_id": repo.id,
                                }
                            )

                        if not branch.active and repo.revive_branch_on_push:
                            branch.active = True

                        shell.checkout_branch(repo.default_branch, cwd=repo_path)
                        del name
                        branch.env.cr.commit()

                    if not repo.branch_ids and not updated_branches:
                        if repo.default_branch:
                            updated_branches.append(repo.default_branch)

                    for branch_name in updated_branches:
                        branch = repo.branch_ids.filtered(
                            lambda x: x.name == branch_name
                        )
                        if not branch:
                            continue
                        self._postprocess_branch_updates(
                            shell,
                            branch,
                        )
                    shell.checkout_branch(
                        repo.default_branch, cwd=repo_path, nosubmodule_update=True
                    )

    def _postprocess_branch_updates(self, shell, branch):
        """
        If a branch was updated, then the
        """
        branch._checkout_latest(
            shell, instance_folder=shell.cwd, nosubmodule_update=True
        )
        branch._update_git_commits(shell)
        branch._trigger_rebuild_after_fetch()

    def _is_healthy_repository(self, shell, path):
        self.ensure_one()
        healthy = False
        if shell.exists(path):
            try:
                rc = shell.X(
                    ["git-cicd", "status", "-s"],
                    cwd=path,
                    logoutput=False,
                    allow_error=True,
                )
                if rc["exit_code"]:
                    if ".git/index.lock" in rc["std_err"]:
                        pass
                    else:
                        raise Exception(rc["stderr"])
                healthy = True

            except Exception:
                healthy = False
        return healthy

    def clone_repo(self, machine, path, logsio=None, branch=None):
        with machine._gitshell(self, cwd="", logsio=logsio) as shell:
            if not self._is_healthy_repository(shell, path):
                with pg_advisory_lock(
                    self.env.cr, self._get_lockname(machine, path), "clone_repo"
                ):
                    shell.rm(path)
                    self._technical_clone_repo(path, machine=machine, branch=branch)

    def _merge_commits_on_target(self, shell, target, commits):
        conflicts = []
        history = []
        merged = []

        for commit in commits:
            # we use git functions to retrieve deltas, git sorting and
            # so; we want to rely on stand behaviour git.
            shell.checkout_branch(target)
            res = shell.X(
                ["git-cicd", "merge", commit["commit"].name], allow_error=True
            )
            already = False
            if res["exit_code"]:
                console = "\n".join([res["stdout"], res["stderr"]])
                conflicts.append(
                    {
                        "commit": commit["commit"],
                        "branch": commit["branch"],
                        "merged": merged,
                        "console": console,
                    }
                )
                shell.X(["git", "checkout", "-f"])
                shell.X(["git", "clean", "-xdff"])
            else:
                already = "Already up to date" in res["stdout"]
            history.append(
                {
                    "sha": commit["commit"].name,
                    "already": already,
                    "branch": commit["branch"],
                    "commit": commit["commit"],
                }
            )
            merged.append(commit)

        return history, conflicts

    def _recreate_branch_from_commits(
        self,
        source_branch,
        commits,
        target_branch_name,
        logsio,
        make_info_commit_msg,
    ):
        """
        Creates a new branch with given commit ids based on source.
        Deletes existing target_branch_name.

        If merge conflicts exist, then all not mergable commits are returned.

        :param commits: expects list of dict with key 'branch' and 'commit'

        """
        if not commits:
            return
        self.ensure_one()

        # we use a working repo
        assert target_branch_name
        assert isinstance(commits[0], dict)
        assert commits[0]["branch"]._name == "cicd.git.branch"
        assert commits[0]["commit"]._name == "cicd.git.commit"
        machine = self.machine_id
        history = []  # what was done for each commit
        conflicts = []

        with self._temp_repo(machine=self.machine_id) as repo_path:
            self = self.with_context(active_test=False)
            message_commit = None  # commit sha of the created message commit
            with machine._gitshell(self, cwd=repo_path, logsio=logsio) as shell:

                # clear the current candidate
                shell.checkout_branch(source_branch)
                if shell.branch_exists(target_branch_name):
                    shell.X(["git-cicd", "branch", "-D", target_branch_name])
                shell.logsio.info("Making target branch {target_branch.name}")
                shell.X(
                    ["git-cicd", "checkout", "--no-guess", "-b", target_branch_name]
                )

                history, conflicts = self._merge_commits_on_target(
                    shell, target_branch_name, commits
                )

                message_commit_sha = None
                if make_info_commit_msg:
                    merged_branches = ",".join(map(lambda x: x["branch"].name, history))
                    if not merged_branches:
                        merged_branches = "No branches merged"
                    make_info_commit_msg = make_info_commit_msg.replace(
                        "__branches__", merged_branches
                    )

                    shell.X(
                        [
                            "git-cicd",
                            "commit",
                            "--allow-empty",
                            "-m",
                            make_info_commit_msg,
                        ]
                    )
                    message_commit_sha = shell.X(
                        ["git-cicd", "log", "-n1", "--format=%H"]
                    )["stdout"].strip()

                # https://stackoverflow.com/questions/6656619/git-and-nasty-error-cannot-lock-existing-info-refs-fatal
                shell.X(["git-cicd", "remote", "remove", "origin"])
                shell.X(["git-cicd", "gc", "--prune=now"])
                shell.X(["git-cicd", "remote", "add", "origin", self.url])
                shell.X(["git-cicd", "config", "push.default", "current"])
                shell.X(["git-cicd", "fetch", "origin"], env=self._sshenv_pullclone())
                try:
                    shell.X(["git-cicd", "pull"])
                except Exception as ex:  # pylint: disable=broad-except
                    shell.logsio.error(str(ex))
                shell.X(
                    [
                        "git-cicd",
                        "push",
                        "-f",
                        "--set-upstream",
                        "origin",
                        target_branch_name,
                    ], env=self._sshenv_pullclone()
                )

                if not (
                    target_branch := self.branch_ids.filtered(
                        lambda x: x.name == target_branch_name
                    )
                ):
                    target_branch = self.branch_ids.create(
                        {
                            "repo_id": self.id,
                            "name": target_branch_name,
                        }
                    )
                if not target_branch.active:
                    target_branch.active = True
                target_branch._update_git_commits(shell)
                if message_commit_sha:
                    message_commit = target_branch.commit_ids.filtered(
                        lambda x: x.name == message_commit_sha
                    )
                    message_commit.ensure_one()

        return message_commit, history, conflicts

    def _merge(self, source, dest, set_tags, logsio=None):
        assert source._name in ["cicd.git.branch", "cicd.git.commit"]
        assert dest._name == "cicd.git.branch"
        source.ensure_one()
        dest.ensure_one()

        machine = self.machine_id
        with self._temp_repo(machine=machine) as repo_path:
            with machine._gitshell(self, cwd=repo_path, logsio=logsio) as shell:
                shell.logsio.info(f"Checking out {dest.name}")
                shell.checkout_branch(dest.name)
                commitid = shell.X(["git-cicd", "log", "-n1", "--format=%H"])[
                    "stdout"
                ].strip()
                shell.logsio.info(f"Commit-ID is {commitid}")
                if source._name == "cicd.git.branch":
                    branches = [
                        self._clear_branch_name(x)
                        for x in shell.X(
                            ["git-cicd", "branch", "--contains", commitid]
                        )["stdout"]
                        .strip()
                        .split("\n")
                    ]
                    if source.name in branches:
                        return False
                shell.logsio.info(f"Checking out {source.name}")
                shell.checkout_branch(source.name)
                shell.logsio.info(f"Checking out {dest.name}")
                shell.checkout_branch(dest.name)
                count_lines = len(
                    shell.X(["git-cicd", "diff", "-p", source.name])["stdout"]
                    .strip()
                    .split("\n")
                )
                shell.logsio.info(f"Count lines: {count_lines}")
                shell.X(["git-cicd", "merge", "--no-edit", source.name])
                shell.logsio.info(f"Merged {source.name}")
                for tag in set_tags:
                    shell.logsio.info(f"Setting tag {tag}")
                    shell.X(
                        [
                            "git-cicd",
                            "tag",
                            "-f",
                            tag.replace(":", "_").replace(" ", "_"),
                        ]
                    )
                shell.X(["git-cicd", "remote", "set-url", "origin", self.url])
                shell.logsio.info("Pushing tags")
                shell.X(["git-cicd", "branch", "-u", f"origin/{dest.name}"])
                shell.X(
                    [
                        "git-cicd",
                        "push",
                        "--tags",
                    ]
                )
                shell.logsio.info("Pushing ")
                shell.X(["git-cicd", "pull"])
                shell.X(["git-cicd", "push"])
                mergecommitid = shell.X(["git-cicd", "log", "-n1", "--format=%H"])[
                    "stdout"
                ].strip()

                return count_lines, mergecommitid

    @api.model
    def _cron_inactivate_branches(self):
        for repo in self.search(
            [
                ("never_cleanup", "=", False),
            ]
        ):
            dt = arrow.utcnow().shift(days=-1 * repo.cleanup_untouched).strftime(DTF)

            # try nicht unbedingt notwendig; bei __exit__
            # wird ein close aufgerufen
            branches = repo.branch_ids.filtered(
                lambda x: x.last_access or x.date_registered
            ).filtered(
                lambda x: max(
                    [
                        arrow.get(x.last_access or x.date_registered)
                        .replace(tzinfo=None)
                        .datetime,
                        arrow.get(x.date_reactivated or arrow.utcnow().shift(weeks=-10))
                        .replace(tzinfo=None)
                        .datetime,
                    ]
                ).strftime(DTF)
                < dt
            )

            # dont touch release branches
            releases = self.env["cicd.release"].search([("repo_id", "=", repo.id)])
            names = list(releases.mapped("branch_id.name"))
            names += list(releases.mapped("item_ids.item_branch_id.name"))
            branches = branches.filtered(lambda x: x.name not in names)
            del names

            # keep branches with recents updates
            def outdated_commits(branch):
                if not branch.latest_commit_id:
                    return True
                if not branch.latest_commit_id.date:
                    return True
                return bool(branch.latest_commit_id.date.strftime(DTF) < dt)

            branches = branches.filtered(outdated_commits)

            for branch in branches:
                branch.with_delay(identity_key=f"deactivate-branch-{branch.id}").write(
                    {"active": False}
                )
            self.env.cr.commit()

            del dt

    def new_branch(self):
        return {
            "view_type": "form",
            "res_model": "cicd.git.branch.new",
            "context": {
                "default_repo_id": self.id,
            },
            "views": [(False, "form")],
            "type": "ir.actions.act_window",
            "flags": {
                "form": {
                    "action_buttons": False,
                    "initial_mode": "edit",
                    # 'footer_to_buttons': False,
                    # 'not_interactiable_on_create': False,
                    # 'disable_autofocus': False,
                    # 'headless': False,  9.0 and others?
                }
            },
            "options": {
                # needs module web_extended_actions
                "hide_breadcrumb": True,
                "replace_breadcrumb": True,
                "clear_breadcrumbs": True,
            },
            "target": "new",
        }

    def _get_base_url(self):
        self.ensure_one()
        url = self.url
        if not url.endswith("/"):
            url += "/"
        if url.startswith("ssh://git@"):
            url = url.replace("ssh://git@", "https://")
        return url

    def _get_url(self, ttype, object, object2=None):
        self.ensure_one()
        if self.ttype == "gitlab":
            if ttype == "commit":
                return self._get_base_url() + "-/commit/" + object.name
            elif ttype == "compare":
                return (
                    self._get_base_url() + "-/compare?from=" + object + "&to=" + object2
                )
            else:
                raise NotImplementedError()
        elif self.ttype == "bitbucket":
            if ttype == "commit":
                return self._get_base_url() + "/commits/" + object.name
            else:
                raise NotImplementedError()
        else:
            raise NotImplementedError()

    def _exception_meaning(self, ex):
        """
        Checks error message from git if the repo is broken and
        needs recreation.  Recreation is a problem, because current state
        is lost, so this should be avoided if possible.
        """

        exmessage = str(ex)
        if any(
            x in exmessage
            for x in [
                ".git/index.lock",
            ]
        ):
            return "retry"
        if any(
            x in exmessage
            for x in [
                "could not read Username for",
            ]
        ):
            return "hangs_not_broken"
        return "broken"

    def _has_rights_for_password(self):
        return self.env.user.has_group("cicd.group_manager") or self.env.user.has_group(
            "base.group_system"
        )

    def read(self, fields=None, load="_classic_read"):
        result = super().read(fields=fields, load=load)
        if not self._has_rights_for_password() and "password" in (fields or []):

            def remove_password(record):
                record["password"] = False
                return record

            result = list(map(remove_password, result))
        return result

    def apply_test_settings_to_all_branches(self):
        for rec in self:
            for branch in rec.branch_ids:
                rec.apply_test_settings(branch)

    @api.model
    def _intelligent_springclean(self, hours=8):
        breakpoint()
        branch_by_projectname = dict(
            (x.project_name, x) for x in self.env["cicd.git.branch"].search([])
        )
        inactive_branches = list(
            filter(
                bool,
                self.env["cicd.git.branch"]
                .search([("active", "=", False), ("repo_id", "=", self.id)])
                .mapped("project_name"),
            )
        )
        release_branches = (
            self.env["cicd.release.item"]
            .search([])
            .mapped("item_branch_id.project_name")
        )
        all_repos = self.search([])
        for repo in all_repos:
            machine = repo.machine_id
            srcfolder = machine._get_volume("source")
            with machine._shell(cwd=srcfolder) as shell:

                all_folders = list(
                    map(
                        lambda x: x.split("/")[-1],
                        shell.X(
                            [
                                "find",
                                ".",
                                "-maxdepth",
                                "1",
                                "-type",
                                "d",
                            ]
                        )["stdout"].splitlines(),
                    )
                )
                breakpoint()

                # remove all tmpfolders and testruns
                for item in filter(
                    lambda x: not x.startswith("_main_")
                    and not ".mirror" in x
                    and (x.startswith("testrun") or ".tmp" in x),
                    all_folders,
                ):
                    item = srcfolder / item
                    if shell.get_age_hours(item) > hours:
                        shell.remove(srcfolder / item)

                for folder in all_folders:
                    if not folder.startswith("testrun"):
                        continue
                    # testrun_cicd.9998,10002,10004,10006,10008.tmp.3d0d23b6-19ab-43e9-8890-1
                    match = re.findall("testrun_([^\d]*)_\d", folder)
                    if not match:
                        continue
                    model = match[0].replace("_", ".")
                    ids = list(map(int, re.findall("\d+", folder)))
                    lines = [
                        x
                        for x in self.env[model].browse(ids)
                        if x.exists() and x.state not in ["success", "failed"]
                    ]
                    if not lines:
                        shell.remove(srcfolder / folder)

                for folder in all_folders:
                    for project_name in inactive_branches:
                        if folder == project_name:
                            branch_by_projectname[project_name].with_delay(
                                identity_key=f"purge_{folder}"
                            ).purge_instance_folder()

                for folder in all_folders:
                    if ".tmp." not in folder:
                        continue
                    if shell.get_age_hours(srcfolder / item) > hours:
                        shell.remove(srcfolder / folder)

                for folder in all_folders:
                    if folder in release_branches:
                        shell.remove(srcfolder / folder)

                    for project_name in release_branches:
                        if folder == project_name:
                            branch_by_projectname[project_name].with_delay(
                                identity_key=f"purge_{folder}"
                            ).purge_instance_folder()
