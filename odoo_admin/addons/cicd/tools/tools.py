import tempfile
import base64
import subprocess
import shutil
from pathlib import Path
import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class OdooFrameworkException(Exception):
    pass


def get_host_ip():
    host_ip = (
        ".".join(
            subprocess.check_output(["/bin/hostname", "-I"])
            .decode("utf-8")
            .strip()
            .split(".")[:3]
        )
        + ".1"
    )
    return host_ip


@contextmanager
def tempdir():
    dir = Path(tempfile.mktemp(suffix="."))
    try:
        dir.mkdir(exist_ok=True, parents=True)
        yield Path(dir)
    finally:
        shutil.rmtree(dir)


def _get_shell_url(host, user, password, command):
    fontsize = 10
    pwd = base64.encodestring(password.encode("utf-8")).decode("utf-8")
    shellurl = (
        f"/console/?hostname={host}&"
        f"fontsize={fontsize}&"
        f"username={user}&password={pwd}&command="
    )
    shellurl += " ".join(command)
    return shellurl
