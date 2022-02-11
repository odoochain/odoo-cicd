import inspect
import arrow
import sys
import os
from pathlib import Path
import sys
import time
import threading
current_dir = Path(os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe()))))

import pexpect



from sarge import Capture, run
from io import TextIOWrapper

cmd = f"ssh localhost {current_dir}/slow.py"

stdout = Capture(buffer_size=-1)
stderr = Capture(buffer_size=-1)
p = run(cmd, async_=True, stdout=stdout, stderr=stderr)

deadline = arrow.get().shift(seconds=3)

while p.commands[0].returncode is None:
    for line in stdout:
        sys.stdout.write(line.decode("UTF-8"))
        sys.stdout.flush()

    for line in stderr:
        sys.stdout.write(line.decode("UTF-8"))
        sys.stdout.flush()

    p.commands[0].poll()
    time.sleep(0.05)

    if arrow.get() > deadline:
        p.commands[0].kill()

print(p.returncode)
import pudb;pudb.set_trace()