import tempfile
import arrow
import random
import base64
import uuid
from contextlib import contextmanager
from sarge import Capture, run
from odoo.exceptions import UserError
import time
from copy import deepcopy
from pathlib import Path
from ..tools.logsio_writer import LogsIOWriter
import logging
from .shell_executor_base import BaseShellExecutor

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 6 * 3600
DEFAULT_ENV = {
    "BUILDKIT_PROGRESS": "plain",
}


class ShellExecutor(BaseShellExecutor):
    """
    ShellExecutor

    Execute remote ssh commands using environments, multi line commands
    """

    def __init__(
        self,
        ssh_keyfile,
        host,
        cwd,
        logsio,
        project_name=None,
        env=None,
        user=None,
        machine=None,
    ):
        env2 = deepcopy(DEFAULT_ENV)
        env2.update(env or {})

        super().__init__(ssh_keyfile, host, cwd, env=env2, user=user)

        self.project_name = project_name
        self.machine = machine
        if project_name:
            assert isinstance(project_name, str)

        if not logsio:
            logsio = LogsIOWriter(project_name or "general", "general")
        if logsio:
            assert isinstance(logsio, LogsIOWriter)
        self.logsio = logsio

    def get_logger(self):
        return self.logsio

    @contextmanager
    def clone(self, cwd=None, env=None, user=None, project_name=None):
        env2 = deepcopy(self.env)
        env2.update(env or {})
        user = user or self.user
        cwd = cwd or self.cwd
        project_name = project_name or self.project_name
        shell2 = ShellExecutor(
            self.ssh_keyfile,
            self.host,
            cwd,
            self.logsio,
            project_name,
            env2,
            user=user,
            machine=self.machine,
        )
        yield shell2

    @property
    def cwd(self):
        return self._cwd

    def exists(self, path, glob=False):
        if not glob:
            res = self._internal_execute(["test", "-e", path], logoutput=False)
            return res["exit_code"] == 0
        path = path.replace(" ", "\ ")
        res = self._internal_execute(f"ls {path}", logoutput=False)
        return res['stdout'].strip().splitlines()

    def rm(self, path):
        return self.remove(path)

    def remove(self, path):
        if self.exists(path):
            if self.logsio:
                self.logsio.info(f"Path {path} exists and is erased now.")
            path = Path(path)
            tmp = path.parent / (path.name + f".tmp.{uuid.uuid4()}")
            self._internal_execute(["mv", path, tmp])
            self._internal_execute(["rm", "-Rf", tmp])

            if self.exists(tmp):
                self._internal_execute(["sudo", "rm", "-Rf", tmp])

            if self.exists(path):
                raise UserError(f"Removing of {tmp} failed.")

    def _get_home_dir(self):
        if not self.machine.homedir:
            res = self._internal_execute(
                ["echo", "$HOME"], cwd="/", env=self.env, logoutput=False, timeout=10
            )["stdout"].strip()
            if res.endswith("/~"):
                res = res[:-2]
            self.machine.homedir = res
        return self.machine.homedir

    def odoo(self, *cmd, allow_error=False, force=False, timeout=None, logoutput=True):
        env = {
            "NO_PROXY": "*",
            "DOCKER_CLIENT_TIMEOUT": "600",
            "COMPOSE_HTTP_TIMEOUT": "600",
            "PSYCOPG_TIMEOUT": "120",
            "PYTHONBREAKPOINT": "",
        }
        if not self.project_name:
            raise Exception("Requires project_name for odoo execution")

        odoocmd = self.machine.odoocmd or 'odoo'
        cmd = [odoocmd, "--project-name", self.project_name] + list(cmd)
        if force:
            cmd.insert(1, "-f")
        res = self.X(
            cmd, allow_error=allow_error, env=env, timeout=timeout, logoutput=logoutput
        )
        if res["exit_code"] and not allow_error or res["exit_code"] is None:
            if (
                ".FileNotFoundError: [Errno 2] No such file or directory:"
                in res["stderr"]
            ):
                raise Exception(("Seems that a reload of the instance is required."))
            else:
                raise Exception("\n".join(filter(bool, [res["stdout"], res["stderr"]])))
        return res

    def checkout_branch(self, branch, cwd=None, nosubmodule_update=False):
        cwd = cwd or self.cwd
        with self.clone(cwd=cwd) as self:
            remote_branch = f"origin/{branch}"
            if not self.branch_exists(branch):
                self.logsio and self.logsio.info(
                    f"Tracking remote branch and checking out {branch}"
                )
                self.X(
                    [
                        "git-cicd",
                        "checkout",
                        "-b",
                        branch,
                        "--track",
                        remote_branch,
                    ],
                    allow_error=True,
                )

            self.logsio and self.logsio.info(f"Checking out {branch} regularly")
            self.X(
                ["git-cicd", "checkout", "-f", "--no-guess", branch], allow_error=False
            )
            # ensure branch is tracked with upstream - master isn't by default tracked
            if self.branch_exists(remote_branch, remote=True):
                breakpoint()
                self.X(["git-cicd", "branch", f"--set-upstream-to={remote_branch}"])
            self.logsio and self.logsio.info(f"Checked out {branch}")
            self._after_checkout(nosubmodule_update=nosubmodule_update)

    def checkout_commit(self, commit, cwd=None):
        cwd = cwd or self.cwd
        with self.clone(cwd=cwd) as self:
            # otherwise checking out a commit brings error message
            self.X(["git-cicd", "config", "advice.detachedHead", "false"])
            self.X(["git-cicd", "clean", "-xdff", commit])
            self.X(["git-cicd", "checkout", "-f", commit])
            sha = self.X(["git-cicd", "log", "-n1", "--format=%H"])["stdout"].strip()
            if sha != commit:
                raise Exception(("Somehow checking out " f"{commit} in {cwd} failed"))
            self._after_checkout()

    def branch_exists(self, branch, cwd=None, remote=False):
        git_cmd = ["git-cicd", "branch", "--no-color"]
        if remote:
            git_cmd += ["-r"]
        res = self.X(git_cmd, cwd=cwd)["stdout"].strip().splitlines()

        def reformat(x):
            x = x.replace("* ", "")
            x = x.strip()
            return x

        res = [reformat(x) for x in res]
        return branch in res

    def current_branch_contains_commit(self, commit, cwd=None):
        assert isinstance(commit, str)
        try:
            self.X(
                [
                    "git-cicd",
                    "branch",
                    "--contains",
                    commit,
                ],
                cwd=cwd,
            )

            return True

        except Exception:  # # pylint: disable=broad-except
            return False

    def _after_checkout(self, nosubmodule_update=False):
        self.logsio and self.logsio.info("Cleaning git...")
        self.X(["git-cicd", "clean", "-xdff"])
        self.logsio and self.logsio.info("Updating submodules...")
        if not nosubmodule_update:
            self.X(
                ["git-cicd", "submodule", "update", "--init", "--force", "--recursive"]
            )
        self.logsio and self.logsio.info("_after_checkout finished.")

    def X(
        self,
        cmd,
        allow_error=False,
        env=None,
        cwd=None,
        logoutput=True,
        timeout=None,
        retry=None,
    ):
        retry = retry or 0
        effective_env = deepcopy(self.env)
        if env:
            effective_env.update(env)
        for i in range((retry or 0) + 1):
            res = self._internal_execute(
                cmd, cwd=cwd, env=env, logoutput=logoutput, timeout=timeout
            )
            if res["exit_code"]:
                if i == retry:
                    break
            else:
                break
            time.sleep(10)

        if not allow_error:
            if res["exit_code"] is None:
                raise Exception("Timeout happend: {cmd}")
            if res["exit_code"]:
                raise Exception(
                    f"Error happened: {res['exit_code']}:\n"
                    f"{res['stderr']}\n"
                    f"{res['stdout']}"
                )

        return res

    def get(self, source):
        # not tested yet
        filename = Path(tempfile.mktemp(suffix="."))

        cmd, host = self._get_ssh_client("scp", split_host=True)
        capt = Capture()
        p = run(cmd + f" '{host}:{source}' '{filename}'", stdout=capt, stderr=capt)
        if p.commands[0].returncode:
            raise Exception("Copy failed")
        try:
            return filename.read_bytes()
        finally:
            if filename.exists():
                filename.unlink()

    def put(self, content, dest):
        filename = Path(tempfile.mktemp(suffix="."))
        if isinstance(content, str):
            content = content.encode("utf-8")
        filename.write_bytes(content)
        try:
            cmd, host = self._get_ssh_client("scp", split_host=True)
            capt = Capture()
            p = run(cmd + f" '{filename}' '{host}:{dest}'", stdout=capt, stderr=capt)
            if p.commands[0].returncode:
                err = capt.readlines()
                raise Exception(f"Transfer failed to {host}:{dest}\n{err}")
        finally:
            filename.unlink()

    def sql_excel(self, sql):
        filename = tempfile.mktemp(suffix=".")
        try:
            self.odoo(
                "excel",
                base64.encodestring(sql.encode("utf-8")).decode("utf-8").strip(),
                "--base64",
                "-f",
                filename,
            )
        except Exception as ex:
            if "psycopg2.errors.UndefinedTable" in str(ex):
                return None
            raise
        try:
            result = self.get(filename)
        finally:
            self.remove(filename)
        return result

    def extract_zip(self, content, dest_path):
        assert dest_path not in ["/", "/var/"]
        assert len(Path(dest_path).parts) > 2

        with self.machine._temppath(
            usage="srcfile", maxage=dict(hours=1)
        ) as remote_temp_path:
            self.X(["mkdir", "-p", remote_temp_path])
            filename = remote_temp_path / "src.tar.gz"

            self.put(content, filename)
            with self.machine._temppath(
                usage="srcfile", maxage=dict(hours=1)
            ) as temppath:
                self.X(["mkdir", "-p", temppath])
                self.X(["tar", "xfz", filename], cwd=temppath)
                self.X(
                    [
                        "rsync",
                        str(temppath) + "/",
                        str(dest_path) + "/",
                        "-ar",
                        "--delete-after",
                    ]
                )

    def get_zipped(self, path, excludes=None):
        excludes = excludes or []
        with self.machine._temppath(usage="get_zipped") as filename:
            zip_cmd = ["tar", "cfz", filename, "-C", path, "."]
            for exclude in excludes:
                zip_cmd.insert(-1, f'--exclude="{exclude}"')
            with self.clone(cwd=path) as self2:
                counter = 0
                while True:
                    try:
                        self2.X(zip_cmd)
                    except Exception:
                        # one time got message files changed during zipping; so retrying
                        # although it was in an own temp folder
                        counter += 1
                        if counter > 5:
                            raise
                        time.sleep(2)
                    else:
                        break
            content = self.get(filename)
            return content

    def get_snapshots(self):
        snaps = self.odoo("snap", "list")["stdout"].splitlines()[2:]
        for snap in snaps:
            yield snap.split(" ")[0]

    def docker_compose_exists(self):
        path = "~/.odoo/run/{self.project_name}/docker-compose.yml"
        return self.exists(path)

    def wait_for_postgres(self, timeout=1800):
        self.odoo("docker", "wait-for-container-postgres")

    def git_is_dirty(self):
        return bool(self.X(["git-cicd", "status", "-s"])["stdout"].strip())

    def git_safe_directory(self, path):
        deadline = arrow.get().shift(seconds=60)
        last_ex = None
        done = False
        while not done and arrow.get() < deadline:
            try:
                self.X(
                    [
                        "git-cicd",
                        "config",
                        "--global",
                        "--replace-all",
                        "safe.directory",
                        "*",
                    ]
                )
            except Exception as ex:
                last_ex = ex
                time.sleep(random.randint(3, 10))
            else:
                done = True
        if not done:
            raise Exception(f"Could not set safe directory.\n\n{last_ex}")

    def safe_move_directory(self, src, dest):
        src = Path(src)
        dest = Path(dest)

        # if on different directories, move to intermediate just before
        # hoping to be on same disk
        tmpdest = dest.parent / (dest.name + f".mv.{uuid.uuid4()}")

        # keep directory ID for running processes; probably files vanished in between,
        # then catch that error
        if self.exists(dest):
            self.remove(dest)

        try:
            self.X(["mv", src, tmpdest], allow_error=True)
            if self.exists(src):
                self.X(["rsync", str(src) + "/", str(tmpdest) + "/", "-ar"])
                self.remove(src)

            self.X(["mv", tmpdest, dest], allow_error=True)
        finally:
            self.rm(tmpdest)

    def current_branch(self):
        current_branch = self.X(["git-cicd", "branch", "--show-current"])[
            "stdout"
        ].strip()
        return current_branch

    def file_size(self, path):
        size = self.X(["stat", "-c", "%s", path])["stdout"].strip()
        size = int(size or 0)
        return size

    def get_date_modified(self, path):
        seconds = int(self.X(["stat", "-c", "%Y", path])["stdout"].strip())
        return arrow.get(seconds)

    def get_age_hours(self, path):
        return (arrow.get() - self.get_date_modified(path)).total_seconds() / 3600