import tempfile
import traceback
import base64
import arrow
import uuid
from contextlib import contextmanager
import threading
from sarge import Capture, run
from odoo.exceptions import UserError
import time
from copy import deepcopy
from pathlib import Path
import logging
from odoo.addons.queue_job.exception import RetryableJobError
logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 6 * 3600


def duration(d):
    return (arrow.get() - d).total_seconds()


class MyWriter():
    def __init__(self, ttype, logger, logoutput):
        self.text = [""]
        self.ttype = ttype
        self.line = ""
        self.logger = logger
        self.logoutput = logoutput
        self.filepath = tempfile.mktemp(suffix='shellwriter.log')
        self.file = open(self.filepath, 'a+')

    def __del__(self):
        self.cleanup()

    def cleanup(self):
        try:
            if not self.file.closed:
                self.file.close()
            path = Path(self.filepath)
            if path.exists():
                path.unlink()
        except Exception:
            msg = traceback.format_exc()
            logger.error(msg)

    def get_all_lines(self):
        self.file.seek(0)
        return self.file.read()

    def write(self, line):
        if line is None:
            return
        line = line.decode("utf-8")
        self.file.write(line)
        if line.endswith("\n"):
            line = line[:-1]
        if self.logoutput and self.logger:
            if self.ttype == 'error':
                self.logger.error(line)
            else:
                self.logger.info(line)


