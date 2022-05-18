from curses import wrapper
from functools import partial
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
from .shell_executor import ShellExecutor

MAX_ERROR_SIZE = 100 * 1024 * 1024 * 1024
BASE_SNAPSHOT_NAME = 'basesnap'

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


class TestFailedAtInitError(Exception):
    pass


class CicdTestRun(models.Model):
    _log_access = False
    _inherit = ['mail.thread', 'cicd.open.window.mixin', 'cicd.test.settings']
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
    machine_id = fields.Many2one(
        'cicd.machine', related="branch_id.repo_id.machine_id", store=False)
    no_reuse = fields.Boolean("No Reuse")
    queuejob_ids = fields.Many2many('queue.job', compute="_compute_queuejobs")

    def refresh_jobs(self):
        pass

    def _compute_queuejobs(self):
        for rec in self:
            ids = [x['id'] for x in self._get_queuejobs('all')]
            rec.queuejob_ids = [[6, 0, ids]]

    def _queuejob_log_filename(self):
        for rec in self:
            rec.queuejob_log_filename = 'queuejobs.xlsx'

    def abort(self):
        for qj in self._get_queuejobs('all'):
            self.env.cr.execute((
                "update queue_job set state = 'failed' "
                "where id=%s "
            ), (qj['id'],))
        self.do_abort = False
        self.state = 'failed'


    def _reload(self, shell, settings, instance_folder):
        with pg_advisory_lock(self.env.cr, f"testrun#{self.id}-reload"):
            def reload():
                try:
                    self.branch_id._reload(
                        shell, project_name=shell.project_name,
                        settings=settings, commit=self.commit_id.name,
                        force_instance_folder=instance_folder,
                        ),
                except Exception as ex:
                    logger.error(ex)
                    self._report("Exception at reload", exception=ex)
                    raise
                else:
                    self._report("Reloaded")

            self._report("Reloading for test run")
            try:
                try:
                    reload()
                except RetryableJobError:
                    self._report("Retryable error occurred at reloading")
                    raise
                except Exception as ex:  # pylint: disable=broad-except
                    self._report(f"Reloading: Exception stage 1 hit {ex}")
                    try:
                        shell.rm(instance_folder)
                        reload()
                    except RetryableJobError:
                        self._report(
                            "Retryable error occurred at reloading stage 2")
                        raise
                    except Exception as ex:
                        if 'reference is not a tree' in str(ex):
                            raise RetryableJobError((
                                "Missing commit not arrived "
                                "- retrying later.")) from ex
                        self._report("Error occurred", exception=ex)
                        raise

            except RetryableJobError as ex:
                self._report("Retrying", exception=ex)
                raise

            except AbortException as ex:
                pass

            except Exception as ex:
                self._report("Error", exception=ex)
                raise

    def _abort_if_required(self):
        if self.do_abort:
            raise AbortException("User aborted")

    def _log(self, func, comment=""):
        started = arrow.utcnow()
        if comment:
            self._report(comment)
        params = {}
        try:
            func(self)
        except Exception as ex:  # pylint: disable=broad-except
            params['exception'] = ex

        finally:
            params['duration'] = (arrow.utcnow() - started).total_seconds()
        self._report(comment, **params)
        self._abort_if_required()

    def _lo(self, shell, *params, comment=None, **kwparams):
        comment = comment or ' '.join(map(str, params))
        self._log(
            lambda self: shell.odoo(*params, **kwparams), comment=comment)

    def _ensure_source_and_machines(
        self, shell, start_postgres=False, settings=""
    ):
        self._log(
            lambda self: self._checkout_source_code(shell.machine),
            'checkout source'
        )
        assert self.env.context.get('testrun')
        lo = partial(self._lo, shell)
        # lo = lambda *args, **kwargs: self._lo(shell, *args, **kwargs)

        self._reload(
            shell, settings, shell.cwd)
        lo('regpull', allow_error=True)
        lo('build')
        lo('kill', allow_error=True)
        lo('rm', allow_error=True)
        if start_postgres:
            lo('up', '-d', 'postgres')
            shell.wait_for_postgres()

    def _cleanup_testruns(self):
        with self._logsio(None) as logsio:
            self._report("Cleanup Testing started...")
            instance_folder = self._get_source_path()
            with self.machine_id._shell(
                logsio=logsio,
                cwd=instance_folder,
            ) as shell:
                shell.remove(instance_folder)

            self._report("Cleanup Testing done.")

    def _report(
        self, msg, state='success',
        exception=None, duration=None, ttype='log'
    ):
        if duration and isinstance(duration, datetime.timedelta):
            duration = duration.total_seconds()

        if exception:
            if isinstance(exception, RetryableJobError):
                return

        ttype = ttype or 'log'
        data = {
            'state': state or 'success',
            'name': msg,
            'ttype': ttype,
            'duration': duration
        }
        if exception:
            data['state'] = 'failed'
            msg = (msg or '') + '\n' + str(exception)
            data['exc_info'] = str(exception)

        self.line_ids = [[0, 0, data]]
        self.env.cr.commit()

        with self._logsio(None) as logsio:
            if state == 'success':
                logsio.info(msg)
            else:
                logsio.error(msg)

    def _get_source_path(self):
        path = Path(self.machine_id._get_volume('source'))
        # one source directory for all tests; to have common .dirhashes
        # and save disk space
        project_name = self.branch_id.with_context(testrun="").project_name
        path = path / f"{project_name}_testrun_{self.id}"
        return path

    def _checkout_source_code(self, machine):
        try:
            assert machine._name == 'cicd.machine'

            with pg_advisory_lock(self.env.cr, f'testrun_{self.id}'):
                path = self._get_source_path()

                with machine._shell(cwd=path.parent) as shell:
                    if not shell.exists(path / '.git'):
                        shell.remove(path)
                        self._report("Checking out source code...")
                        self.branch_id.repo_id._get_main_repo(
                            destination_folder=path,
                            logsio=shell.logsio,
                            machine=machine,
                            limit_branch=self.branch_id.name,
                            depth=1,
                        )
                with shell.clone(cwd=path) as shell:
                    shell.X(["git", "checkout", "-f", self.commit_id.name])

                    self._report("Checking commit")
                    sha = shell.X(["git", "log", "-n1", "--format=%H"])[
                        'stdout'].strip()
                    if sha != self.commit_id.name:
                        raise WrongShaException((
                            f"checked-out SHA {sha} "
                            f"not matching test sha {self.commit_id.name}"
                            ))
                    self._report("Commit matches")
                    self._report(f"Checked out source code at {shell.cwd}")
        except ShellExecutor.TimeoutConnection:
            raise RetryableJobError(
                "SSH Timeout on checkout source",
                seconds=20, ignore_retry=True)


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

    def as_job(self, suffix, afterrun=False, eta=None):
        marker = self._get_qj_marker(suffix, afterrun=afterrun)
        eta = arrow.utcnow().shift(minutes=eta or 0).strftime(DTF)
        return self.with_delay(
            channel="testruns",
            identity_key=marker,
            eta=eta
            )

    def _get_queuejobs(self, ttype, include_wait_for_finish=False):
        assert ttype in ['active', 'all']
        self.ensure_one()
        if ttype == 'active':
            domain = " state not in ('done') "
        else:
            domain = " 1 = 1 "

        # TODO safe
        marker1 = self._get_qj_marker("", False)
        marker2 = self._get_qj_marker("", True)
        domain += (
            " AND ( "
            f" identity_key ilike '%{marker1}%' "
            " OR "
            f" identity_key ilike '%{marker2}%' "
            " ) "
        )
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

        def _filter(qj):
            idkey = qj['identity_key'] or ''
            if not include_wait_for_finish and 'wait_for_finish' in idkey:
                return False
            return True

        queuejobs = list(filter(_filter, queuejobs))
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
        self.as_job(
            "wait_for_finish", False, eta=1)._wait_for_finish()

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

        self.as_job("cleanup", True)._cleanup_testruns()

        self.as_job("compute_success_rate", True)._compute_success_rate(
            task=task)
        self.as_job('inform_developer', True)._inform_developer()

    @contextmanager
    def _shell(self, quick=False):
        assert self.env.context.get('testrun')
        with self.machine_id._shell(
            cwd=self._get_source_path(),
            project_name=self.branch_id.project_name,
        ) as shell:
            if not quick:
                self._ensure_source_and_machines(shell)
            yield shell

    def _switch_to_running_state(self):
        """
        Should be called by methods that to work on test runs.
        If a queuejob is revived then the state of the test run should
        represent this.
        """
        if self.state != 'running':
            self.state = 'running'

        self._trigger_wait_for_finish()

    # ----------------------------------------------
    # Entrypoint
    # ----------------------------------------------
    def execute(self, task=None):
        self.ensure_one()
        self.do_abort = False
        self.ensure_one()
        self._switch_to_running_state()
        self.env.cr.commit()
        self.line_ids.unlink()

        with self._logsio(None) as logsio:
            if not self.any_testing:
                logsio.info("No testing - so done")
                self.success_rate = 100
                self.state = 'success'
                return

            self._report("Started")

        if self.run_unittests:
            self.as_job('unittests')._run_unit_tests()

        if self.run_robottests:
            self.as_job('robots')._run_robot_tests()

        if self.simulate_install_id:
            self.as_job('migration')._run_udpate_db()

    def _compute_success_rate(self, task=None):
        self.ensure_one()
        lines = self.mapped('line_ids').filtered(
            lambda x: x.ttype != 'log')
        success_lines = len(lines.filtered(
            lambda x: x.state == 'success' or x.force_success or x.reused))
        any_failed_line = bool(
            self.line_ids.filtered(lambda x: x.state == 'failed'))
        if not lines and not any_failed_line:
            # perhaps in debugging and quickly testing releasing
            # or turning off tests
            self.state = 'success'
            self.success_rate = 100

        else:
            if lines and all(
                x.state == 'success' or
                x.force_success or x.reused for x in lines
            ) and not any_failed_line:
                self.state = 'success'
            else:
                self.state = 'failed'

            if not success_lines:
                self.success_rate = 0
            else:
                self.success_rate = int(
                    100 / float(len(lines)) * float(success_lines))
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
        breakpoint()
        if self.branch_id.state not in ['testable', 'tested', 'dev']:
            raise ValidationError(
                _("State of branch does not allow a repeated test run"))
        for qj in self._get_queuejobs('all'):
            if qj['state'] in ['pending', 'enqueued', 'started']:
                raise ValidationError(
                    "There are pending jobs - cannot restart")
        for qj in self._get_queuejobs('all', include_wait_for_finish=True):
            self.env.cr.execute("delete from queue_job where id = %s", (
                qj['id'],))
        self = self.sudo()
        self.line_ids.unlink()
        self.state = 'open'
        self.success_rate = 0

    def _run_update_db(self, shell, logsio, **kwargs):

        def _update(item):  # NOQA
            logsio.info(f"Restoring {self.branch_id.dump_id.name}")

            shell.odoo('-f', 'restore', 'odoo-db', self.branch_id.dump_id.name)
            shell.wait_for_postgres()
            shell.odoo('update', timeout=self.timeout_migration)
            shell.wait_for_postgres()

        self._generic_run(
            shell, logsio, [None],
            'migration', _update,
        )

    def _get_generic_run_name(
        self, item, name_callback
    ):
        name = item or ''
        if name_callback:
            try:
                name = name_callback(item)
            except Exception:  # pylint: disable=broad-except
                logger.error("Error at name bacllback", exc_info=True)
                pass
        return name

    def _generic_run(
        self, shell, todo, ttype, execute_run,
        try_count=1, name_callback=None, name_prefix='',
        hash=False, odoo_module=None,
    ):
        """

        """
        self._switch_to_running_state()
        success = True
        len_todo = len(todo)
        for i, item in enumerate(todo):
            trycounter = 0

            name = self._get_generic_run_name(item, name_callback)
            if hash and self.env['cicd.test.run.line'] \
                    .check_if_test_already_succeeded(
                self, name, hash,
            ):
                continue

            position = name_prefix or ''
            if len_todo > 0:
                position += f"({i + 1} / {len_todo})"

            while trycounter < try_count:
                if self.do_abort:
                    raise AbortException("Aborted by user")
                trycounter += 1
                shell.logsio.info(f"Try #{trycounter}")

                started = arrow.get()
                data = {
                    'position': position,
                    'name': name,
                    'ttype': ttype,
                    'run_id': self.id,
                    'started': started.datetime.strftime("%Y-%m-%d %H:%M:%S"),
                    'try_count': trycounter,
                    'hash': hash,
                    'odoo_module': odoo_module or False,
                }
                try:
                    shell.logsio.info(f"Running {name}")
                    result = execute_run(item)
                    if result:
                        data.update(result)

                except Exception:
                    msg = traceback.format_exc()
                    shell.logsio.error(f"Error happened: {msg}")
                    data['state'] = 'failed'
                    data['exc_info'] = msg
                    success = False
                else:
                    # e.g. robottests return state from run
                    if 'state' not in data:
                        data['state'] = 'success'
                end = arrow.get()
                data['duration'] = (end - started).total_seconds()
                if data['state'] == 'success':
                    break
            self.line_ids = [[0, 0, data]]
            self.env.cr.commit()
        return success

    def _inform_developer(self):
        for rec in self:
            partners = (
                rec.commit_id.author_user_id.mapped('partner_id')
                | rec.commit_id.branch_ids.mapped('assignee_id.partner_id')
                | rec.mapped('message_follower_ids.partner_id')
                | rec.branch_id.mapped('message_follower_ids.partner_id')
            )

            rec.message_post_with_view(
                "cicd.mail_testrun_result",
                subtype_id=self.env.ref('mail.mt_note').id,
                partner_ids=partners.ids,
                values={
                    "obj": rec,
                },
            )
