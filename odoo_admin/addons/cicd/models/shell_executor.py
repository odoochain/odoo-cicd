import arrow
import threading
from pssh.clients import ParallelSSHClient
from pssh.exceptions import Timeout
import shlex
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
    def __init__(self, ssh_keyfile, machine, cwd, logsio, project_name=None, env={}):
        self.machine = machine
        self.cwd = Path(cwd)
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

        self.client = ParallelSSHClient(
            [machine.effective_host], user=machine.ssh_user, pkey=str(ssh_keyfile),
        )
    def exists(self, path):
        with self.shell() as spurplus:
            return spurplus.exists(path)

    def rmifexists(self, path):
        with self.shell() as spurplus:
            path = str(path)
            if spurplus.exists(path):
                if self.logsio:
                    self.logsio.info(f"Path {path} exists and is erased now.")
                spurplus.run(["rm", "-Rf", path])
            else:
                if self.logsio:
                    self.logsio.info(f"Path {path} doesn't exist - nothing will be erased.")

    def _get_home_dir(self):
        res = self.machine._execute_shell(
            ['realpath', '~'],
        ).output.strip()
        if res.endswith("/~"):
            res = res[:-2]
        return res

    def odoo(self, *cmd, allow_error=False, force=False):
        env={
            'NO_PROXY': "*",
            'DOCKER_CLIENT_TIMEOUT': "600",
            'COMPOSE_HTTP_TIMEOUT': "600",
            'PSYCOPG_TIMEOUT': "120",
        }
        if not self.project_name:
            raise Exception("Requires project_name for odoo execution")
        cmd = ["odoo", "--project-name", self.project_name] + list(cmd)
        if force:
            cmd.insert(1, "-f")
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

    def X(self, cmd, allow_error=False, env=None, cwd=None, logoutput=True):
        effective_env = deepcopy(self.env)
        if env:
            effective_env.update(env)
        return self._internal_execute(cmd, cwd=cwd, env=env, logoutput=logoutput, allow_error=allow_error)

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
    
    def _internal_execute(self, cmd, cwd=None, env=None, logoutput=True, allow_error=False, timeout=600):

        def convert(x):
            if isinstance(x, Path):
                x = str(x)
            return x

        cmd = list(map(convert, cmd))

        class MyWriter(object):
            def __init__(self, ttype, logsio):
                self.text = [""]
                self.ttype = ttype
                self.line = ""
                self.logsio = logsio

            def finish(self):
                self._write_line()

            def write(self, text):
                if not self.logsio:
                    return
                if '\n' in text and len(text) == 1:
                    self._write_line()
                    self.line = ""
                else:
                    self.line += text
                    return

            def _write_line(self):
                if not self.line:
                    return
                if logoutput:
                    if self.ttype == 'error':
                        self.logsio.error(self.line)
                    else:
                        self.logsio.info(self.line)

        if not logoutput:
            stdwriter, errwriter = None, None
        else:
            stdwriter, errwriter = MyWriter('info', self.logsio), MyWriter('error', self.logsio)

        if cwd:
            cmd = ["cd", cwd, ";"] + cmd

        for k, v in env.items():
            cmd = [f"{k}=\"{v}\"", ";"] + cmd

        deadline = arrow.get().shift(seconds=timeout)
        output = self.client.run_command(cmd, use_pty=True, stop_on_errors=not allow_error)
        import pudb;pudb.set_trace()

        def reader(stream, writer):
            if not writer:
                return
            while not self.client.finished(output):
                for line in stream:
                    writer.write(line)

        threads = [
            threading.Thread(target=reader, args=(output.stdout, stdwriter)),
            threading.Thread(target=reader, args=(output.stderr, errwriter)),
        ]
        [t.start() for t in threads]

        timeout = False
        while True:
            if self.client.finished(output):
                break
            if arrow.get() > deadline:
                timeout = True
                output.client.close_channel(output.channel)

        self.client.join(output)
        exit_code = output.exit_code if not timeout else -1
        stdwriter and stdwriter.finish()
        errwriter and errwriter.finish()
        return {
            'timeout': timeout,
            'exit_code': exit_code,
            'output': stdwriter and stdwriter.line,
            'erroutput': errwriter and errwriter.line,
        }
