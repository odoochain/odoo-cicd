from . import pg_advisory_lock
import traceback
import json
from contextlib import contextmanager
from odoo import fields
from pathlib import Path
import os
import arrow
from odoo import _, api, models, fields
from odoo.exceptions import UserError, RedirectWarning, ValidationError
import inspect
from pathlib import Path
from odoo.addons.queue_job.exception import RetryableJobError
import logging
from .repository import InvalidBranchName
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT as DTF
from odoo.tools import DEFAULT_SERVER_DATE_FORMAT
from . shell_executor_base import HandledProcessOutputException

current_dir = Path(
    os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
)


def effify(text, values):
    for line in text.splitlines():
        line2 = eval('f"' + line + '"', values).strip()
        yield line2


logger = logging.getLogger(__name__)


class Branch(models.Model):
    _inherit = "cicd.git.branch"

    def _prepare_a_new_instance(self, shell, **kwargs):
        dump = self.dump_id or self.repo_id.default_simulate_install_id_dump_id
        if not dump:
            self._reset_db(shell, **kwargs)
        else:
            self.backup_machine_id = dump.machine_id
            self.dump_id = dump
        if self.dump_id:
            self._restore_dump(shell, dump=dump, **kwargs)
        else:
            self._reset_db(shell, **kwargs)
        self._update_all_modules(shell, **kwargs)

    def _update_odoo(self, shell, **kwargs):
        if (
            self.block_updates_until
            and self.block_updates_until > fields.Datetime.now()
        ):
            raise RetryableJobError(
                "Branch is blocked - have to wait", seconds=10, ignore_retry=True
            )

        tasks = self.task_ids.with_context(prefetch_fields=False).filtered(
            lambda x: x.state == "done"
            and x.name in ["_update_all_modules", "_update_odoo"]
        )
        commit = None
        if tasks:
            commit = tasks[0].commit_id.name
        if commit:
            try:
                shell.logsio.info("Updating")
                result = shell.odoo(
                    "update",
                    "--since-git-sha",
                    commit,
                    "--no-dangling-check",
                    "--i18n" if self.update_i18n else "",
                )

                if result["exit_code"]:
                    raise Exception("Error at update")
            except Exception as ex:
                logger.error(ex)
                shell.logsio.error(ex)
                shell.logsio.info(
                    (
                        "Running full update now - "
                        f"update since sha {commit} did not succeed"
                    )
                )
                self._update_all_modules(shell=shell, **kwargs)
        else:
            self._update_all_modules(shell=shell, **kwargs)

    def _update_all_modules(self, shell, **kwargs):
        shell.logsio.info("Reloading")
        self._reload(shell)
        shell.logsio.info("Building")
        self._internal_build(shell)
        shell.logsio.info("Updating")
        shell.odoo(
            "update", "--no-dangling-check", "--i18n" if self.update_i18n else ""
        )
        shell.logsio.info("Upping")
        shell.odoo("up", "-d")

    def _update_installed_modules(self, shell, **kwargs):
        shell.logsio.info("Reloading")
        self._reload(shell)
        shell.logsio.info("Building")
        self._internal_build(shell)
        shell.logsio.info("Updating")
        shell.odoo(
            "update",
            "--no-dangling-check",
            "--installed-modules",
            "--i18n" if self.update_i18n else "",
        )
        shell.logsio.info("Upping")
        shell.odoo("up", "-d")

    def _simple_docker_up(self, shell, **kwargs):
        shell.odoo("up", "-d")

    def _reload_and_restart(self, shell, **kwargs):
        self._reload(shell)
        shell.logsio.info("Building")
        self._internal_build(shell)
        shell.logsio.info("Upping")
        shell.odoo("kill")
        self._kill_tmux_sessions(shell)
        shell.odoo("rm")
        shell.odoo("up", "-d")
        self._after_build(shell)

    def _restore_dump(self, shell, dump, **kwargs):
        if not dump:
            raise ValidationError(_("Dump missing - cannot restore"))
        dump = dump or self.dump_id
        if isinstance(dump, int):
            dump = self.env["cicd.dump"].browse(dump)
        if isinstance(dump, str):
            dump_name = dump.name
            dump_date = False
        else:
            dump_name = dump.name
            dump_date = dump.date_modified

        del dump

        self._reload(shell)
        shell.logsio.info("Reloading")
        shell.odoo("reload")
        shell.logsio.info("Building")
        self._internal_build(shell)
        shell.logsio.info("Downing")
        shell.odoo("kill")
        shell.odoo("rm")
        if "wodoo-bin" in shell.odoo("show-dump-type", dump_name)["stdout"].lower():
            raise ValidationError(
                "Cannot restore wodoobin dump on cicd (everything would be lost)"
            )
        shell.logsio.info(f"Restoring {dump_name}")
        shell.odoo("-f", "restore", "odoo-db", "--no-remove-webassets", dump_name)
        if self.remove_web_assets_after_restore:
            shell.odoo("-f", "remove-web-assets")
        self.last_restore_dump_name = dump_name
        self.last_restore_dump_date = dump_date
        if self.env.context.get("task"):
            self.env.context["task"].sudo().with_delay().write({"dump_used": dump_name})
        shell.machine.sudo().postgres_server_id.with_delay().update_databases()
        self.env.cr.commit()
        self.update_all_modules()
        shell.machine.sudo().postgres_server_id.with_delay().update_databases()
        self.last_snapshot = False
        self._after_build(shell=shell)
        shell.machine.sudo().postgres_server_id.with_delay().update_databases()

    def _docker_start(self, shell, **kwargs):
        shell.odoo("up", "-d")
        self.machine_id._fetch_psaux_docker_containers()

    def _docker_stop(self, shell, **kwargs):
        shell.odoo("kill")
        self.machine_id._fetch_psaux_docker_containers()

    def _docker_remove(self, shell, **kwargs):
        shell.odoo("kill")
        shell.odoo("rm")
        self.machine_id._fetch_psaux_docker_containers()

    def _turn_into_dev(self, shell, **kwargs):
        shell.odoo("turn-into-dev")

    def _reload(
        self,
        shell,
        project_name=None,
        settings=None,
        commit=None,
        registry=None,
        force_instance_folder=None,
        no_update_images=False,
        no_checkout=False,
        **kwargs,
    ):

        cwd = force_instance_folder or self._make_sure_source_exists(shell)

        try:
            with shell.clone(cwd=cwd) as shell:
                self._make_instance_docker_configs(
                    shell,
                    forced_project_name=project_name,
                    settings=settings,
                    registry=registry,
                )
                self._collect_all_files_by_their_checksum(shell)
                if commit and not no_checkout:
                    shell.checkout_commit(commit)
                params = []
                if no_update_images:
                    params += ["--no-update-images"]
                shell.odoo("reload", *params)
                if self._is_hub_configured(shell):
                    shell.odoo("login")
        except Exception as ex:
            msg = str(ex)
            if (
                "Warning: Permanently added 'github.com" in msg
                and "Command '['git', 'pull']' returned non-zero exit status 1" in msg
            ):
                raise RetryableJobError(
                    (
                        "Could not pull from github.com - "
                        f"check network please: {msg}"
                    ),
                    seconds=10,
                    ignore_retry=True,
                )
            raise

    def _is_hub_configured(self, shell):
        output = shell.odoo("config", "--full", logoutput=False)["stdout"]
        lines = [x for x in output.split("\n") if "HUB_URL=" in x]
        if lines:
            if len(lines[0]) > len("HUB_URL="):
                return True
        return False

    def _build(self, shell, **kwargs):
        self._reload(shell, **kwargs)
        self._internal_build(shell)

    def _dump(self, shell, volume=None, filename=None, **kwargs):
        volume = volume or shell.machine._get_volume("dumps")
        machine = shell.machine
        if isinstance(volume, int):
            volume = self.env["cicd.machine.volume"].browse(volume)
            volume = Path(volume.name)

        shell.logsio.info(f"Dumping to {machine.name}:{volume}")
        filename = filename or self.backup_filename or (self.project_name + ".dump.gz")
        assert isinstance(filename, str)

        if "/" in filename:
            raise ValidationError("Filename mustn't contain slashses!")
        shell.odoo("backup", "odoo-db", str(volume / filename))
        # to avoid serialize access erros which may occur
        machine.with_delay().update_dumps()

    def _update_git_commits(self, shell, force_instance_folder=None, **kwargs):

        self.ensure_one()
        shell.logsio.info(f"Updating commits for {self.project_name}")
        instance_folder = force_instance_folder or self._get_instance_folder(
            self.machine_id
        )

        def _extract_commits():
            # removing the 4 months filter:
            # old branches get stuck and stuck other branches because
            # latest commit # cannot be found, if that filter is active.
            return list(
                filter(
                    bool,
                    shell.X(
                        [
                            "git-cicd",
                            "log",
                            "--pretty=format:%H___%ct",
                            "-n",
                            str(self.repo_id.analyze_last_n_commits),
                            # "--since='last 4 months'",
                        ],
                        logoutput=False,
                        cwd=instance_folder,
                    )["stdout"]
                    .strip()
                    .split("\n"),
                )
            )

        commits = _extract_commits()
        commits = [list(x.split("___")) for x in commits]
        for commit in commits:
            commit[1] = arrow.get(int(commit[1]))

        all_commits = (
            self.env["cicd.git.commit"].with_context(active_test=False).search([])
        )
        all_commits = dict((x.name, x) for x in all_commits)

        for icommit, commit in enumerate(commits):
            sha, date = commit
            if sha in all_commits:
                cicd_commit = all_commits[sha]
                if self not in cicd_commit.branch_ids:
                    # memory error otherwise - reported by MT
                    # cicd_commit.branch_ids |= self
                    self.commit_ids |= cicd_commit
                continue

            env = {"TZ": "UTC0"}
            if date is None:
                line = (
                    shell.X(
                        [
                            "git-cicd",
                            "log",
                            sha,
                            "-n1",
                            "--pretty=format:%ct",
                        ],
                        logoutput=False,
                        cwd=instance_folder,
                        env=env,
                    )["stdout"]
                    .strip()
                    .split(",")
                )

                if not line or not any(line):
                    continue
                date = arrow.get(int(line[0]))

            shell.logsio.info(
                (
                    f"Getting detail information of sha "
                    f"{sha} ({icommit} / {len(commits)})"
                )
            )

            info = (
                shell.X(
                    [
                        "git-cicd",
                        "log",
                        sha,
                        "--date=format:%Y-%m-%d %H:%M:%S",
                        "-n1",
                    ],
                    logoutput=False,
                    cwd=instance_folder,
                    env=env,
                )["stdout"]
                .strip()
                .split("\n")
            )

            def _get_item(name):
                for line in info:
                    if line.strip().startswith(f"{name}:"):
                        return line.split(":", 1)[-1].strip()

            def _get_body():
                for i, line in enumerate(info):
                    if not line:
                        return info[i + 1 :]

            text = ("\n".join(_get_body())).strip()
            self.commit_ids = [
                [
                    0,
                    0,
                    {
                        "name": sha,
                        "author": _get_item("Author"),
                        "date": date.strftime("%Y-%m-%d %H:%M:%S"),
                        "text": text,
                        "branch_ids": [[4, self.id]],
                    },
                ]
            ]

    def _remove_web_assets(self, shell, **kwargs):
        shell.logsio.info("Killing...")
        shell.odoo("kill")
        shell.logsio.info("Calling remove-web-assets")
        shell.odoo("-f", "remove-web-assets")
        shell.logsio.info("Restarting...")
        shell.odoo("up", "-d")

    def _shrink_db(self, shell, **kwargs):
        shell.odoo("cleardb")

    def _anonymize(self, shell, **kwargs):
        shell.odoo("update", "anonymize")
        shell.odoo("anonymize")

    def _cron_run_open_tests(self):
        for testrun in self.env["cicd.test.run"].search([("state", "=", "open")]):
            # observed duplicate starts without eta
            testrun.as_job(
                (
                    "start-open-testrun-"
                    f"{testrun.branch_id.name}-"
                    f"{testrun.commit_id.name}-"
                    f"{testrun.id}"
                ),
                eta=1,
            ).execute()

    def _after_build(self, shell, **kwargs):
        self.last_access = fields.Datetime.now()  # to avoid cycle down
        res = shell.odoo("db", "db-health", "check", allow_error=True)
        if res["exit_code"]:
            return
        shell.odoo("remove-settings", "--settings", "web.base.url,web.base.url.freeze")

        external_url = self.machine_id.external_url
        shell.odoo("update-setting", "web.base.url", external_url)
        shell.odoo("set-ribbon", self.name)
        shell.odoo("prolong")
        shell.odoo("restore-web-icons")

    def _build_since_last_gitsha(self, shell, **kwargs):
        # todo make button
        self._after_build(shell=shell, **kwargs)

    def delete_folder_deferred(self, machine, folder):
        with machine._shell() as shell:
            shell.remove(folder)

    def _checkout_latest(self, shell, instance_folder=None, **kwargs):
        """
        Use this for getting source code. It updates also submodules.

        """
        my_name = self._unblocked("name")

        def _clone_instance_folder(machine, instance_folder):
            # be atomic
            with shell.machine._temppath(usage="clone_repo_at_checkout_latest") as path:
                self.repo_id._technical_clone_repo(
                    path=path,
                    branch=my_name,
                    machine=machine,
                )
                with shell.clone(cwd=path.parent, project_name=None) as shell2:
                    # if work in progress happening in instance_folder;
                    # unlink does not delete the folder, just unlinks, so running processes
                    # still work
                    # path2 will be deleted within two hours by cronjob
                    path2 = shell.machine._temppath(
                        usage="replace_main_folder", maxage=dict(hours=2)
                    )
                    if shell2.exists(instance_folder):
                        shell2.safe_move_directory(instance_folder, path2)
                    shell2.safe_move_directory(path, instance_folder)
                    self.with_delay(
                        eta=arrow.utcnow().shift(hours=3).strftime(DTF)
                    ).delete_folder_deferred(shell2.machine, str(path2))

        machine = shell.machine
        instance_folder = instance_folder or self._get_instance_folder(machine)
        shell.logsio.write_text(f"Updating instance folder {my_name}")
        _clone_instance_folder(machine, instance_folder)
        shell.logsio.write_text(f"Cloning {my_name} to {instance_folder}")

        with shell.clone(cwd=instance_folder) as shell:
            shell.logsio.write_text(f"Checking out {my_name}")
            try:
                shell.X(["git-cicd", "checkout", "-f", my_name])
            except Exception as ex:
                shell.logsio.error(ex)
                shell.rm(instance_folder)
                raise RetryableJobError(
                    "Cleared directory - branch not found - please retry",
                    ignore_retry=True,
                ) from ex

            try:
                shell.X(["git-cicd", "pull"])
            except Exception as ex:  # pylint: disable=broad-except
                shell.logsio.error(
                    ("Error at pulling," f"cloning path {instance_folder} again:\n{ex}")
                )
                shell.rm(instance_folder)
                _clone_instance_folder(machine, instance_folder)

            # delete all other branches:
            res = shell.X(["git-cicd", "branch"])["stdout"].strip().split("\n")
            for branch in list(filter(lambda x: "* " not in x, res)):
                branch = self.repo_id._clear_branch_name(branch)

                if branch == my_name:
                    continue

                shell.X(["git-cicd", "branch", "-D", branch])
                del branch

            current_branch = shell.current_branch()
            if not current_branch:
                raise Exception("Somehow no current branch found")
            try:
                branch_in_dir = self.repo_id._clear_branch_name(current_branch)
            except InvalidBranchName:
                branch_in_dir = None
            if branch_in_dir != my_name:
                shell.rm(instance_folder)

                raise Exception(
                    (
                        f"Branch could not be checked out!"
                        f"Was {branch_in_dir} - but should be {my_name}"
                    )
                )

            shell.logsio.write_text("Clean git")
            shell.X(["git-cicd", "clean", "-xdff"], retry=10)

            shell.logsio.write_text("Updating submodules")
            shell.X(["git-cicd", "submodule", "update", "--recursive", "--init"])

            shell.logsio.write_text("Getting current commit")
            commit = shell.X(["git-cicd", "rev-parse", "HEAD"])["stdout"].strip()
            shell.logsio.write_text(commit)

        return str(commit)

    def _cron_inactivity_cycle_down(self):
        for machine in self.env["cicd.machine"].search([("ttype", "=", "dev")]):
            self.env.cr.commit()
            containers = machine._get_containers()
            containers = {k: v for k, v in containers.items() if v == "running"}
            machine_branches = self.with_context(
                active_test=False,
                prefetch_fields=False,
            ).search([("repo_id.machine_id", "=", machine.id)])

            to_check = machine_branches.filtered(
                lambda x: any(x.project_name + "_" in line for line in containers)
            )
            self.env.cr.commit()
            for rec in to_check:
                name = rec.name
                logger.info(f"Checking {name} for to cycle down.")
                last_access = arrow.get(rec._unblocked("last_access") or "1980-04-04")
                uptime = (arrow.get() - last_access).total_seconds()
                if uptime <= rec._unblocked("cycle_down_after_seconds"):
                    continue

                # dont disturb running tasks
                if rec.task_ids.filtered(lambda x: x.state not in ["done", "failed"]):
                    continue

                rec.with_delay(
                    identity_key=f"cycle_down:{rec.name}"
                )._cycle_down_instance()
                rec.env.cr.commit()

    def _cycle_down_instance(self):
        self.ensure_one()
        with self.machine_id._shell(
            cwd=self.project_path, project_name=self.project_name
        ) as shell:
            self.env.cr.commit()
            with self._extra_env() as x_rec:
                name = x_rec.name
            if shell.logsio:
                shell.logsio.info(
                    ("Cycling down instance " f"{name} due to inactivity")
                )
            logger.info(("Shutting down instance due to inactivity " f"{name}"))
            shell.odoo("kill", allow_error=True)
            shell.odoo("rm", allow_error=True)

    def _make_instance_docker_configs(
        self, shell, forced_project_name=None, settings=None, registry=None
    ):
        home_dir = shell._get_home_dir()
        machine = shell.machine
        project_name = forced_project_name or self._unblocked("project_name")

        shell.put(
            self._get_docker_compose(project_name),
            (f"{home_dir}" f"/.odoo/docker-compose.{project_name}.yml"),
        )

        if not machine.postgres_server_id:
            raise ValidationError(_(f"Please configure a db server for {machine.name}"))

        shell.put(
            self._get_settings(project_name, machine, registry, settings),
            home_dir + f"/.odoo/settings.{project_name}",
        )

    def _get_docker_compose(self, project_name):
        content = (
            (current_dir.parent / "data" / "template_cicd_instance.yml.template")
        ).read_text()

        values = os.environ.copy()
        values["PROJECT_NAME"] = project_name
        content = content.format(**values)
        return content

    def _get_settings(self, project_name, machine, registry, custom_settings):
        self.ensure_one()

        content = (
            (current_dir.parent / "data" / "template_cicd_instance.settings")
        ).read_text()
        assert machine

        content += "\n" + (self._unblocked("reload_config") or "")
        if custom_settings:
            content += "\n" + custom_settings

        registry = registry or self.repo_id.registry_id
        if registry:
            content += f"\nHUB_URL={registry.hub_url}"

        if self.enable_snapshots:
            content += (
                "\nRUN_POSTGRES=1"
                "\nDB_HOST=postgres"
                "\nDB_PORT=5432"
                "\nDB_USER=odoo"
                "\nDB_PASSWORD=odoo"
                "\n"
            )

        content = "\n".join(
            effify(
                content, dict(branch=self, project_name=project_name, machine=machine)
            )
        ).strip()
        return content

    def _cron_autobackup(self):
        for rec in self.search([("autobackup", "=", True)]):
            rec._make_task(
                "_dump",
                machine=rec.backup_machine_id,
            )

    def _fetch_from_registry(self, shell):
        if self._is_hub_configured(shell):
            shell.odoo("docker-registry", "login")
            shell.odoo("docker-registry", "regpull", allow_error=True)

    def _push_to_registry(self, shell):
        if self._is_hub_configured(shell):
            shell.odoo("docker-registry", "login")
            shell.odoo("docker-registry", "regpush")

    def _internal_build(self, shell):
        try:
            self._fetch_from_registry(shell)
        except Exception as ex:  # pylint: disable=broad-except
            shell.logsio.error("Could not pull from registry. Trying to build.")
            shell.logsio.error(ex)
        shell.odoo("build")
        self._push_to_registry(shell)

    def _reset_db(self, shell, **kwargs):
        self._reload(shell)
        self._internal_build(shell)
        shell.odoo("-f", "db", "reset")
        shell.odoo("update", "--no-dangling-check")
        try:
            shell.odoo("turn-into-dev")  # why commented?
        except Exception:  # pylint: disable=broad-except
            pass
        self.last_snapshot = False
        self._after_build(shell=shell, **kwargs)

    def _compress(self, shell, compress_job_id, **kwargs):
        breakpoint()
        self.ensure_one()
        compressor = self.env["cicd.compressor"].sudo().browse(compress_job_id)

        # get list of files
        filename = compressor._get_latest_dump(logsio=shell.logsio)

        # if the machines are the same, then just rewrite destination path
        # if machines are different then copy locally and then put it on the
        # machine
        self = self.with_context(testrun=f"compressor_{compress_job_id}")
        dest_file_path = shell.machine._get_volume("dumps") / self.project_name
        compressor.last_log = ""

        # release db resources:
        self.env.cr.commit()
        source_machine = compressor.source_volume_id.machine_id
        try:
            with source_machine._put_temporary_file_on_machine(
                shell.logsio,
                compressor.source_volume_id.name + "/" + filename,
                shell.machine,
                dest_file_path,
            ) as effective_dest_file_path:
                compressor.last_input_size = int(
                    shell.X(["stat", "-c", "%s", effective_dest_file_path])[
                        "stdout"
                    ].strip()
                )

                assert shell.machine.ttype == "dev"
                breakpoint()

                # change working project/directory
                with self._tempinstance(self.env.context.get('testrun')) as shell:
                    shell.odoo("-f", "restore", "odoo-db", effective_dest_file_path)
                    shell.logsio.info("Clearing DB...")
                    breakpoint()
                    output = shell.odoo("-f", "cleardb", allow_error=True)
                    if output['exit_code']:
                        raise HandledProcessOutputException(output)
                    compressor.last_log += f"\nMinimized DB"
                    if compressor.anonymize:
                        shell.logsio.info("Anonymizing DB...")
                        output = shell.odoo("-f", "anonymize", allow_error=True)
                        if output['exit_code']:
                            raise HandledProcessOutputException(output)
                        compressor.last_log += f"\nAnonymized DB"
                        self.env.cr.commit()

                    shell.logsio.info("Dumping compressed dump")
                    dump_path = shell.machine._get_volume("dumps") / self.project_name
                    shell.odoo("backup", "odoo-db", dump_path)
                    compressor.last_output_size = shell.file_size(dump_path)
                    self.env.cr.commit()
                    compressor.last_log += f"\nDump created transferring to destinations"

                    dump = shell.get(dump_path)
                    for output in compressor.output_ids:
                        with output.volume_id.machine_id._shell() as shell_dest:
                            dest_path = output.volume_id.name
                            dest_path = dest_path + "/" + output.output_filename
                            compressor.last_log += (
                                f"\nPutting dump to {dest_path} on "
                                f"{shell_dest.machine.name}"
                            )
                            shell_dest.put(dump, dest_path)

                    compressor.date_last_success = fields.Datetime.now()
                    self.env.cr.commit()

        except HandledProcessOutputException as ex:
            compressor.last_log = f"\n{ex.console}\n{compressor.last_log}"
            self.env.cr.commit()
            raise

        except Exception as ex:
            msg = traceback.format_exc()
            compressor.last_log += (
                f"{str(ex)}\n"
                f"{msg}"
            )
        else:
            compressor.last_log = "Success - no error"

    def _make_sure_source_exists(self, shell):
        instance_folder = self._get_instance_folder(shell.machine)
        self.ensure_one()
        with self._extra_env() as x_self:
            healthy = x_self.repo_id._is_healthy_repository(shell, instance_folder)

        if not healthy:
            try:
                self._checkout_latest(shell)
            except Exception:
                shell.rm(instance_folder)
                self._checkout_latest(shell)
        return instance_folder

    def _collect_all_files_by_their_checksum(self, shell):
        """

        STOP!
        odoo calls isdir on symlink which fails unfortunately
        hardlink on dir does not work
        so new idea needed

        Odoo stores its files by sha. If a db is restored then usually
        it has to rebuild the assets.
        And files are not available.
        To save space we make the following:

        ~/.odoo/files/filestore/project_name1/00/000000000
        ~/.odoo/files/filestore/project_name2/00/000000000

                           |
                           |
                           |
                          \ /
                           W

        ~/.odoo/files/filestore/_all/00/000000000
        ~/.odoo/files/filestore/_all/00/000000000
        ~/.odoo/files/filestore/project_name1 --> ~/.odoo/files/filestore/_all
        ~/.odoo/files/filestore/project_name2 --> ~/.odoo/files/filestore/_all
        """
        return  # see comment

        python_script_executed = """
from pathlib import Path
import os
import subprocess
import shutil
base = Path(os.path.expanduser("~/.odoo/files/filestore"))

ALL_FOLDER = base / "_all"
ALL_FOLDER.mkdir(exist_ok=True, parents=True)

for path in base.glob("*"):
    if path == ALL_FOLDER:
        continue
    if path.is_dir():
        subprocess.check_call(["rsync", str(path) + "/", str(ALL_FOLDER) + "/", "-ar"])
        shutil.rmtree(path)
        path.symlink_to(ALL_FOLDER, target_is_directory=True)

        """
        shell.put(python_script_executed, ".cicd_reorder_files")
        try:
            shell.X(["python3", ".cicd_reorder_files"])
        finally:
            shell.rm(".cicd_reorder_files")

    def _kill_tmux_sessions(self, shell):
        for rec in self:
            machine = rec.machine_id
            with machine._shell() as shell:
                machine.make_login_possible_for_webssh_container()
                shell.X(
                    [
                        "sudo",
                        "pkill",
                        "-9",
                        "-f",
                        "-u",
                        shell.machine.ssh_user_cicdlogin,
                        f"new-session.*-s.*{rec.project_name}",
                    ],
                    allow_error=True,
                )

    def new_branch(self):
        self.ensure_one()
        if not self.env.user.has_group("cicd.group_make_branches"):
            raise UserError("Missing rights to create branch.")
        action = self.repo_id.new_branch()
        action["context"].update(
            {
                "default_source_branch_id": self.id,
                "default_dump_id": self.dump_id.id,
            }
        )
        return action

    def _get_settings_isolated_run(self, dbname="odoo"):
        return (
            "RUN_POSTGRES=1\n"
            "DB_HOST=postgres\n"
            "DB_USER=odoo\n"
            "DB_PWD=odoo\n"
            "DB_PORT=5432\n"
            f"DBNAME={dbname}\n"
            "RUN_CRONJOBS=0\n"
            "RUN_QUEUEJOBS=0\n"
            "RUN_POSTGRES=1\n"
            "RUN_ROBOT=0\n"
            "RUN_PROXY_PUBLISHED=0\n"
        )

    @contextmanager
    def _tempinstance(self, uniqueappendix, commit=None, dbname="odoo"):
        settings = self._get_settings_isolated_run(dbname=dbname)
        machine = self.machine_id
        assert machine.ttype == "dev"
        self = self.with_context(testrun=uniqueappendix)
        with pg_advisory_lock(
            self.env.cr,
            self.project_name,
            (
                f"project_name: {self.project_name}, "
                f"temporary instance testrun={uniqueappendix}"
            ),
        ):
            with self.repo_id._temp_repo(machine=machine) as repo_path:
                with machine._shell(
                    cwd=repo_path,
                    project_name=self.project_name,
                ) as shell:
                    breakpoint()
                    self._reload(
                        shell,
                        project_name=self.project_name,
                        commit=commit,
                        settings=settings,
                        force_instance_folder=repo_path,
                    )
                    config = shell.odoo("config", "--full")["stdout"].splitlines()
                    for conf in config:
                        if conf.strip().startswith("DB_HOST:"):
                            assert "DB_HOST: postgres" == conf.strip()
                    shell.odoo("regpull", "postgres", allow_error=True)
                    shell.odoo("build", "postgres")
                    shell.odoo("down", "-v", force=True, allow_error=True)
                    shell.odoo("up", "-d", "postgres")
                    shell.wait_for_postgres()
                    shell.odoo("pghba-conf-wide-open", "--no-scram")
                    shell.odoo("kill")
                    try:
                        yield shell
                    finally:
                        shell.odoo("down", "-v", allow_error=True, force=True)
                        repo_path and len(repo_path.parts) > 2 and shell.rm(
                            repo_path
                        )  # make sure to avoid rm /

    def _ensure_dump(self, ttype, commit, dumptype=None, dbname="odoo"):
        """
        Makes sure that a dump for installation of base/web module exists.
        """
        assert ttype in ["full", "base"]
        assert isinstance(commit, str)
        self.ensure_one()

        dest_path = self._ensure_dump_get_dest_path(ttype, commit, dumptype)

        with self.machine_id._shell() as shell:
            if shell.exists(dest_path):
                return dest_path

        with self._tempinstance("ensuredump", commit=commit, dbname=dbname) as shell:
            shell.logsio.info(f"Creating dump file {dest_path}")
            shell.odoo("up", "-d", "postgres")
            shell.wait_for_postgres()
            shell.odoo("db", "reset", force=True)
            shell.wait_for_postgres()
            shell.logsio.info(f"Dumping to {dest_path}")
            if ttype == "full":
                shell.odoo("update", timeout=60 * 30)
                shell.odoo('turn-into-dev')
                shell.wait_for_postgres()
            params = ["backup", "odoo-db", dest_path]
            if dumptype:
                params += ["--dumptype", dumptype]
            shell.odoo(*params)
        return dest_path

    def _ensure_dump_get_dest_path(self, ttype, commit, dumptype):
        if commit:
            assert isinstance(commit, str)
        machine = self.machine_id
        instance_folder = self._get_instance_folder(machine)
        settings = self._get_settings_isolated_run()
        with machine._shell(
            cwd=instance_folder,
            project_name=self.project_name,
        ) as shell:
            path = Path(shell.machine._get_volume("dumps"))

            if ttype in ['base']:
                self._checkout_latest(shell)
                self._reload(
                    shell,
                    project_name=self.project_name,
                    settings=settings,
                    force_instance_folder=instance_folder,
                )

            def _get_dumpfile_name():
                if ttype == "base":
                    output = shell.odoo("list-deps", "base")["stdout"].split("---", 1)[
                        1
                    ]
                    deps = json.loads(output)
                    hash = deps["hash"]
                    return f"base_dump_{dumptype}_{self.repo_id.short}_{hash}"

                elif ttype == "full":
                    return f"full_{dumptype}_{self.repo_id.short}_{commit}"

                raise NotImplementedError(ttype)

            dump_name = _get_dumpfile_name()
            dest_path = path / dump_name
            return dest_path

    def _create_testrun(self):
        testrun = self.test_run_ids.create(
            {
                "commit_id": self.latest_commit_id.id,
                "branch_id": self.id,
            }
        )
        self.apply_test_settings(testrun)