class BaseShellExecutor():
    class TimeoutConnection(Exception):
        pass

    class TimeoutFinished(Exception):
        pass

    def __init__(
        self, ssh_keyfile, host,
        cwd, env=None, user=None,
    ):

        self.host = host
        self._cwd = Path(cwd) if cwd else None
        self.env = env or {}
        if env:
            assert isinstance(env, dict)
        self.ssh_keyfile = ssh_keyfile
        self.user = user
        if not user:
            raise Exception("User required!")

    def _internal_execute(
        self, cmd, cwd=None, env=None, logoutput=True, timeout=None
    ):

        if timeout is None:
            timeout = DEFAULT_TIMEOUT

        # region: conversions
        def convert_path(x):
            if isinstance(x, Path):
                x = str(x)
            return x

        cmd = list(map(convert_path, cmd))

        if isinstance(cmd, (tuple, list)):
            cmd = f"{cmd[0]} " + " ".join(map(lambda x: f'"{x}"', cmd[1:]))
        cmd = cmd.replace('\n', ' ')
        # endregion

        # region: Writer Class

        stdwriter = MyWriter('info', self.get_logger(), logoutput)
        errwriter = MyWriter('error', self.get_logger(), logoutput)

        # endregion

        sshcmd = self._get_ssh_client()
        stop_marker = str(uuid.uuid4()) + str(uuid.uuid4())
        start_marker = str(uuid.uuid4()) + str(uuid.uuid4())

        # region: start collecting threads
        stdout = Capture(buffer_size=-1)  # line buffering
        stderr = Capture(buffer_size=-1)  # line buffering
        data = {
            'stop': False,
            'started': False,
            'stop_marker': False,
        }

        def on_started():
            data['started'] = True

        def on_stop_marker():
            data['stop_marker'] = arrow.get()

        def collect(
            capture, writer, marker=None, on_marker=None,
            stop_marker=None, on_stop_marker=None
        ):

            while not data['stop']:
                for line in capture:
                    line_decoded = line.decode('utf-8')
                    is_marker = False
                    if marker and marker in line_decoded and on_started:
                        on_marker()
                        is_marker = True
                    if stop_marker and stop_marker in line_decoded and \
                            on_stop_marker:
                        on_stop_marker()
                        is_marker = True

                    if not is_marker:
                        writer.write(line)

        tstd = threading.Thread(target=collect, args=(
            stdout, stdwriter, start_marker,
            on_started, stop_marker, on_stop_marker))

        terr = threading.Thread(target=collect, args=(stderr, errwriter))
        tstd.daemon = True
        terr.daemon = True
        [x.start() for x in [tstd, terr]]  # NOQA
        # endregion

        # region: build command chain
        bashcmd = (
            "#!/bin/bash\n"
            "set -o pipefail\n"
        )

        cwd = cwd or self.cwd
        if cwd:
            bashcmd += f"cd '{cwd}' || exit 15\n"

        effective_env = deepcopy(self.env or {})
        if env:
            effective_env.update(env)
        for k, v in effective_env.items():
            bashcmd += f'export {k}="{v}"\n'

        bashcmd += (
            f"echo '{start_marker}'\n"
            f"touch ~/.hushlogin  # suppress motd "
            "to correctly parse git outputs\n"
            f"set -e\n"
            f"{cmd} | cat -\n"
            f"echo\n"
            f"echo 1>&2\n"
            f"echo '{stop_marker}' \n"
        )
        # endregion

        # region: run command
        p = run(
            sshcmd, async_=True, stdout=stdout,
            stderr=stderr, env=effective_env, input=bashcmd)
        deadline_started = arrow.get().shift(seconds=60)
        while True:
            if p.returncodes and any(x is not None for x in p.returncodes):
                break
            if arrow.get() > deadline_started:
                raise BaseShellExecutor.TimeoutConnection()
            if data['started']:
                break
            if p.commands:
                p.commands[0].poll()

        deadline = arrow.get().shift(seconds=timeout)
        timeout_happened = False
        try:
            if not p.commands:
                raise Exception(f"Command failed: {cmd}")
            while True:
                p.commands[0].poll()

                if p.commands[0].returncode is not None and \
                        not p.commands[0].returncode and \
                        data.get('stop_marker'):
                    # Perfect End
                    break

                if p.commands[0].returncode is not None and \
                        p.commands[0].returncode:
                    break

                if arrow.get() > deadline:
                    p.commands[0].kill()
                    timeout_happened = True
                    p.commands[0].kill()
                    break
                time.sleep(0.05)

                if data.get('stop_marker'):
                    if duration(
                            data['stop_marker']) > 10 and not p.returncodes:
                        break
                if p.commands[0].returncode is not None and \
                        not data.get('stop_marker'):
                    data.setdefault("waiting_for_stop", arrow.get())
                    if duration(data['waiting_for_stop']) > 5:
                        break

        finally:
            data['stop'] = True
        tstd.join()
        terr.join()
        # endregion

        # region: evaluate run result
        stdout = stdwriter.get_all_lines()
        stderr = errwriter.get_all_lines()

        stdwriter.cleanup()
        errwriter.cleanup()

        if p.returncodes:
            return_code = p.returncodes[0]
        elif data.get('stop_marker'):
            # script finished but ssh didnt get it
            return_code = 0
            if stderr.endswith("\n"):
                stderr = stderr[:-1]
        else:
            raise BaseShellExecutor.TimeoutFinished()

        # remove last line from bashcmd if good:
        if return_code == 0 and stdout.endswith("\n"):
            stdout = stdout[:-1]
        # endregion

        return {
            'timeout': timeout_happened,
            'exit_code': p.commands[0].returncode,
            'stdout': stdout,
            'stderr': stderr,
        }

    def _get_ssh_client(self, cmd='ssh', split_host=False):
        host = self.host
        user = self.user
        base = f"{cmd} -T -oStrictHostKeyChecking=no -i {self.ssh_keyfile}"
        user_host = f"{user}@{host}"
        if split_host:
            return base, user_host
        return base + " " + user_host + " "

    def get_logger(self):
        return logger


def RetryOnTimeout(method, seconds=20):
    def wrapper(*args, **kwargs):
        try:
            result = method(*args, **kwargs)
        except BaseShellExecutor.TimeoutConnection:
            raise RetryableJobError(
                "SSH Timeout happened - retrying",
                seconds=seconds, ignore_retry=True)

        return result
    return wrapper