import traceback
from odoo import _, api, fields, models, SUPERUSER_ID
import tempfile
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from contextlib import contextmanager
from ..tools.logsio_writer import LogsIOWriter


class CicdReleaseAction(models.Model):
    _name = "cicd.release.action"

    release_id = fields.Many2one("cicd.release", string="Release", required=True)
    machine_id = fields.Many2one("cicd.machine", string="Machine")
    shell_script_before_update = fields.Text("Shell Script Before Update")
    shell_script_at_end = fields.Text("Shell Script At End (finally)")
    shell_script_on_update_fail = fields.Text("Shell Script at Update Fail")
    settings = fields.Text("Settings")
    effective_settings = fields.Text(compute="_compute_effective_settings")

    def _compute_effective_settings(self):
        for rec in self:
            hub_url = rec.release_id.repo_id.registry_id.hub_url_readonly
            use_registry = "1" if hub_url else "0"
            default_settings = []
            if hub_url:
                default_settings.append("HUB_URL=" + hub_url)
            default_settings.append("REGISTRY=" + use_registry)

            default_settings = "\n".join(default_settings)
            rec.effective_settings = (
                f"{default_settings}"
                "\n"
                f"{rec.release_id.common_settings or ''}"
                "\n"
                f"{rec.settings or ''}"
                "\n"
                f"PROJECT_NAME={rec.release_id.project_name}"
                "\n"
            )

    def _exec_shellscripts(self, logsio, pos):
        for rec in self:
            script = (
                rec.shell_script_before_update
                if pos == "before"
                else rec.shell_script_at_end
            )

            if not script:
                return

            rec._exec_shellscript(logsio, script)

    def _exec_shellscript(self, logsio, script):
        self.ensure_one()
        filepath = tempfile.mktemp(suffix=".")
        with self._contact_machine(logsio) as shell:
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

        with LogsIOWriter.GET(branch_name, "release") as logsio:
            try:
                if actions:
                    actions._exec_shellscripts(logsio, "before")

                    actions._upload_settings_file(logsio, release_item, commit_sha)
                    actions._load_images_to_registry(logsio, release_item, commit_sha)
                    actions._update_sourcecode(logsio, release_item, commit_sha)
                    actions._update_images(logsio)
                    actions._stop_odoo(logsio)
                    try:
                        actions[0]._run_update(logsio)
                    except Exception:
                        if not actions[0].shell_script_on_update_fail:
                            raise
                        else:
                            actions[0]._exec_shellscript(
                                logsio, actions[0].shell_script_on_update_fail
                            )
                            actions[0]._run_update(logsio)

            except Exception:  # pylint: disable=broad-except
                errors.append(traceback.format_exc())

            finally:
                for action in actions:
                    try:
                        action._start_odoo(logsio=logsio)
                    except Exception:  # pylint: disable=broad-except
                        errors.append(traceback.format_exc())

                for action in actions:
                    try:
                        action._exec_shellscripts(logsio, "after")
                    except Exception:  # pylint: disable=broad-except
                        errors.append(traceback.format_exc())
            return errors

    @contextmanager
    def _contact_machine(self, logsio):
        self.ensure_one()
        project_name = self.release_id._unblocked("project_name")
        path = self.machine_id._get_volume("source")

        # make sure directory exists
        with self.machine_id._shell(
            cwd=path, logsio=logsio, project_name=project_name
        ) as shell:
            yield shell

    def _stop_odoo(self, logsio):
        for self in self:
            with self._contact_machine(logsio) as shell:
                if not shell:
                    return
                shell.odoo("kill", allow_error=True)

    def _update_sourcecode(self, logsio, release_item, merge_commit_id):
        logsio.info("Updating source code")
        with self._extra_env() as x_self:
            repo = x_self[0].release_id.repo_id
            zip_content = repo._get_zipped(
                logsio, merge_commit_id, with_git=x_self.release_id.deploy_git
            )

        for rec in self:
            with rec._contact_machine(logsio) as shell:
                path = shell.cwd
                home_dir = shell._get_home_dir()
                breakpoint()
                # dest path not required to exist
                with shell.clone(cwd=home_dir) as shell2:
                    shell2.extract_zip(zip_content, path)

    def _update_images(self, logsio):
        breakpoint()
        logsio.info("Updating ~/.odoo/images")
        with self._extra_env() as x_self:
            machine = x_self.release_id.repo_id.machine_id
            with machine._shell() as shell:
                home_dir = shell._get_home_dir()
                images_path = f"{home_dir}/.odoo/images"

                images = shell.get_zipped(images_path)
                del images_path

            with x_self._contact_machine(logsio) as shell:
                home_dir = shell._get_home_dir()
                images_path = f"{home_dir}/.odoo/images"

                shell.extract_zip(images, images_path)
                # disable any remotes on images so not pulled
                with shell.clone(cwd=images_path) as gitshell:
                    for remote in (
                        gitshell.X(["git-cicd", "remote"])["stdout"]
                        .strip()
                        .splitlines()
                    ):
                        gitshell.X(["git-cicd", "remote", "remove", remote])

    def _upload_settings_file(self, logsio, release_item, commit_sha):
        breakpoint()
        logsio.info("Uploading settings file")
        with self._extra_env() as x_self:
            for rec in x_self:
                if not rec.settings and not rec.release_id.common_settings:
                    continue
                with rec._contact_machine(logsio) as shell:
                    settings = rec.effective_settings
                    settings = f"DOCKER_IMAGE_TAG={commit_sha}\n" f"{settings}"
                    shell.put(
                        settings, f"~/.odoo/settings.{rec.release_id.project_name}"
                    )

    def _remove_setting(self, settings, key):
        def _do():
            for line in settings.splitlines():
                if line.startswith(f"{key}="):
                    continue
                yield line

        return "\n".join(_do())

    def _get_virginal_settings(self):
        self.ensure_one()
        settings = self.effective_settings
        settings = self._remove_setting(settings, "OWNER_UID")
        settings = self._remove_setting(settings, "RESTART_CONTAINERS")
        settings = self._remove_setting(settings, "DUMPS_PATH")
        settings = self._remove_setting(settings, "HUB_URL")
        settings = self._remove_setting(settings, "PROJECT_NAME")
        return settings

    def _load_images_to_registry(self, logsio, release_item, commit_sha):
        """
        Builds with given configuration and uploads to registry
        """
        with self._extra_env() as x_self:
            logsio.info("Preparing updating docker registry")
            for rec in x_self:
                release = rec.release_id
                if not release.repo_id.registry_id:
                    continue
                machine = release.repo_id.machine_id
                branch = release.branch_id.name
                logsio.info(f"Cloning {branch}...")
                with rec.release_id.repo_id._temp_repo(
                    machine, branch=branch
                ) as repo_path:
                    project_name = f"build_{branch}"
                    logsio.info(f"Cloning {branch} done.")
                    with machine._shell(
                        cwd=repo_path, project_name=project_name
                    ) as shell:
                        homedir = shell._get_home_dir()
                        settings_file = f"{homedir}/.odoo/settings.{project_name}"
                        # registry=0 so that build tags are kept
                        # and use not readonly registry access
                        writable_hub = rec.release_id.repo_id.registry_id.hub_url
                        settings = self._get_virginal_settings()
                        shell.put(
                            (
                                f"{settings or ''}\n"
                                "REGISTRY=0\n"
                                f"HUB_URL={writable_hub}\n"
                                f"PROJECT_NAME={project_name}"
                            ),
                            settings_file,
                        )
                        shell.X(["git-cicd", "checkout", commit_sha])
                        shell.odoo("-xs", settings_file, "reload")
                        logsio.info("Pulling images for only-images services")
                        shell.odoo("docker", "pull")
                        logsio.info("Building Docker Images")
                        shell.odoo("build")
                        shell.odoo("docker-registry", "login")
                        logsio.info("Uploading to registry")
                        shell.odoo("docker-registry", "regpush")
                        logsio.info("Uploading done.")

    @api.constrains("settings")
    def _strip_settings(self):
        for rec in self:
            settings = (rec.settings or "").strip()
            if settings != (rec.settings or ""):
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
                cmd += ["--i18n"]
            cmd += "--non-interactive"
            shell.odoo(*cmd)

    def _start_odoo(self, logsio):
        breakpoint()
        for self in self:
            with self._contact_machine(logsio) as shell:
                if shell.exists(shell.cwd):
                    shell.odoo("up", "-d")
