from odoo import _, api, fields, models, SUPERUSER_ID
import tempfile
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from contextlib import contextmanager
from ..tools.logsio_writer import LogsIOWriter


class CicdReleaseAction(models.Model):
    _name = 'cicd.release.action'

    release_id = fields.Many2one(
        'cicd.release', string="Release", required=True)
    machine_id = fields.Many2one('cicd.machine', string="Machine")
    shell_script_before_update = fields.Text("Shell Script Before Update")
    shell_script_at_end = fields.Text("Shell Script At End (finally)")
    settings = fields.Text("Settings")
    effective_settings = fields.Text(compute="_compute_effective_settings")

    def _compute_effective_settings(self):
        for rec in self:
            hub_url = rec.release_id.repo_id.registry_id.hub_url
            use_registry = '1' if hub_url else '0'
            if hub_url:
                default_settings.append("HUB_URL=" + hub_url)
            default_settings.append("REGISTRY=" + use_registry)

            default_settings = '\n'.join(default_settings)
            rec.settings = (
                f"{default_settings}"
                "\n"
                f"{rec.settings}"
            )

    def _exec_shellscripts(self, logsio, pos):
        for rec in self:
            script = rec.shell_script_before_update \
                if pos == 'before' else rec.shell_script_at_end

            if not script:
                return

            filepath = tempfile.mktemp(suffix='.')

            with rec._contact_machine(logsio) as shell:
                shell.put(script, filepath)
                try:
                    shell.X(["/bin/bash", filepath])
                finally:
                    shell.rm(filepath)

    def run_action_set(self, release_item, commit_sha):
        actions = self
        errors = []

        with release_item._extra_env() as unblocked_item:
            branch_name = unblocked_item.release_id.branch_id.name

        with LogsIOWriter.GET(branch_name, 'release') as logsio:
            try:
                actions._exec_shellscripts(logsio, "before")
                actions._stop_odoo(logsio)

                breakpoint()
                actions._load_images_to_registry(logsio, release_item)
                actions._update_sourcecode(
                    logsio, release_item, commit_sha)
                actions._update_images(logsio)
                actions._upload_settings_file(logsio)

                actions[0]._run_update(logsio)

            except Exception as ex:
                errors.append(ex)

            finally:
                for action in actions:
                    try:
                        action._start_odoo(logsio=logsio)
                    except Exception as ex:
                        errors.append(ex)

                for action in actions:
                    try:
                        action._exec_shellscripts(logsio, "after")
                    except Exception as ex:
                        errors.append(ex)
            return errors

    @contextmanager
    def _contact_machine(self, logsio):
        self.ensure_one()
        project_name = self.release_id._unblocked('project_name')
        path = self.machine_id._get_volume("source")

        # make sure directory exists
        with self.machine_id._shell(
            cwd=path, logsio=logsio,
            project_name=project_name
        ) as shell:
            if not shell.exists(path):
                shell.X(["mkdir", "-p", path])
            yield shell

    def _stop_odoo(self, logsio):
        for self in self:
            with self._contact_machine(logsio) as shell:
                if not shell:
                    return
                shell.odoo("kill")

    def _update_sourcecode(self, logsio, release_item, merge_commit_id):
        logsio.info("Updating source code")
        with self._extra_env() as x_self:
            repo = x_self[0].release_id.repo_id
            zip_content = repo._get_zipped(
                logsio,
                merge_commit_id,
                with_git=x_self.release_id.deploy_git
            )

        for rec in self:
            with rec._contact_machine(logsio) as shell:
                shell.extract_zip(zip_content, shell.cwd)

    def _update_images(self, logsio):
        logsio.info("Updating ~/.odoo/images")
        images_path = "~/.odoo/images"
        with self._extra_env() as x_self:
            with x_self.machine_id._shell() as shell:
                images = shell.get_zipped(images_path)

            with x_self._contact_machine(logsio) as shell:
                shell.extract_zip(images, images_path)
                # disable any remotes on images so not pulled
                with shell.clone(cwd=images_path) as gitshell:
                    for remote in gitshell.X([
                            "git", "remote"])['stdout'].strip().splitlines():
                        gitshell.X(["git", "remote", "remove", remote])

    def _upload_settings_file(self, logsio):
        logsio.info("Uploading settings file")
        with self._extra_env() as x_self:
            for rec in x_self:
                if not rec.settings:
                    continue
                with rec._contact_machine(logsio) as shell:
                    shell.put(rec.effective_settings, "~/.odoo/settings")

    def _load_images_to_registry(self, logsio, release_item):
        """
        Builds with given configuration and uploads to registry
        """
        with self._extra_env() as x_self:
            for rec in x_self:
                release = rec.release_id
                if not release.repo_id.registry_id:
                    continue
                machine = release.repo_id.machine_id
                branch = release.branch_id.name
                with rec.release_id.repo_id._temp_repo(
                    machine, branch=branch) as repo_path:
                    project_name = f"build_{branch}"
                    with machine._shell(
                            cwd=repo_path, project_name=project_name) as shell:
                        settings_file = f"~/.odoo/settings.{project_name}"
                        shell.put(rec.effective_settings, settings_file)
                        shell.X(["git", "checkout", release_item.commit_id.name])
                        shell.odoo("reload")
                        shell.odoo("build")
                        shell.odoo("docker-registry", "login")
                        shell.odoo("docker-registry", "regpush")



    @api.constrains("settings")
    def _strip_settings(self):
        for rec in self:
            settings = (rec.settings or '').strip()
            if settings != (rec.settings or ''):
                rec.settings = settings

    def _run_update(self, logsio):
        self.ensure_one()
        with self._contact_machine(logsio) as shell:
            shell.odoo("reload")
            if self.release_id.repo_id.registry_id:
                shell.odoo("regpull")
            else:
                shell.odoo("build")
            cmd = ["update"]
            if self.release_id.update_i18n:
                cmd += ['--i18n']
            shell.odoo(*cmd)

    def _start_odoo(self, logsio):
        for self in self:
            with self._contact_machine(logsio) as shell:
                shell.odoo("up", "-d")
