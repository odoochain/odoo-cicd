import tempfile
import subprocess
import shutil
from pathlib import Path
import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class OdooFrameworkException(Exception): pass

def get_host_ip():
    host_ip = '.'.join(subprocess.check_output(["/bin/hostname", "-I"]).decode('utf-8').strip().split(".")[:3]) + '.1'
    return host_ip

@contextmanager
def tempdir():
    dir = Path(tempfile.mktemp(suffix='.'))
    try:
        dir.mkdir(exist_ok=True, parents=True)
        yield Path(dir)
    finally:
        shutil.rmtree(dir)