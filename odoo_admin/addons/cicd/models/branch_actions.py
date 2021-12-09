from odoo import fields
from pathlib import Path
import os
import arrow
import base64
from odoo import _, api, models, fields
from odoo.exceptions import UserError, RedirectWarning, ValidationError
import inspect
import os
from pathlib import Path
from odoo.addons.queue_job.exception import RetryableJobError
import logging
current_dir = Path(os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe()))))

logger = logging.getLogger(__name__)

class Branch(models.Model):
    _inherit = 'cicd.git.branch'

    def _update_odoo(self, shell, task, logsio, **kwargs):
        if self.block_updates_until and self.block_updates_until > fields.Datetime.now():
            raise RetryableJobError("Branch is blocked - have to wait", seconds=60, ignore_retry=True)
        tasks = self.task_ids.filtered(lambda x: x.state == 'done' and x.name in ['_update_all_modules', '_update_odoo']).sorted(lambda x: x.id, reverse=True)
        commit = None
        if tasks:
            commit = tasks[0].commit_id.name
        if commit:
            try:
                logsio.info("Updating")
                shell.odoo("update", "--since-git-sha", commit)
            except Exception as ex:
                logger.error(ex)
                logsio.error(ex)
                logsio.info(f"Running full update now - update since sha {commit} did not succeed")
                self._update_all_modules(shell=shell, task=task, logsio=logsio, **kwargs)
        else:
            self._update_all_modules(shell=shell, task=task, logsio=logsio, **kwargs)

    def _update_all_modules(self, shell, task, logsio, **kwargs):
        logsio.info("Reloading")
        shell.odoo('reload')
        logsio.info("Building")
        shell.odoo('build')
        logsio.info("Updating")
        shell.odoo('update')
        logsio.info("Upping")
        shell.odoo("up", "-d")

    def _reload_and_restart(self, shell, task, logsio, **kwargs):
        self._reload(shell, task, logsio)
        self._checkout_latest(shell, self.machine_id, logsio)
        logsio.info("Building")
        shell.odoo('build')
        logsio.info("Upping")
        shell.odoo("up", "-d")
        self._after_build(shell, logsio)

    def _restore_dump(self, shell, task, logsio, **kwargs):
        self._reload(shell, task, logsio)
        task.sudo().write({'dump_used': self.dump_id.name})
        logsio.info("Reloading")
        shell.odoo('reload')
        logsio.info("Building")
        shell.odoo('build')
        logsio.info("Downing")
        shell.odoo('down', allow_error=False)
        logsio.info(f"Restoring {self.dump_id.name}")
        shell.odoo('-f', 'restore', 'odoo-db', self.dump_id.name)
    
    def _docker_start(self, shell, task, logsio, **kwargs):
        shell.odoo('up', '-d')
        self._docker_get_state(shell)

    def _docker_stop(self, shell, task, logsio, **kwargs):
        shell.odoo('kill')
        self._docker_get_state(shell)

    def _docker_get_state(self, shell, **kwargs):
        info = shell.odoo('ps').output

        passed = False
        updated_containers = set()
        for line in info.split("\n"):
            if line.startswith("------"):
                passed = True
                continue
            if not passed: continue
            while "  " in line:
                line = line.replace("  ", " ")

            if line.startswith("Version:"):
                continue
            container_name = line.split(" ")[0]
            state = line.split(" ", 1)[-1].lower()
            if 'exit' in state:
                state = 'down'
            elif 'up' in state:
                state = 'up'
            else:
                state = False
            
            container = self.container_ids.filtered(lambda x: x.name == container_name)
            if not container:
                self.container_ids = [[0, 0, {
                    'name': container_name,
                    'state': state,
                }]]
            else:
                container.state = state
            updated_containers.add(container_name)
        for container in self.container_ids:
            if container.name not in updated_containers:
                container.unlink()

    def _turn_into_dev(self, shell, task, logsio, **kwargs):
        shell.odoo('turn-into-dev')

    def _reload(self, shell, task, logsio, **kwargs):
        raw_settings = (task.machine_id.reload_config or '') + "\n" + (self.reload_config or '')
        self._make_instance_docker_configs(shell) 
        shell.odoo('reload')

    def _build(self, shell, task, logsio, **kwargs):
        self._reload(shell, task, logsio, **kwargs)
        shell.odoo('build')

    def _dump(self, shell, task, logsio, **kwargs):
        volume = task.machine_id._get_volume('dumps')
        logsio.info(f"Dumping to {task.machine_id.name}:{volume}")
        filename = task.branch_id.backup_filename or (self.project_name + ".dump.gz")
        if '/' in filename:
            raise ValidationError("Filename mustn't contain slashses!")
        shell.odoo('backup', 'odoo-db', str(volume / filename))
        task.machine_id.update_dumps()

    def _update_git_commits(self, shell, logsio, force_instance_folder=None, force_commits=None, **kwargs):
        self.ensure_one()
        self._checkout_latest(shell, self.machine_id, logsio)
        instance_folder = force_instance_folder or self._get_instance_folder(self.machine_id)
        with shell.shell() as shell:

            def _extract_commits():
                return list(filter(bool, shell.check_output([
                    "/usr/bin/git",
                    "log",
                    "--pretty=format:%H",
                    "--since='last 4 months'",
                ], cwd=instance_folder).strip().split("\n")))

            if force_commits:
                commits = force_commits
            else:
                commits = _extract_commits()

            all_commits = self.env['cicd.git.commit'].search([])
            all_commits = dict((x.name, x.branch_ids) for x in all_commits)

            for sha in commits:
                if sha in all_commits:
                    if self not in all_commits[sha]:
                        self.env['cicd.git.commit'].search([('name', '=', sha)]).branch_ids = [[4, self.id]]
                    continue

                env = update_env={
                    "TZ": "UTC0"
                }
                
                line = shell.check_output([
                    "/usr/bin/git",
                    "log",
                    sha,
                    "-n1",
                    "--pretty=format:%ct",
                ], cwd=instance_folder, update_env=env).strip().split(',')
                if not line or not any(line):
                    continue

                date = arrow.get(int(line[0]))

                info = shell.check_output([
                    "/usr/bin/git",
                    "log",
                    sha,
                    "--date=format:%Y-%m-%d %H:%M:%S",
                    "-n1",
                ], cwd=instance_folder, update_env=env).split("\n")

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

    def _clear_db(self, shell, task, logsio, **kwargs):
        shell.odoo('cleardb')

    def _anonymize(self, shell, task, logsio, **kwargs):
        shell.odoo('update', 'anonymize')
        shell.odoo('anonymize')

    def _create_empty_db(self, shell, task, logsio, **kwargs):
        logsio.info("Reloading")
        shell.odoo('reload')
        logsio.info("Building")
        shell.odoo('build')
        logsio.info("Downing")
        shell.odoo('down')
        shell.odoo('-f', 'db' 'reset')

    def _run_tests(self, shell, task, logsio, **kwargs):
        """
        If update_state is set, then the state is set to 'tested'
        """
        # try nicht unbedingt notwendig; bei __exit__ wird ein close aufgerufen
        b = task.branch_id

        update_state = kwargs.get('update_state', False)
        self._update_git_commits(shell, task=task, logsio=logsio)

        test_run = self.test_run_ids.create({
            'commit_id': self.commit_ids[0].id,
            'branch_id': b.id,
        })

        # use tempfolder for tests to not interfere with updates or so
        repo_path = task.branch_id.repo_id._get_main_repo(tempfolder=True, machine=shell.machine)
        shell.cwd = repo_path
        try:
            self.env.cr.commit()
            shell.X(["git", "checkout", "-f", test_run.commit_id.name])
            test_run.execute(shell, task, logsio)
            if update_state:
                if test_run.state == 'failed':
                    self.state = 'rework'
                else:
                    self.state = 'tested'
        finally:
            shell.rmifexists(repo_path)

    # def _transform_input_dump():
    #     dump = Path(request.args['dump'])
    #     erase = request.args['erase'] == '1'
    #     anonymize = request.args['anonymize'] == '1'
    #     site = 'master'
    #     logger = LogsIOWriter("input_dump", f"{site}_{arrow.get().strftime('%Y-%m-%d_%H%M%S')}")

    #     def do():
    #         instance_folder = Path("/cicd_workspace") / f"{PREFIX_PREPARE_DUMP}{Path(tempfile.mktemp()).name}"
    #         try:
    #             # reverse lookup the path
    #             real_path = _get_host_path(Path("/input_dumps") / dump.parent) / dump.name

    #             def of(*args):
    #                 _odoo_framework(
    #                     instance_folder.name,
    #                     list(args),
    #                     log_writer=logger,
    #                     instance_folder=instance_folder
    #                     )

    #             logger.info(f"Preparing Input Dump: {dump.name}")
    #             logger.info("Preparing instance folder")
    #             source = str(Path("/cicd_workspace") / "master") + "/"
    #             dest = str(instance_folder) + "/"
    #             branch = 'master'
    #             logger.info(f"checking out {branch} to {dest}")

    #             repo = _get_main_repo(destination_folder=dest)
    #             repo.git.checkout('master', force=True)
    #             repo.git.pull()

    #             custom_settings = """
    # RUN_POSTGRES=1
    # DB_PORT=5432
    # DB_HOST=postgres
    # DB_USER=odoo
    # DB_PWD=odoo
    #             """
    #             of("reload")
    #             of("down", "-v")

    #             # to avoid orphan messages, that return error codes although warning
    #             logger.info(f"Starting local postgres")
    #             of("up", "-d", 'postgres')

    #             of("restore", "odoo-db", str(real_path))
    #             suffix =''
    #             if erase:
    #                 of("cleardb")
    #                 suffix += '.cleared'
    #             if anonymize:
    #                 of("anonymize")
    #                 suffix += '.anonym'
    #             of("backup", "odoo-db", str(Path(os.environ['DUMPS_PATH']) / (dump.name + suffix + '.cicd_ready')))
    #             of("down", "-v")
    #         except Exception as ex:
    #             msg = traceback.format_exc()
    #             logger.info(msg)
    #         finally:
    #             if instance_folder.exists(): 
    #                 shutil.rmtree(instance_folder)

    #     t = threading.Thread(target=do)
    #     t.start()

        
    def _after_build(self, shell, logsio, **kwargs):
        shell.odoo("remove-settings", '--settings', 'web.base.url,web.base.url.freeze')
        shell.odoo("update-setting", 'web.base.url', shell.machine.external_url)
        shell.odoo("set-ribbon", self.name)
        shell.odoo("prolong")
        self._docker_get_state(shell=shell)

    def _build_since_last_gitsha(self, shell, logsio, **kwargs):
        # todo make button
        self._after_build(shell=shell, logsio=logsio, **kwargs)

    def _reset(self, task, shell, **kwargs):
        shell.odoo('db', 'reset', '--do-not-install-base')

    def _checkout_latest(self, shell, machine, logsio, **kwargs):
        instance_folder = self._get_instance_folder(machine)
        with machine._shell() as spurplus_shell:
            with self.repo_id._get_ssh_command(spurplus_shell) as env:
                env["GIT_TERMINAL_PROMPT"] = "0"
                with machine._shellexec(
                    logsio=logsio,
                    cwd=instance_folder,
                    env=env
                ) as shell_exec:
                    logsio.write_text(f"Updating instance folder {self.name}")

                    logsio.write_text(f"Cloning {self.name} to {instance_folder}")
                    self.repo_id.clone_repo(machine, instance_folder, logsio)

                    logsio.write_text(f"Checking out {self.name}")
                    shell_exec.X(["git", "checkout", "-f", self.name])

                    logsio.write_text(f"Pulling {self.name}")
                    shell_exec.X(["git", "pull"])

                    logsio.write_text(f"Clean git")
                    shell_exec.X(["git", "clean", "-xdff"])

                    logsio.write_text("Updating submodules")
                    shell_exec.X(["git", "submodule", "update", "--init", "--force", "--recursive"])

                    logsio.write_text("Getting current commit")
                    commit = shell_exec.X(["git", "rev-parse", "HEAD"]).output.strip()
                    logsio.write_text(commit)

                    return str(commit)

    def inactivity_cycle_down(self):
        self.ensure_one()

        logsio = self._get_new_logsio_instance("inactivity_cycle_down")
        dest_folder = self.machine_id._get_volume('source') / self.project_name
        try:
            with self.machine_id._shellexec(dest_folder, logsio, project_name=self.project_name) as shell:
                if (arrow.get() - arrow.get(self.last_access or '1980-04-04')).total_seconds() > self.cycle_down_after_seconds:
                    self._docker_get_state(shell=shell)
                    if self.docker_state == 'up':
                        logsio.info(f"Cycling down instance due to inactivity")
                        shell.odoo('kill')

        except Exception as ex:
            logsio.error(ex)

    def _make_instance_docker_configs(self, shell):
        with shell.shell() as ssh_shell:
            home_dir = shell._get_home_dir()
            project_name = self.project_name
            content = (current_dir.parent / 'data' / 'template_cicd_instance.yml.template').read_text()
            ssh_shell.write_text(home_dir + f"/.odoo/docker-compose.{project_name}.yml", content.format(**os.environ))

            content = (current_dir.parent / 'data' / 'template_cicd_instance.settings').read_text()
            content += "\n" + self.reload_config
            ssh_shell.write_text(home_dir + f'/.odoo/settings.{project_name}', content.format(branch=self, machine=self.machine_id))

    def _cron_autobackup(self):
        for rec in self:
            rec._make_task("_dump")
