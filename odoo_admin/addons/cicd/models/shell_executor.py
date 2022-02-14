import arrow
import uuid
import threading
from sarge import Capture, run
import time
import shlex
import tempfile
from copy import deepcopy
from pathlib import Path
from ..tools.logsio_writer import LogsIOWriter
import logging
logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 6 * 3600
DEFAULT_ENV = {
    'BUILDKIT_PROGRESS': 'plain',
}


class ShellExecutor(object):
    class TimeoutConnection(Exception): pass
    class TimeoutFinished(Exception): pass
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
        res = self._internal_execute(["stat", path])
        return res['exit_code'] == 0

    def rm(self, path):
        return self.remove(path)

    def remove(self, path):
        if self.exists(path):
            if self.logsio:
                self.logsio.info(f"Path {path} exists and is erased now.")
            self._internal_execute(["rm", "-Rf", path])
        else:
            if self.logsio:
                self.logsio.info(f"Path {path} did not exist - not erased")

    def _get_home_dir(self):
        with self.machine._shell() as shell:
            res = shell.X(
                ['echo', '$HOME'],
            )['stdout'].strip()
        if res.endswith("/~"):
            res = res[:-2]
        return res

    def odoo(self, *cmd, allow_error=False, force=False, timeout=None):
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
        res = self.X(cmd, allow_error=allow_error, env=env, timeout=timeout)
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

    def X(self, cmd, allow_error=False, env=None, cwd=None, logoutput=True, timeout=None):
        effective_env = deepcopy(self.env)
        if env:
            effective_env.update(env)
        res = self._internal_execute(
            cmd, cwd=cwd, env=env,
            logoutput=logoutput, allow_error=allow_error, timeout=timeout)
        if not allow_error:
            if res['exit_code'] is None:
                raise Exception("Timeout happend: {cmd}")
            if res['exit_code']:
                raise Exception(f"Error happened: {res['exit_code']}: {res['stdout']}")
        return res

    def get(self, source):
        # not tested yet
        filename = Path(tempfile.mktemp(suffix='.'))

        cmd, host = self._get_ssh_client('scp', split_host=True)
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
        filename = Path(tempfile.mktemp(suffix='.'))
        if isinstance(content, str):
            content = content.encode('utf-8')
        filename.write_bytes(content)
        try:
            cmd, host = self._get_ssh_client('scp', split_host=True)
            capt = Capture()
            p = run(cmd + f" '{filename}' '{host}:{dest}'", stdout=capt, stderr=capt)
            if p.commands[0].returncode:
                raise Exception("Transfer failed")
        finally:
            filename.unlink()

    def _get_ssh_client(self, cmd='ssh', split_host=False):
        host = self.machine.effective_host
        user = self.machine.ssh_user
        base = f"{cmd} -T -oStrictHostKeyChecking=no -i {self.ssh_keyfile}"
        user_host = f"{user}@{host}"
        if split_host:
            return base, user_host
        return base + " " + user_host + " "

    def _internal_execute(self, cmd, cwd=None, env=None, logoutput=True, allow_error=False, timeout=9999):
        if timeout is None:
            timeout = DEFAULT_TIMEOUT

        def convert(x):
            if isinstance(x, Path):
                x = str(x)
            return x

        cmd = list(map(convert, cmd))
        class MyWriter(object):
            def __init__(self, ttype, logsio, logoutput):
                self.text = [""]
                self.ttype = ttype
                self.line = ""
                self.logsio = logsio
                self.all_lines = []
                self.logoutput = logoutput

            def write(self, line):
                if line is None:
                    return
                line = line.decode("utf-8")
                if line.endswith("\n"):
                    line = line[:-1]
                self.all_lines += [line]
                if logoutput and self.logsio:
                    if self.ttype == 'error':
                        self.logsio.error(line)
                    else:
                        self.logsio.info(line)

        stdwriter, errwriter = MyWriter('info', self.logsio, logoutput), MyWriter('error', self.logsio, logoutput)


        if isinstance(cmd, (tuple, list)):
            cmd = f"{cmd[0]} " + " ".join(map(lambda x: f'"{x}"', cmd[1:]))


        sshcmd = self._get_ssh_client()
        stop_marker = str(uuid.uuid4()) + str(uuid.uuid4())
        start_marker = str(uuid.uuid4()) + str(uuid.uuid4())

        stdout = Capture(buffer_size=-1) # line buffering
        stderr = Capture(buffer_size=-1) # line buffering
        data = {
            'stop': False,
            'started': False,
            'stop_marker': False,
        }

        def on_started():
            data['started'] = True

        def on_stop_marker():
            data['stop_marker'] = True
            data['stop_marker_arrived'] = arrow.get()

        def collect(capture, writer, marker=None, on_marker=None, stop_marker=None, on_stop_marker=None):
            while not data['stop']:
                for line in capture:
                    line_decoded = line.decode('utf-8')
                    is_marker = False
                    if marker and marker in line_decoded and on_started:
                        on_started()
                        is_marker = True
                    if stop_marker and stop_marker in line_decoded and on_stop_marker:
                        on_stop_marker()
                        is_marker = True
                    
                    if not is_marker:
                        writer.write(line)

        tstd = threading.Thread(target=collect, args=(stdout, stdwriter, start_marker, on_started, stop_marker, on_stop_marker))
        terr = threading.Thread(target=collect, args=(stderr, errwriter))
        tstd.daemon = True
        terr.daemon = True
        [x.start() for x in [tstd, terr]]

        cmd = cmd.replace('\n', ' ')
        bashcmd = (
            f"#!/bin/bash\n"
            f"set -o pipefail\n"
        )

        cwd = cwd or self.cwd
        if cwd:
            bashcmd += f"cd '{cwd}' || exit 15\n"

        effective_env = deepcopy(DEFAULT_ENV)
        if self.env: effective_env.update(self.env)
        if env: effective_env.update(env)
        for k, v in effective_env.items():
            bashcmd += f'export {k}="{v}"\n'
        
        bashcmd += (
            f"echo '{start_marker}'\n"
            f"set -e\n"
            f"{cmd} | cat -\n"
            f"echo\n"
            f"echo 1>&2\n"
            f"echo '{stop_marker}'\n"
        )
        logger.debug(bashcmd)



        p = run(sshcmd, async_=True, stdout=stdout, stderr=stderr, env=effective_env, input=bashcmd)
        deadline_started = arrow.get().shift(seconds=10)
        while True:
            if arrow.get() > deadline_started:
                raise ShellExecutor.TimeoutConnection()
            if data['started']:
                break

        deadline = arrow.get().shift(seconds=timeout)
        timeout_happened = False
        try:
            if not p.commands:
                raise Exception(f"Command failed: {cmd}")
            while True:

                p.commands[0].poll()

                if p.commands[0].returncode is not None and not p.commands[0].returncode and data['stop_marker']:
                    # Perfect End
                    break

                if p.commands[0].returncode is not None and p.commands[0].returncode:
                    break

                if arrow.get() > deadline:
                    p.commands[0].kill()
                    timeout_happened = True
                    p.commands[0].kill()
                    break
                time.sleep(0.05)

                if data['stop_marker']:
                    if (arrow.get() - data['stop_marker_arrived']).total_seconds() > 10 and not p.returncodes:
                        break
                if p.commands[0].returncode and not data['stop_marker_arrived']:
                    data.setdefault("waiting_for_stop", arrow.get())
                    if (arrow.get() - data['waiting_for_stop']).total_seconds() > 10 and not data['stop_marker_arrived']:
                        break

        finally:
            data['stop'] = True
        tstd.join()
        terr.join()
        stdout = '\n'.join(stdwriter.all_lines)
        stderr = '\n'.join(errwriter.all_lines)


        if p.returncodes:
            return_code = p.returncodes[0]
        elif data['stop_marker']:
            # script finished but ssh didnt get it
            return_code = 0
            if stderr.endswith("\n"):
                stderr = stderr[:-1]
        else:
            raise ShellExecutor.TimeoutFinished()
        # remove last line from bashcmd if good:
        if return_code == 0 and stdout.endswith("\n"):
            stdout = stdout[:-1]

        return {
            'timeout': timeout_happened,
            'exit_code': p.commands[0].returncode,
            'stdout': stdout,
            'stderr': stderr,
        }