import arrow
import re
import threading
from pssh.clients import SSHClient
from pssh.exceptions import Timeout, ConnectionError
import shlex
import os
import tempfile
from copy import deepcopy
import pwd
import grp
import hashlib
from pathlib import Path
from ..tools.logsio_writer import LogsIOWriter
from contextlib import contextmanager
from odoo import _, api, fields, models, SUPERUSER_ID, tools
import subprocess
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from ..tools.tools import tempdir
from ..tools.tools import get_host_ip
import gevent
import gevent.lock
import logging
logger = logging.getLogger(__name__)

class ShellExecutor(object):
    def __init__(self, ssh_keyfile, machine, cwd, logsio, project_name=None, env={}):
        self.machine = machine
        self.cwd = Path(cwd) if cwd else None
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
        self.ssh_keyfile = ssh_keyfile

    def exists(self, path):
        try:
            res = self._internal_execute(["stat", path])
        except ConnectionError:
            raise
        except Exception as ex:
            return False
        else:
            return res['exit_code'] == 0

    def remove(self, path):
        if self.exists(path):
            if self.logsio:
                self.logsio.info(f"Path {path} exists and is erased now.")
            self._internal_execute(["rm", "-Rf", path])

    def rmifexists(self, path):
        if self.exists(path):
            self.remove(path)
        else:
            if self.logsio:
                self.logsio.info(f"Path {path} doesn't exist - nothing will be erased.")

    def _get_home_dir(self):
        with self.machine._shell() as shell:
            res = shell.X(
                ['realpath', '~'],
            )['stdout'].strip()
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
        if res['exit_code'] and not allow_error or res['exit_code'] is None:
            if '.FileNotFoundError: [Errno 2] No such file or directory:' in res['stderr']:
                raise Exception("Seems that a reload of the instance is required.")
            else:
                raise Exception(res['stdout'])
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
        if res['exit_code'] is not None and not res['exit_code'] and branch in res['stdout'].strip():
            return True
        return False

    def _after_checkout(self, cwd):
        self.X(["git", "clean", "-xdff"], cwd=cwd)
        self.X(["git", "submodule", "update", "--init", "--force", "--recursive"], cwd=cwd)

    def X(self, cmd, allow_error=False, env=None, cwd=None, logoutput=True, ignore_stdout=False):
        effective_env = deepcopy(self.env)
        if env:
            effective_env.update(env)
        res = self._internal_execute(
            cmd, cwd=cwd, env=env,
            logoutput=logoutput, allow_error=allow_error, ignore_stdout=ignore_stdout)
        if not allow_error:
            if res['exit_code'] is None:
                import pudb;pudb.set_trace()
                raise Exception("Timeout happend: {cmd}")
            if res['exit_code']:
                raise Exception(f"Error happened: {res['exit_code']}: {res['stdout']}")
        return res

    def get(self, source):
        client = self._get_ssh_client()
        filename = Path(tempfile.mktemp(suffix='.'))
        
        client.scp_recv(source, filename)
        try:
            return filename.read_bytes()
        finally:
            if filename.exists():
                filename.unlink()

    def put(self, content, dest):
        client = self._get_ssh_client()
        filename = Path(tempfile.mktemp(suffix='.'))
        if isinstance(content, str):
            content = content.encode('utf-8')
        filename.write_bytes(content)
        try:
            client.scp_send(str(filename), dest)
        finally:
            filename.unlink()

    def _get_ssh_client(self):
        client = SSHClient(
            self.machine.effective_host,
            user=self.machine.ssh_user,
            pkey=str(self.ssh_keyfile),
        )
        return client
    
    def _internal_execute(self, cmd, cwd=None, env=None, logoutput=True, allow_error=False, timeout=600, ignore_stdout=False):

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
                self.all_lines = []

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
                line = self.line
                self.all_lines.append(line)
                if logoutput:
                    if self.ttype == 'error':
                        self.logsio.error(line)
                    else:
                        self.logsio.info(line)

        if not logoutput:
            stdwriter, errwriter = None, None
        else:
            stdwriter, errwriter = MyWriter('info', self.logsio), MyWriter('error', self.logsio)

        cwd = cwd or self.cwd
        cd_command = []
        if cwd:
            cd_command = ["cd", str(cwd)]

        effective_env = {
        }
        if self.env: effective_env.update(self.env)
        if env: effective_env.update(env)

        client = self._get_ssh_client()

        wait = gevent.lock.BoundedSemaphore(1)

        def evntlet_add_msg(src, pf, wait):
            for msg in src:
                with wait:
                    if stdwriter:
                        stdwriter.write(msg)
                gevent.sleep(.1)


        # ohne use_pty das failed/haengt close_channel
        # leider kommt dann allels über stdout.
        # stderr bleibt leer.
        cmd = shlex.join(cmd)
        for k, v in effective_env.items():
            cmd = f"{k}=\"{v}\"" + " " + cmd

        if cd_command:
            cmd = shlex.join(cd_command) + " && " + cmd
        if ignore_stdout:
            cmd += " 1> /dev/null"
        else:
            cmd += " | cat - "
        host_out = client.run_command(cmd, use_pty=True)

        rstdout = gevent.spawn(evntlet_add_msg, host_out.stdout, "stdout: ", wait)
        rstderr = gevent.spawn(evntlet_add_msg, host_out.stderr, "stderr: ", wait)
        timeout_happened = False
        try:
            client.wait_finished(host_out, timeout)
            gevent.killall([rstdout, rstderr])
        except Timeout:
            logger.warn(f"Timeout occurred")
            timeout_happened = True
            gevent.killall([rstdout, rstderr])
            host_out.client.close_channel(host_out.channel)

        exit_code = host_out.exit_code

        rest_stdout = list(host_out.stdout)
        if stdwriter:
            for line in rest_stdout:
                stdwriter.write(line + "\n")

        stdwriter and stdwriter.finish()
        errwriter and errwriter.finish()
        stdout = '\n'.join(stdwriter.all_lines) if logoutput else ""
        stderr = ""

        return {
            'timeout': timeout_happened,
            'exit_code': exit_code,
            'stdout': stdout,
            'stderr': stderr,
        }