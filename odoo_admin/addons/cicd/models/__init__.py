import os
import traceback
from contextlib import contextmanager
import hashlib
import struct
from pathlib import Path
import logging
from odoo.addons.queue_job.exception import RetryableJobError
logger = logging.getLogger("CICD")

MAIN_FOLDER_NAME = "_main"

from . import ticketsystem
from . import mixin_size
from . import branch
from . import branch_button_actions
from . import branch_actions
from . import commit
from . import machine
from . import volume
from . import repository
from . import dump
from . import task
from . import release
from . import release_item
from . import registry
from . import test_run
from . import container
from . import database
from . import postgres_server
from . import user
from . import queue_job
from . import compressor
from . import release_actions
from . import wiz_new_branch