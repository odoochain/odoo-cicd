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

def is_lock_set(cr, lock):
    lock = _int_lock(lock)
    if lock > 2147483647 or lock < 0:
        raise Exception("Lock int should be low - somehow written in objid and classid")
    cr.execute("select count(*) from pg_locks where locktype = 'advisory' and objid=%s", (lock,))
    return bool(cr.fetchone()[0])

def _int_lock(lock):
    if isinstance(lock, str):
        hasher = hashlib.sha1(str(lock).encode())
        # pg_lock accepts an int8 so we build an hash composed with
        # contextual information and we throw away some bits
        int_lock = struct.unpack("q", hasher.digest()[:8])
    else:
        int_lock = lock
    return int_lock

def pg_try_advisory_lock(cr, lock):
    cr.execute("SELECT pg_try_advisory_xact_lock(%s);", (_int_lock(lock),))
    acquired = cr.fetchone()[0]
    return acquired

def pg_advisory_xact_lock(cr, lock):
    cr.execute("SELECT pg_advisory_xact_lock(%s);", (_int_lock(lock),))


@contextmanager
def pg_advisory_lock(cr, lock, do_try=True):
    lock = _int_lock(lock)
    if do_try:
        cr.execute("SELECT pg_try_advisory_lock(%s);", (lock,))
        if not cr.fetchone()[0]:
            trace = '\n'.join(traceback.format_stack())
            raise RetryableJobError(f"Lock could not be acquired: {lock} at: \n{trace}", ignore_retry=True)
    else:
        cr.execute("SELECT pg_advisory_lock(%s);", (lock,))
    try:
        logger.info(f"Acquired advisory lock {lock}")
        yield
    finally:
        try:
            cr.execute("SELECT pg_advisory_unlock(%s);", (lock,))
        except Exception:
            logger.warn(f"Could not release lock because of connection. Perhaps already closed so ok.", exc_info=True)


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