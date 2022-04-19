import re
import uuid
import psycopg2
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
current_dir = Path(os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe()))))

logger = logging.getLogger(__name__)


class Branch(models.Model):
    _inherit = 'cicd.git.branch'

    def _prepare_a_new_instance(self, shell, task, logsio, **kwargs):
        dump = self.dump_id or self.repo_id.default_simulate_install_id_dump_id
        if not dump:
            self._reset_db(shell, task, logsio, **kwargs)
        else:
            self.backup_machine_id = dump.machine_id
            self.dump_id = dump
        if self.dump_id:
            self._restore_dump(shell, task, logsio, **kwargs)
        else:
            self._reset_db(shell, task, logsio, **kwargs)
        self._update_all_modules(shell, task, logsio, **kwargs)

    def _update_odoo(self, shell, task, logsio, **kwargs):
        if self.block_updates_until and \
                self.block_updates_until > fields.Datetime.now():
            raise RetryableJobError(
                "Branch is blocked - have to wait",
                seconds=10, ignore_retry=True
                )

        tasks = self.task_ids.with_context(prefetch_fields=False).filtered(
            lambda x: x.state == 'done' and x.name in [
                '_update_all_modules', '_update_odoo'])
        commit = None
        if tasks:
            commit = tasks[0].commit_id.name
        if commit:
            try:
                logsio.info("Updating")
                result = shell.odoo(
                    "update", "--since-git-sha", commit,
                    "--no-dangling-check", "--i18n")

                if result['exit_code']:
                    raise Exception("Error at update")
            except Exception as ex:
                logger.error(ex)
                logsio.error(ex)
                logsio.info((
                    "Running full update now - "
                    f"update since sha {commit} did not succeed"))
                self._update_all_modules(shell=shell, task=task, logsio=logsio, **kwargs)
        else:
            self._update_all_modules(
                shell=shell, task=task, logsio=logsio, **kwargs)

    def _update_all_modules(self, shell, task, logsio, **kwargs):
        logsio.info("Reloading")
        self._reload(shell, task, logsio)
        logsio.info("Building")
        self._internal_build(shell)
        logsio.info("Updating")
        shell.odoo('update', "--no-dangling-check") # , "--i18n")
        logsio.info("Upping")
        shell.odoo("up", "-d")

    def _update_installed_modules(self, shell, task, logsio, **kwargs):
        logsio.info("Reloading")
        self._reload(shell, task, logsio)
        logsio.info("Building")
        self._internal_build(shell)
        logsio.info("Updating")
        shell.odoo('update', "--no-dangling-check", "--installed-modules")
        logsio.info("Upping")
        shell.odoo("up", "-d")

    def _simple_docker_up(self, shell, task, logsio, **kwargs):
        shell.odoo("up", "-d")

    def _reload_and_restart(self, shell, task, logsio, **kwargs):
        self._reload(shell, task, logsio)
        logsio.info("Building")
        self._internal_build(shell)
        logsio.info("Upping")
        shell.odoo("kill")
        self._kill_tmux_sessions(shell)
        shell.odoo("rm")
        shell.odoo("up", "-d")
        self._after_build(shell, logsio)

    def _restore_dump(self, shell, task, logsio, dump, **kwargs):
        dump = dump or self.dump_id
        if isinstance(dump, int):
            dump = self.env['cicd.dump'].browse(dump)

        if not dump:
            raise ValidationError(_("Dump missing - cannot restore"))
        self._reload(shell, task, logsio)
        task.sudo().write({'dump_used': self.dump_id.name})
        logsio.info("Reloading")
        shell.odoo('reload')
        logsio.info("Building")
        self._internal_build(shell)
        logsio.info("Downing")
        shell.odoo('kill')
        shell.odoo('rm')
        logsio.info(f"Restoring {dump.name}")
        shell.odoo(
            '-f', 'restore', 'odoo-db', '--no-remove-webassets',
            dump.name)
        if self.remove_web_assets_after_restore:
            shell.odoo('-f', 'remove-web-assets')
        shell.odoo("update")
        self.last_restore_dump_name = dump.name
        self.last_restore_dump_date = dump.date_modified

    def _docker_start(self, shell, task, logsio, **kwargs):
        shell.odoo('up', '-d')
        self.machine_id._fetch_psaux_docker_containers()

    def _docker_stop(self, shell, task, logsio, **kwargs):
        shell.odoo('kill')
        self.machine_id._fetch_psaux_docker_containers()

    def _docker_remove(self, shell, task, logsio, **kwargs):
        shell.odoo('kill')
        shell.odoo('rm')
        self.machine_id._fetch_psaux_docker_containers()

    def _turn_into_dev(self, shell, task, logsio, **kwargs):
        shell.odoo('turn-into-dev')

    def _reload(
            self, shell, task, logsio,
            project_name=None, settings=None, commit=None, registry=None,
            **kwargs
            ):

        cwd = self._make_sure_source_exists(shell, logsio)

        with shell.clone(cwd=cwd) as shell:
            self._make_instance_docker_configs(
                shell, forced_project_name=project_name, settings=settings,
                registry=registry
            )
            self._collect_all_files_by_their_checksum(shell)
            if commit:
                shell.checkout_commit(commit)
            shell.odoo('reload')
            if self._is_hub_configured(shell):
                shell.odoo("login")

    def _is_hub_configured(self, shell):
        output = shell.odoo("config", "--full", logoutput=False)['stdout']
        lines = [x for x in output.split("\n") if 'HUB_URL=' in x]
        if lines:
            if len(lines[0]) > len("HUB_URL="):
                return True
        return False

    def _build(self, shell, task, logsio, **kwargs):
        self._reload(shell, task, logsio, **kwargs)
        self._internal_build(shell)

    def _dump(self, shell, task, logsio, volume=None, filename=None, **kwargs):
        volume = volume or task.machine_id._get_volume('dumps')
        if isinstance(volume, int):
            volume = self.env['cicd.machine.volume'].browse(volume)
            volume = Path(volume.name)

        logsio.info(f"Dumping to {task.machine_id.name}:{volume}")
        filename = filename or task.branch_id.backup_filename or (
            self.project_name + ".dump.gz")
        assert isinstance(filename, str)

        if '/' in filename:
            raise ValidationError("Filename mustn't contain slashses!")
        shell.odoo('backup', 'odoo-db', str(volume / filename))
        # to avoid serialize access erros which may occur
        task.machine_id.with_delay().update_dumps()

    def _update_git_commits(
            self, shell, logsio,
            force_instance_folder=None, **kwargs
            ):

        self.ensure_one()
        logsio.info(f"Updating commits for {self.project_name}")
        instance_folder = force_instance_folder or self._get_instance_folder(
            self.machine_id)

        def _extract_commits():
            # removing the 4 months filter:
            # old branches get stuck and stuck other branches because
            # latest commit # cannot be found, if that filter is active.
            return list(filter(bool, shell.X([
                "git",
                "log",
                "--pretty=format:%H___%ct",
                "-n", str(self.repo_id.analyze_last_n_commits),
                # "--since='last 4 months'",
            ], logoutput=False, cwd=instance_folder)[
                'stdout'].strip().split("\n")))

        commits = _extract_commits()
        commits = [list(x.split("___")) for x in commits]
        for commit in commits:
            commit[1] = arrow.get(int(commit[1]))

        all_commits = self.env['cicd.git.commit'].with_context(
            active_test=False).search([])
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

            env = {
                "TZ": "UTC0"
            }
            if date is None:
                line = shell.X([
                    "git",
                    "log",
                    sha,
                    "-n1",
                    "--pretty=format:%ct",
                ], logoutput=False, cwd=instance_folder, env=env)[
                    'stdout'].strip().split(',')

                if not line or not any(line):
                    continue
                date = arrow.get(int(line[0]))

            logsio.info((
                f"Getting detail information of sha "
                f"{sha} ({icommit} / {len(commits)})"))

            info = shell.X([
                "git",
                "log",
                sha,
                "--date=format:%Y-%m-%d %H:%M:%S",
                "-n1",
            ], logoutput=False, cwd=instance_folder, env=env)[
                'stdout'].strip().split("\n")

            def _get_item(name):
                for line in info:
                    if line.strip().startswith(f"{name}:"):
                        return line.split(":", 1)[-1].strip()

            def _get_body():
                for i, line in enumerate(info):
                    if not line:
                        return info[i + 1:]

            text = ('\n'.join(_get_body())).strip()
            self.commit_ids = [[0, 0, {
                'name': sha,
                'author': _get_item("Author"),
                'date': date.strftime("%Y-%m-%d %H:%M:%S"),
                'text': text,
                'branch_ids': [[4, self.id]],
            }]]

    def _remove_web_assets(self, shell, task, logsio, **kwargs):
        logsio.info("Killing...")
        shell.odoo('kill')
        logsio.info("Calling remove-web-assets")
        shell.odoo('-f', 'remove-web-assets')
        logsio.info("Restarting...")
        shell.odoo('up', '-d')

    def _shrink_db(self, shell, task, logsio, **kwargs):
        shell.odoo('cleardb')

    def _anonymize(self, shell, task, logsio, **kwargs):
        shell.odoo('update', 'anonymize')
        shell.odoo('anonymize')

    def _run_tests(self):
        pass # deprecated

    def _cron_run_open_tests(self):
        for testrun in self.env['cicd.test.run'].search([
                ('state', '=', 'open')]):
            testrun.with_delay(channel="testruns", identity_key=(
                "start-open-testrun-"
                f"{self.name}-"
                f"{testrun.commit_id.name}-"
                f"{testrun.id}"
            )).execute()

    def _after_build(self, shell, logsio, **kwargs):
        shell.odoo(
            "remove-settings", '--settings', 'web.base.url,web.base.url.freeze'
            )

        with self._extra_env() as x_self:
            external_url = x_self.machine_id.external_url

        shell.odoo(
            "update-setting", 'web.base.url', external_url)
        shell.odoo("set-ribbon", self.name)
        shell.odoo("prolong")
        shell.odoo('restore-web-icons')

    def _build_since_last_gitsha(self, shell, logsio, **kwargs):
        # todo make button
        self._after_build(shell=shell, logsio=logsio, **kwargs)

    def _checkout_latest(
        self, shell, logsio=None, machine=None,
        instance_folder=None, **kwargs
    ):
        """
        Use this for getting source code. It updates also submodules.

        """
        my_name = self._unblocked('name')

        def _clone_instance_folder(machine, logsio, instance_folder):
            self.repo_id._get_main_repo(
                logsio=logsio,
                destination_folder=instance_folder,
                limit_branch=my_name,
                machine=machine,
            )

        machine = machine or shell.machine
        instance_folder = instance_folder or self._get_instance_folder(machine)
        logsio = logsio or shell.logsio
        logsio.write_text(f"Updating instance folder {my_name}")
        _clone_instance_folder(machine, logsio, instance_folder)
        logsio.write_text(f"Cloning {my_name} to {instance_folder}")

        with shell.clone(cwd=instance_folder) as shell:
            logsio.write_text(f"Checking out {my_name}")
            try:
                shell.X(["git", "checkout", "-f", my_name])
            except Exception as ex:
                logsio.error(ex)
                shell.rm(instance_folder)
                raise RetryableJobError(
                    "Cleared directory - branch not found - please retry",
                    ignore_retry=True) from ex

            try:
                shell.X(["git", "pull"])
            except Exception as ex:
                logsio.error((
                    "Error at pulling,"
                    f"cloning path {instance_folder} again:\n{ex}"))
                shell.rm(instance_folder)
                _clone_instance_folder(machine, logsio, instance_folder)

            # delete all other branches:
            res = shell.X(["git", "branch"])['stdout'].strip().split("\n")
            for branch in list(filter(lambda x: '* ' not in x, res)):
                branch = self.repo_id._clear_branch_name(branch)

                if branch == my_name:
                    continue

                shell.X(["git", "branch", "-D", branch])
                del branch

            current_branch = list(filter(lambda x: '* ' in x, shell.X([
                "git", "branch"])['stdout'].strip().split("\n")))
            if not current_branch:
                raise Exception(f"Somehow no current branch found")
            branch_in_dir = self.repo_id._clear_branch_name(current_branch[0])
            if branch_in_dir != my_name:
                shell.rm(instance_folder)

                raise Exception((
                    f"Branch could not be checked out!"
                    f"Was {branch_in_dir} - but should be {my_name}"
                ))

            logsio.write_text(f"Clean git")
            shell.X(["git", "clean", "-xdff"])

            logsio.write_text("Updating submodules")
            shell.X(["git", "submodule", "update", "--recursive", "--init"])

            logsio.write_text("Getting current commit")
            commit = shell.X(["git", "rev-parse", "HEAD"])['stdout'].strip()
            logsio.write_text(commit)

        return str(commit)

    def _cron_inactivity_cycle_down(self):
        branches = self.with_context(
            active_test=False,
            prefetch_fields=False
        ).search([])
        self.env.cr.commit()

        for rec in branches:
            with rec._extra_env() as x_rec:
                last_access = arrow.get(x_rec.last_access or '1980-04-04')
                uptime = (arrow.get() - last_access).total_seconds()
                if uptime <= x_rec.cycle_down_after_seconds:
                    continue

                if ":running" in x_rec.containers:
                    x_rec.with_delay(
                        identity_key=f"cycle_down:{x_rec.name}"
                    )._cycle_down_instance()
                    x_rec.env.cr.commit()

    def _cycle_down_instance(self):
        self.ensure_one()
        with self.machine_id._shell(
            cwd=self.project_path,
            project_name=self.project_name
        ) as shell:
            self.env.cr.commit()
            with self._extra_env() as x_rec:
                name = x_rec.name
            if shell.logsio:
                shell.logsio.info((
                    "Cycling down instance "
                    f"{name} due to inactivity"
                ))
            logger.info((
                "Shutting down instance due to inactivity "
                f"{name}"
                ))
            shell.odoo('kill', allow_error=True)
            shell.odoo('rm', allow_error=True)

    def _make_instance_docker_configs(
            self, shell, forced_project_name=None, settings=None,
            registry=None
        ):

        home_dir = shell._get_home_dir()
        machine = shell.machine
        project_name = forced_project_name or self._unblocked('project_name')
        content = ((
            current_dir.parent
            / 'data'
            / 'template_cicd_instance.yml.template')).read_text()

        values = os.environ.copy()
        values['PROJECT_NAME'] = project_name
        content = content.format(**values)
        shell.put(content, (
            f"{home_dir}"
            f"/.odoo/docker-compose.{project_name}.yml"
            ))

        content = ((
            current_dir.parent
            / 'data'
            / 'template_cicd_instance.settings')).read_text()
        assert machine

        with machine._extra_env() as x_machine:
            if not x_machine.postgres_server_id:
                raise ValidationError(
                    _(f"Please configure a db server for {x_machine.name}"))

        content += "\n" + (self._unblocked('reload_config') or '')
        if settings:
            content += "\n" + settings

        registry = registry or self.repo_id.registry_id
        if registry:
            content += (
                f"\nHUB_URL={registry.hub_url}"
            )

        with self._extra_env() as x_self:
            with machine._extra_env() as x_machine:
                shell.put(
                    content.format(
                        branch=x_self,
                        project_name=project_name,
                        machine=x_machine),
                    home_dir + f'/.odoo/settings.{project_name}'
                    )

    def _cron_autobackup(self):
        for rec in self.search([('autobackup', '=', True)]):
            rec._make_task(
                "_dump", machine=rec.backup_machine_id,
                ignore_previous_tasks=True)

    def _fetch_from_registry(self, shell):
        if self._is_hub_configured(shell):
            shell.odoo('docker-registry', 'login')
            shell.odoo('docker-registry', 'regpull')

    def _push_to_registry(self, shell):
        if self._is_hub_configured(shell):
            shell.odoo('docker-registry', 'login')
            shell.odoo('docker-registry', 'regpush')

    def _internal_build(self, shell):
        self._fetch_from_registry(shell)
        shell.odoo("build")
        self._push_to_registry(shell)

    def _reset_db(self, shell, task, logsio, **kwargs):
        self._reload(shell, task, logsio)
        self._internal_build(shell)
        shell.odoo('-f', 'db', 'reset')
        shell.odoo('update')
        # shell.odoo('turn-into-dev')
        self._after_build(shell=shell, logsio=logsio, **kwargs)

    def _compress(self, shell, task, logsio, compress_job_id):
        import pudb;pudb.set_trace()
        self.ensure_one()
        compressor = self.env['cicd.compressor'].sudo().browse(
            compress_job_id).sudo()

        # get list of files

        logsio.info("Identifying latest dump")
        with compressor.source_volume_id.machine_id._shell(
                logsio=logsio, cwd="") as source_shell:
            output = list(reversed(source_shell.X([
                "ls", "-trA", compressor.source_volume_id.name])[
                    'stdout'].strip().split("\n")))

            for line in output:
                if line == '.' or line == '..':
                    continue
                if re.findall(compressor.regex, line):
                    filename = line.strip()
                    break
            else:
                logsio.info("No files found.")
                return

        # if the machines are the same, then just rewrite destination path
        # if machines are different then copy locally and then put it on the
        # machine
        appendix = "_compressor"
        if compressor.anonymize:
            appendix += '_anonymize'
        self = self.with_context(testrun=appendix)
        dest_file_path = shell.machine._get_volume('dumps') / self.project_name

        # release db resources:
        self.env.cr.commit()

        with compressor.source_volume_id.machine_id._put_temporary_file_on_machine(
            logsio,
            compressor.source_volume_id.name + "/" + filename,
            shell.machine,
            dest_file_path,
        ) as effective_dest_file_path:
            compressor.last_input_size = int(shell.X([
                'stat', '-c', '%s', effective_dest_file_path])[
                    'stdout'].strip())

            assert shell.machine.ttype == 'dev'

            with self.repo_id._temp_repo(
                machine=shell.machine, branch=compressor.branch_id.name,
                pull=True,
            ) as instance_path:
                # change working project/directory
                with shell.clone(
                    cwd=instance_path,
                    project_name=self.project_name
                ) as shell:
                    breakpoint()

                    logsio.info("Reloading...")
                    settings = (
                        "\n"
                        f"DBNAME={self.database_project_name}\n"
                        "RUN_CRONJOBS=0\n"
                        "RUN_QUEUEJOBS=0\n"
                        "RUN_POSTGRES=1\n"
                        "RUN_ROBOT=0\n"
                        "RUN_PROXY_PUBLISHED=0\n"
                        "DB_HOST=postgres\n"
                        "DB_USER=odoo\n"
                        "DB_PWD=odoo\n"
                        "DB_PORT=5432\n"
                    )
                    self._reload(
                        shell, task, logsio,
                        project_name=self.project_name, settings=settings)
                    logsio.info(f"Restoring {effective_dest_file_path}...")
                    shell.odoo("up", "-d", "postgres")
                    breakpoint()
                    try:
                        shell.odoo(
                            "-f", "restore", "odoo-db",
                            effective_dest_file_path)
                        logsio.info("Clearing DB...")
                        shell.odoo('-f', 'cleardb')
                        if compressor.anonymize:
                            logsio.info("Anonymizing DB...")
                            shell.odoo('-f', 'anonymize')
                        logsio.info("Dumping compressed dump")
                        output_path = compressor.volume_id.name + "/" + \
                            compressor.output_filename
                        shell.odoo('backup', 'odoo-db', output_path)
                        compressor.last_output_size = int(shell.X([
                            'stat', '-c', '%s', output_path])[
                                'stdout'].strip())
                        compressor.date_last_success = fields.Datetime.now()
                    finally:
                        shell.odoo('down', '-v', force=True, allow_error=True)

    def _make_sure_source_exists(self, shell, logsio):
        instance_folder = self._get_instance_folder(shell.machine)
        self.ensure_one()
        with self._extra_env() as x_self:
            healthy = x_self.repo_id._is_healthy_repository(
                shell, instance_folder)

        if not healthy:
            try:
                self._checkout_latest(shell, logsio=logsio)
            except Exception:
                shell.rm(instance_folder)
                self._checkout_latest(shell, logsio=logsio)
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
        return # see comment

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
        shell.put(python_script_executed, '.cicd_reorder_files')
        try:
            shell.X(["python3", '.cicd_reorder_files'])
        finally:
            shell.rm(".cicd_reorder_files")

    def _kill_tmux_sessions(self, shell):
        for rec in self:
            machine = rec.machine_id
            with machine._shell() as shell:
                machine.make_login_possible_for_webssh_container()
                test = shell.X([
                    "sudo", "pkill", "-9", "-f",
                    "-u", shell.machine.ssh_user_cicdlogin,
                    f'new-session.*-s.*{rec.project_name}'
                    ], allow_error=True)

    def new_branch(self):
        self.ensure_one()
        if not self.env.user.has_group("cicd.group_make_branches"):
            raise UserError("Missing rights to create branch.")
        action = self.repo_id.new_branch()
        action['context'].update({
            'default_source_branch_id': self.id,
            'default_dump_id': self.dump_id.id,
    })
        return action
