import os
import tempfile
from copy import deepcopy
import pwd
import grp
import hashlib
from pathlib import Path
from ..tools.logsio_writer import LogsIOWriter
import spur
import spurplus
from contextlib import contextmanager
from odoo import _, api, fields, models, SUPERUSER_ID, tools
import subprocess
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from ..tools.tools import tempdir
from ..tools.tools import get_host_ip
import logging
logger = logging.getLogger(__name__)

class ShellExecutor(object):
    def __init__(self, machine, cwd, logsio, project_name=None, env={}):
        self.machine = machine
        self.cwd = cwd
        self.logsio = logsio
        self.env = env
        self.project_name = project_name
        if machine:
            assert machine._name == 'cicd.machine'
        if logsio:
            assert isinstance(logsio, LogsIOWriter)
        if project_name:
            assert isinstance(project_name, str)
        if env:
            assert isinstance(env, dict)

    def exists(self, path):
        with self.shell() as spurplus:
            return spurplus.exists(path)

    def rmifexists(self, path):
        with self.shell() as spurplus:
            path = str(path)
            if spurplus.exists(path):
                self.logsio.info(f"Path {path} exists and is erased now.")
                spurplus.run(["rm", "-Rf", path])
            else:
                self.logsio.info(f"Path {path} doesn't exist - nothing will be erased.")

    def _get_home_dir(self):
        res = self.machine._execute_shell(
            ['realpath', '~'],
        ).output.strip()
        if res.endswith("/~"):
            res = res[:-2]
        return res

    @contextmanager
    def shell(self):
        with self.machine._shell() as shell:
            yield shell

    def odoo(self, *cmd, allow_error=False):
        env={
            'NO_PROXY': "*",
            'DOCKER_CLIENT_TIMEOUT': "600",
            'COMPOSE_HTTP_TIMEOUT': "600",
            'PSYCOPG_TIMEOUT': "120",
        }
        if not self.project_name:
            raise Exception("Requires project_name for odoo execution")
        cmd = ["odoo", "--project-name", self.project_name] + list(cmd)
        res = self.X(cmd, allow_error=allow_error, env=env)
        if res.return_code and not allow_error:
            if '.FileNotFoundError: [Errno 2] No such file or directory:' in res.stderr_output:
                raise Exception("Seems that a reload of the instance is required.")
            else:
                raise Exception(res.stderr_output)
        return res

    def checkout_branch(self, branch, cwd=None):
        if not self.branch_exists(branch):
            self.X(["git", "checkout", "-b", branch, "--track", "origin/" + branch], cwd=cwd, allow_error=True)
        self.X(["git", "checkout", "-f", "--no-guess", branch], cwd=cwd, allow_error=True)
        self._after_checkout(cwd=cwd)

    def checkout_commit(self, commit, cwd=None):
        self.X(["git", "checkout", "-f", commit], cwd=cwd, allow_error=True)
        self._after_checkout(cwd=cwd)

    def branch_exists(self, branch, cwd=None):
        res = self.X(["git", "show-ref", "--verify", "refs/heads/" + branch], cwd=cwd, allow_error=True)
        if not res.return_code and branch in res.output.strip():
            return True
        return False

    def _after_checkout(self, cwd):
        self.X(["git", "clean", "-xdff"], cwd=cwd)
        self.X(["git", "submodule", "update", "--init", "--force", "--recursive"], cwd=cwd)

    def X(self, cmd, allow_error=False, env=None, cwd=None):
        effective_env = deepcopy(self.env)
        if env:
            effective_env.update(env)
        return self.machine._execute_shell(
            cmd, cwd=cwd or self.cwd, env=effective_env, logsio=self.logsio,
            allow_error=allow_error,
        )

    def get(self, source):
        filename = Path(tempfile.mktemp(suffix='.'))
        
        with self.machine._shell() as shell:
            try:
                shell.get(source, filename)
                return filename.read_bytes()
            finally:
                if filename.exists():
                    filename.unlink()

    def put(self, content, dest):
        filename = Path(tempfile.mktemp(suffix='.'))
        filename.write_bytes(content)
        try:
            with self.machine._shell() as shell:
                shell.put(filename, dest)
        finally:
            filename.unlink()