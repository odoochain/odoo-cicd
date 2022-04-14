import traceback
from boltons import tbutils
import time
from curses import wrapper
import traceback

import arrow
from contextlib import contextmanager, closing
import base64
import datetime
from . import pg_advisory_lock
import traceback
import time
from odoo import _, api, fields, models
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT as DTF
from odoo.addons.queue_job.exception import RetryableJobError
from odoo.exceptions import ValidationError
import logging
from pathlib import Path
from contextlib import contextmanager
from ..tools.logsio_writer import LogsIOWriter

INTERVAL = 1
logger = logging.getLogger(__name__)

class CicdTestRun(models.Model):
    _log_access = False
    _inherit = ['mail.thread', 'cicd.open.window.mixin']
    _name = 'cicd.test.run'
    _order = 'id desc'

    name = fields.Char(compute="_compute_name")
    line_ids = fields.One2many('cicd.test.run.line', 'run_id')
    commit_id = fields.Many2one('cicd.git.commit')
    state = fields.Selection([('open', 'open'), ('running', 'Running'), ('done', 'Done')])

    def _prepare_run(self):
        for i in range(10):
            time.sleep(INTERVAL)
            self._report(f'in _prepare_run {i}')

    def _report(
        self, msg, state='success',
        exception=None, duration=None, ttype='log'
    ):
        if duration and isinstance(duration, datetime.timedelta):
            duration = duration.total_seconds()

        ttype = ttype or 'log'
        data = {
            'state': state,
            'name': msg,
            'ttype': ttype,
            'duration': duration
        }
        if exception:
            state = 'failed'
            msg = (msg or '') + '\n' + str(exception)
            data['exc_info'] = str(exception)
        else:
            state = state or 'success'

        self.line_ids = [[0, 0, data]]

        with LogsIOWriter.GET('test', 'test') as logs:
            logs.info(msg)

    def prepare_run(self):
        self = self._with_context()
        self.with_delay(identity_key='prepare_run_const')._prepare_run()

    def _get_qj_marker(self, suffix, afterrun):
        runtype = '__after_run__' if afterrun else '__run__'
        return (
            f"testrun-{self.id}-{runtype}"
            f"{suffix}"
        )

    def _trigger_wait_for_finish(self):
        self.as_job(
            "wait_for_finish", False, eta=1)._wait_for_finish()

    def execute(self, task=None):
        self.ensure_one()
        self.state = 'running'

        self.with_delay()._let_the_games_begin()

    def _with_context(self):
        self = self.with_context()
        return self

    def _let_the_games_begin(self):
        # CLOSE
        self = self._with_context()
        self.with_delay(identity_key='prepare-run').prepare_run()

    def _execute(self, shell, logsio, run, appendix):
        try:
            logsio.info("Running " + appendix)
            run(shell, logsio)
        except Exception as ex:
            logger.error(ex, exc_info=True)
            msg = traceback.format_exc()
            self._report(msg, exception=ex)

    def _compute_name(self):
        for rec in self:
            rec.name = str(rec.id)

    @api.model
    def _get_ttypes(self, filtered):
        for x in self._fields['ttype'].selection:
            if filtered:
                if x[0] not in filtered:
                    continue
            yield x[0]

    def rerun(self):
        self = self.sudo()
        self.line_ids.unlink()
        self.state = 'open'