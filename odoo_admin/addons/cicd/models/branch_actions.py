# TODO turn into dev an?
import time
import shutil
import traceback
from pathlib import Path
import threading
import os
import arrow
import base64
from odoo import _, api, models
from odoo.exceptions import UserError, RedirectWarning, ValidationError

class Branch(models.Model):
    _inherit = 'cicd.git.branch'

    def _reload_and_restart(self, shell, task, logsio, **kwargs):
        self._reload(shell, task, logsio)
        self._checkout_latest(shell, self.machine_id, logsio)
        shell.X(['odoo', '--project-name', self.name, 'build'])
        shell.X(['odoo', '--project-name', self.name, 'up', '-d'])
        self._after_build(shell, logsio)

    def _restore_dump(self, shell, task, logsio, **kwargs):
        self._reload(shell, task, logsio)
        task.dump_used = self.dump_id.name
        shell.X(['odoo', '--project-name', self.name, 'reload'])
        shell.X(['odoo', '--project-name', self.name, 'build'])
        shell.X(['odoo', '--project-name', self.name, 'down'])
        shell.X([
            'odoo', '--project-name', self.name,
            '-f', 'restore', 'odoo-db',
            self.dump_id.name
        ])
    
    def _docker_start(self, shell, task, logsio, **kwargs):
        shell.X(['odoo', '--project-name', self.name, 'up', '-d'])

    def _docker_stop(self, shell, task, logsio, **kwargs):
        shell.X(['odoo', '--project-name', self.name, 'kill'])

    def _docker_get_state(self, shell, task, logsio, **kwargs):
        import pudb;pudb.set_trace()
        info = shell.X(['odoo', '--project-name', self.name, 'ps', 'kill']).output
            
    def _turn_into_dev(self, task, logsio, **kwargs):
        with self._shellexec(logsio=logsio) as shell:
            shell.X(['odoo', '--project-name', 'turn-into-dev'])

    def _reload(self, shell, task, logsio, **kwargs):
        raw_settings = (task.machine_id.reload_config or '') + "\n" + (self.reload_config or '')
        odoo_settings = base64.encodestring((raw_settings).encode('utf-8').strip()).decode('utf-8')
        self._make_instance_docker_configs(shell) 
        shell.X([
            'odoo', '--project-name', self.name,
            'reload', '--additional_config', odoo_settings
            ])

    def _build(self, shell, task, logsio, **kwargs):
        self._reload(shell, task, logsio, **kwargs)
        shell.X(['odoo', '--project-name', self.name, 'build'])

    def _dump(self, shell, task, logsio, **kwargs):
        shell.X([
            'odoo', '--project-name', self.name, 
            'backup', 'odoo-db', self.name + ".dump.gz"
            ])

    def _update_git_commits(self, shell, logsio, force_instance_folder=None, **kwargs):
        self.ensure_one()
        instance_folder = force_instance_folder or self._get_instance_folder(self.machine_id)
        with shell.shell() as shell:
            commits = shell.check_output([
                "/usr/bin/git",
                "log",
                "--pretty=format:%H,%ct",
                "--since='last 4 months'",
            ], cwd=instance_folder)

            all_commits = self.env['cicd.git.commit'].search([])
            all_commits = dict((x.name, x.branch_ids) for x in all_commits)

            for line in commits.split("\n"):
                if not line:
                    continue
                date = arrow.get(int(line.split(",")[-1]))
                sha = line.split(",")[0]
                if sha in all_commits:
                    if self not in all_commits[sha]:
                        self.env['cicd.git.commit'].search([('name', '=', sha)]).branch_ids = [[4, self.id]]
                    continue

                logsio.info(f"Found new commit: {sha}")

                info = shell.check_output([
                    "/usr/bin/git",
                    "log",
                    sha,
                    "--date=format:%Y-%m-%d %H:%M:%S",
                    "-n1",
                ], cwd=instance_folder, update_env={
                    "TZ": "UTC0"
                }).split("\n")

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
    
    def _remove_web_assets(self, shell, tasks, logsio, **kwargs):
        shell.X([
            'odoo', '--project-name', self.name,
            'remove-web-assets'
            ])

    def _clear_db(self, shell, tasks, logsio, **kwargs):
        shell.X([
            'odoo', '--project-name', self.name,
            'cleardb'
            ])

    def _run_robot_tests(self, shell, tasks, logsio, **kwargs):
        shell.X([
            'odoo', '--project-name', self.name,
            'robot', '-a',
        ])

    def _run_unit_tests(self, shell, tasks, logsio, **kwargs):
        shell.X([
            'odoo', '--project-name', self.name,
            'run-tests',
        ])

    def _transform_input_dump():
        dump = Path(request.args['dump'])
        erase = request.args['erase'] == '1'
        anonymize = request.args['anonymize'] == '1'
        site = 'master'
        logger = LogsIOWriter("input_dump", f"{site}_{arrow.get().strftime('%Y-%m-%d_%H%M%S')}")

        def do():
            instance_folder = Path("/cicd_workspace") / f"{PREFIX_PREPARE_DUMP}{Path(tempfile.mktemp()).name}"
            try:
                # reverse lookup the path
                real_path = _get_host_path(Path("/input_dumps") / dump.parent) / dump.name

                def of(*args):
                    _odoo_framework(
                        instance_folder.name,
                        list(args),
                        log_writer=logger,
                        instance_folder=instance_folder
                        )

                logger.info(f"Preparing Input Dump: {dump.name}")
                logger.info("Preparing instance folder")
                source = str(Path("/cicd_workspace") / "master") + "/"
                dest = str(instance_folder) + "/"
                branch = 'master'
                logger.info(f"checking out {branch} to {dest}")

                repo = _get_main_repo(destination_folder=dest)
                repo.git.checkout('master', force=True)
                repo.git.pull()

                custom_settings = """
    RUN_POSTGRES=1
    DB_PORT=5432
    DB_HOST=postgres
    DB_USER=odoo
    DB_PWD=odoo
                """
                of("reload", '--additional_config', base64.encodestring(custom_settings.encode('utf-8')).strip().decode('utf-8'))
                of("down", "-v")

                # to avoid orphan messages, that return error codes although warning
                logger.info(f"Starting local postgres")
                of("up", "-d", 'postgres')

                of("restore", "odoo-db", str(real_path))
                suffix =''
                if erase:
                    of("cleardb")
                    suffix += '.cleared'
                if anonymize:
                    of("anonymize")
                    suffix += '.anonym'
                of("backup", "odoo-db", str(Path(os.environ['DUMPS_PATH']) / (dump.name + suffix + '.cicd_ready')))
                of("down", "-v")
            except Exception as ex:
                msg = traceback.format_exc()
                logger.info(msg)
            finally:
                if instance_folder.exists(): 
                    shutil.rmtree(instance_folder)

        t = threading.Thread(target=do)
        t.start()

        
    def _after_build(self, shell, logsio, **kwargs):
        cmd = ['odoo', '--project-name', self.name]
        shell.X(cmd + ["remove-settings", '--settings', 'web.base.url,web.base.url.freeze'])
        shell.X(cmd + ["update-setting", 'web.base.url', shell.machine.external_url])
        shell.X(cmd + ["set-ribbon", self.name])
        shell.X(cmd + ["prolong"])

    def _build_since_last_gitsha(self, shell, logsio, **kwargs):
        # todo make button
        self._after_build(shell=shell, logsio=logsio, **kwargs)

    def _reset(self, task, shell, **kwargs):
        shell.X(
            ['odoo', '--project-name', self.name, 'db', 'reset', '--do-not-install-base'],
        )

    def _checkout_latest(self, shell, machine, logsio, **kwargs):
        instance_folder = self._get_instance_folder(machine)
        with machine._shellexec(
            logsio=logsio,
            cwd=instance_folder,
            env={
                "GIT_TERMINAL_PROMPT": "0",
            }

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

    def debug_instance(self):
        site_name = request.args.get('name')
        logger = LogsIOWriter(site_name, 'misc')

        _odoo_framework(site_name, ['kill', 'odoo'], logs_writer=logger)
        _odoo_framework(site_name, ['kill', 'odoo_debug'], logs_writer=logger)

        shell_url = _get_shell_url([
            "cd", f"/{os.environ['WEBSSH_CICD_WORKSPACE']}/{site_name}", ";",
            "/usr/bin/python3",  "/opt/odoo/odoo", "-f", "--project-name", site_name, "debug", "odoo", "--command", "/odoolib/debug.py",
        ])
        # TODO make safe; no harm on system, probably with ssh authorized_keys

        return redirect(shell_url)
    
    def show_pgcli(self):
        site_name = request.args.get('name')

        shell_url = _get_shell_url([
            "cd", f"/{os.environ['WEBSSH_CICD_WORKSPACE']}/{site_name}", ";",
            "/usr/bin/python3",  "/opt/odoo/odoo", "-f", "--project-name", site_name, "pgcli",
            "--host", os.environ['DB_HOST'],
            "--user", os.environ['DB_USER'],
            "--password", os.environ['DB_PASSWORD'],
            "--port", os.environ['DB_PORT'],
        ])
        return redirect(shell_url)

    def shell_instance(self):
        # kill existing container and start odoo with debug command
        def _get_shell_url(command):
            pwd = base64.encodestring('odoo'.encode('utf-8')).decode('utf-8')
            shellurl = f"/console/?encoding=utf-8&term=xterm-256color&hostname=127.0.0.1&username=root&password={pwd}&command="
            shellurl += ' '.join(command)
            return shellurl

        containers = docker.containers.list(all=True, filters={'name': [name]})
        containers = [x for x in containers if x.name == name]
        shell_url = _get_shell_url([
            "cd", f"/{os.environ['WEBSSH_CICD_WORKSPACE']}/{site_name}", ";",
            "/usr/bin/python3",  "/opt/odoo/odoo", "-f", "--project-name", site_name, "debug", "odoo_debug", "--command", "/odoolib/shell.py",
        ])
        # TODO make safe; no harm on system, probably with ssh authorized_keys

        return {
            'type': 'ir.actions.act_url',
            'url': 'shell_url',
            'target': 'self'
        }


    def clear_instance(self):
        instance_folder = self._get_instance_folder(self.machine_id)
        _delete_sourcecode(name)
        _delete_dockercontainers(name)

        conn = _get_db_conn()
        try:
            cr = conn.cursor()
            _drop_db(cr, name)
        finally:
            cr.close()
            conn.close()
            
        db.sites.update_one(
            {'_id': site['_id']},
            {"$set": {'archive': True}}
            )
        db.updates.remove({'name': name})

        return jsonify({
            'result': 'ok',
        })

    @api.model
    def inactivity_cycle_down(shell):
        while True:
            sites = db.sites.find({'name': 1, 'last_access': 1})
            for site in sites:
                try:
                    logger = LogsIOWriter(site['name'], 'misc')
                    logger.debug(f"Checking site to cycle down: {site['name']}")
                    if (arrow.get() - arrow.get(site.get('last_access', '1980-04-04') or '1980-04-04')).total_seconds() > 2 * 3600: # TODO configurable
                        if _get_docker_state(site['name']) == 'running':
                            logger.debug(f"Cycling down instance due to inactivity: {site['name']}")
                            _odoo_framework(site['name'], 'kill', logs_writer=logger)

                except Exception as ex:
                    msg = traceback.format_exc()
                    logger.error(msg)
            time.sleep(10)

    def _make_instance_docker_configs(self, shell):
        with shell.shell() as ssh_shell:
            home_dir = shell._get_home_dir()
            ssh_shell.write_text(home_dir + f"/.odoo/docker-compose.{self.name}.yml", """
services:
    proxy:
        networks:
            - cicd_network
networks:
    cicd_network:
        external:
            name: {}
        """.format(os.environ["CICD_NETWORK_NAME"]))

            ssh_shell.write_text(home_dir + f'/.odoo/settings.{self.name}', """
DEVMODE=1
PROJECT_NAME={}
RUN_PROXY_PUBLISHED=0
RUN_CRONJOBS=0
RUN_CUPS=0
RUN_POSTGRES=0

DB_HOST={}
DB_USER={}
DB_PWD={}
DB_PORT={}
    """.format(
            self.name,
            shell.machine.db_host,
            shell.machine.db_user,
            shell.machine.db_pwd,
            shell.machine.db_port,
        ))
