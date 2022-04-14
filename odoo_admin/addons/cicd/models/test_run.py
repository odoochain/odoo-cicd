from curses import wrapper
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


MAX_ERROR_SIZE = 100 * 1024 * 1024 * 1024

SETTINGS = (
    "RUN_POSTGRES=1\n"
    "DB_HOST=postgres\n"
    "DB_PORT=5432\n"
    "DB_USER=odoo\n"
    "DB_PWD=odoo\n"
    "ODOO_DEMO=1\n"
    "ODOO_QUEUEJOBS_CRON_IN_ONE_CONTAINER=1\n"
    "RUN_ODOO_QUEUEJOBS=0\n"
    "RUN_ODOO_CRONJOBS=0\n"
    "ODOO_LOG_LEVEL=warn\n"
)

logger = logging.getLogger(__name__)


class AbortException(Exception):
    pass


class WrongShaException(Exception):
    pass


class CicdTestRun(models.Model):
    _log_access = False
    _inherit = ['mail.thread', 'cicd.open.window.mixin']
    _name = 'cicd.test.run'
    _order = 'id desc'

    name = fields.Char(compute="_compute_name")
    do_abort = fields.Boolean("Abort when possible", tracking=True)
    create_date = fields.Datetime(
        default=lambda self: fields.Datetime.now(), required=True,
        readonly=True)
    date = fields.Datetime(
        "Date Started", default=lambda self: fields.Datetime.now(),
        required=True, tracking=True)
    commit_id = fields.Many2one("cicd.git.commit", "Commit", required=True)
    commit_id_short = fields.Char(related="commit_id.short", store=True)
    branch_id = fields.Many2one(
        'cicd.git.branch', string="Initiating branch", required=True)
    branch_id_name = fields.Char(related='branch_id.name', store=False)
    branch_ids = fields.Many2many(
        'cicd.git.branch', related="commit_id.branch_ids", string="Branches")
    repo_short = fields.Char(related="branch_ids.repo_id.short")
    state = fields.Selection([
        ('open', 'Ready To Test'),
        ('running', 'Running'),
        ('success', 'Success'),
        ('omitted', 'Omitted'),
        ('failed', 'Failed'),
    ], string="Result", required=True, default='open', tracking=True)
    success_rate = fields.Integer("Success Rate [%]", tracking=True)
    line_ids = fields.One2many('cicd.test.run.line', 'run_id', string="Lines")
    duration = fields.Integer("Duration [s]", tracking=True)
    queuejob_log = fields.Binary("Queuejob Log")
    queuejob_log_filename = fields.Char(compute="_queuejob_log_filename")

    def _queuejob_log_filename(self):
        for rec in self:
            rec.queuejob_log_filename = 'queuejobs.xlsx'

    def abort(self):
        for qj in self._get_queuejobs('all'):
            self.env.cr.execute((
                "update queue_job set state = 'failed' "
                "where id=%s "
            ), (qj['id'],))
        self.env['queue.job'].search([]).unlink()
        self.do_abort = False
        self.state = 'failed'

    def _abort_if_required(self):
        if self.do_abort:
            raise AbortException("User aborted")

    def _prepare_run(self):
        self = self._with_context()

        for i in range(10):
            self.with_delay()._report(f"{i} at _prepare_run")

    def _report(
        self, msg, state='success',
        exception=None, duration=None, ttype='log'
    ):
        # if not hasattr(report, 'last_report_time'):
        #     report.last_report_time = arrow.get()
        # if duration is None:
        #     duration = (arrow.get() - report.last_report_time)\
        #         .total_seconds()
        # elif isinstance(duration, datetime.timedelta):
        #     duration = duration.total_seconds()
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
        self.env.cr.commit()

        with self._logsio(None) as logsio:
            if state == 'success':
                logsio.info(msg)
            else:
                logsio.error(msg)

    def prepare_run(self):
        self = self._with_context()
        self._switch_to_running_state()

        self._report("Prepare run...")
        self.date = fields.Datetime.now()
        for i in range(10):
            self.with_delay()._report(f"{i} at prepare_run")
        self.as_job('prepare', False)._prepare_run()

    def execute_now(self):
        self.with_context(
            test_queue_job_no_delay=True,
            DEBUG_TESTRUN=True,
            FORCE_TEST_RUN=True).execute()
        return True

    def _get_qj_marker(self, suffix, afterrun):
        runtype = '__after_run__' if afterrun else '__run__'
        return (
            f"testrun-{self.id}-{runtype}"
            f"{suffix}"
        )

    def as_job(self, suffix, afterrun, eta=None):
        return self
        marker = self._get_qj_marker(suffix, afterrun=afterrun)
        eta = arrow.utcnow().shift(seconds=eta or 0).strftime(DTF)
        return self.with_delay(
            identity_key=marker,
            eta=eta
            )

    def _get_queuejobs(self, ttype):
        assert ttype in ['active', 'all']
        self.ensure_one()
        if ttype == 'active':
            domain = " state not in ('done') "
        else:
            domain = " 1 = 1 "

        # TODO safe
        marker = self._get_qj_marker("", False)
        domain += f" AND identity_key ilike '%{marker}%'"
        self.env.cr.execute((
            "select id, state, exc_info, identity_key "
            "from queue_job "
            "where " + domain
        ))
        queuejobs = self.env.cr.dictfetchall()

        def retryable(job):
            if job['state'] != 'failed':
                return True
            if 'could not serialize' in (job['exc_info'] or '').lower():
                return True
            return False

        if ttype == 'active':
            queuejobs = [x for x in queuejobs if retryable(x)]

        return queuejobs
    @contextmanager
    def _logsio(self, logsio=None):
        if logsio:
            yield logsio
        else:
            with self.branch_id.with_context(
                testrun="")._get_new_logsio_instance(
                    'test-run-execute') as logsio:
                yield logsio

    def _trigger_wait_for_finish(self):
        return
        # self.as_job(
        #     "wait_for_finish", False, eta=1)._wait_for_finish()

    def _wait_for_finish(self, task=None):
        self.ensure_one()
        if not self.exists():
            return
        if self.env.context.get('test_queue_job_no_delay'):
            return

        qj = self._get_queuejobs('active')
        if qj:
            raise RetryableJobError(
                "Waiting for test finish", seconds=30,
                ignore_retry=True)

        with self._logsio(None) as logsio:
            logsio.info(f"Duration was {self.duration}")

            qj = sorted(qj, key=lambda x: x['date_created'])
            if qj:
                self.duration = \
                        (arrow.utcnow() - arrow.get(qj[0]['date_created']))\
                    .total_seconds()
            else:
                self.duration = 0

        self.as_job("compute_success_rate", True)._compute_success_rate(
            task=task)

    @contextmanager
    def _shell(self, logsio=None):
        with self._logsio(logsio) as logsio:
            self = self._with_context()
            machine = self.branch_ids.repo_id.machine_id
            root = machine._get_volume('source')
            project_name = self.branch_id.project_name
            with machine._shell(
                cwd=root / project_name, project_name=project_name,
                logsio=logsio,
            ) as shell:
                yield shell

    # ----------------------------------------------
    # Entrypoint
    # ----------------------------------------------
    # env['cicd.test.run'].with_context(DEBUG_TESTRUN=True, FORCE_TEST_RUN=True).browse(nr).execute()
    def execute(self, task=None):
        self.ensure_one()

        self._switch_to_running_state()
        self.do_abort = False
        self.as_job('starting_games', False)._let_the_games_begin()

    def _switch_to_running_state(self):
        """
        Should be called by methods that to work on test runs.
        If a queuejob is revived then the state of the test run should
        represent this.
        """
        if self.state != 'running':
            self.state = 'running'

        self._trigger_wait_for_finish()

    def _with_context(self):
        testrun_context = f"_testrun_{self.id}"
        self = self.with_context(
            testrun=testrun_context,
            prefetch_fields=False
            )

        # lock test run
        self.env.cr.execute((
            "select id "
            "from cicd_test_run "
            "where id = %s "
            "for update nowait "
        ), (self.id,))

        return self

    def _let_the_games_begin(self):

        self.as_job('prepare-run', False).prepare_run()

    def _execute(self, shell, logsio, run, appendix):
        try:
            logsio.info("Running " + appendix)
            run(shell, logsio)
        except Exception as ex:
            logger.error(ex, exc_info=True)
            msg = traceback.format_exc()
            self._report(msg, exception=ex)

    def _compute_success_rate(self, task=None):
        self.ensure_one()
        lines = self.mapped('line_ids').filtered(
            lambda x: x.ttype != 'log')
        success_lines = len(lines.filtered(
            lambda x: x.state == 'success' or x.force_success))
        qj = self._get_queuejobs('all')
        failed_qj = bool([x for x in qj if x['state'] == 'failed'])
        if lines and not failed_qj and all(
                x.state == 'success' or x.force_success for x in lines):
            self.state = 'success'
        else:
            self.state = 'failed'
        if not lines or not success_lines:
            self.success_rate = 0
        else:
            self.success_rate = \
                int(100 / float(len(lines)) * float(success_lines))
        self.branch_id._compute_state()
        if task:
            if self.state == 'failed':
                task.state = 'failed'
            elif self.state == 'success':
                task.state = 'success'

    def _compute_name(self):
        for rec in self:
            date = rec.create_date.strftime("%Y-%m-%d %H:%M:%S")[:10]
            rec.name = f"{date} - {rec.branch_id.name}"

    @api.model
    def _get_ttypes(self, filtered):
        for x in self._fields['ttype'].selection:
            if filtered:
                if x[0] not in filtered:
                    continue
            yield x[0]

    def rerun(self):
        if self.branch_id.state not in ['testable', 'tested', 'dev']:
            raise ValidationError(
                _("State of branch does not allow a repeated test run"))
        self.abort()
        self = self.sudo()
        self.line_ids.unlink()
        self.state = 'open'
        self.success_rate = 0