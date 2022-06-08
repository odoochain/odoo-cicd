from curses import wrapper
import arrow
from contextlib import contextmanager, closing
from . import pg_advisory_lock
from odoo import _, api, fields, models
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT as DTF
from odoo.addons.queue_job.exception import RetryableJobError
from odoo.exceptions import ValidationError
import logging
from pathlib import Path
from contextlib import contextmanager
import inspect
import os
from pathlib import Path

current_dir = Path(
    os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
)

MAX_ERROR_SIZE = 100 * 1024 * 1024 * 1024
BASE_SNAPSHOT_NAME = "basesnap"

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


class TestFailedAtInitError(Exception):
    pass


class CicdTestRun(models.Model):
    _log_access = False
    _inherit = ["mail.thread", "cicd.open.window.mixin", "cicd.test.settings"]
    _name = "cicd.test.run"
    _order = "id desc"

    name = fields.Char(compute="_compute_name")
    do_abort = fields.Boolean("Abort when possible", tracking=True)
    create_date = fields.Datetime(
        default=lambda self: fields.Datetime.now(), required=True, readonly=True
    )
    date = fields.Datetime(
        "Date Created",
        default=lambda self: fields.Datetime.now(),
        required=True,
        tracking=True,
    )
    date_started = fields.Datetime("Date Started")
    commit_id = fields.Many2one("cicd.git.commit", "Commit", required=True)
    commit_id_short = fields.Char(related="commit_id.short", store=True)
    branch_id = fields.Many2one(
        "cicd.git.branch", string="Initiating branch", required=True
    )
    branch_id_name = fields.Char(related="branch_id.name", store=False)
    branch_ids = fields.Many2many(
        "cicd.git.branch", related="commit_id.branch_ids", string="Branches"
    )
    repo_short = fields.Char(related="branch_ids.repo_id.short")
    state = fields.Selection(
        [
            ("open", "Ready To Test"),
            ("running", "Running"),
            ("success", "Success"),
            ("omitted", "Omitted"),
            ("failed", "Failed"),
        ],
        string="Result",
        required=True,
        default="open",
        tracking=True,
    )
    duration = fields.Integer("Duration [s]", tracking=True)
    queuejob_log = fields.Binary("Queuejob Log")
    queuejob_log_filename = fields.Char(compute="_queuejob_log_filename")
    no_reuse = fields.Boolean("No Reuse")
    queuejob_ids = fields.Many2many("queue.job", compute="_compute_queuejobs")
    line_unittest_ids = fields.One2many(
        "cicd.test.run.line.unittest", "run_id", string="Unit-Tests"
    )
    line_robottest_ids = fields.One2many(
        "cicd.test.run.line.robottest", "run_id", string="Robot Tests"
    )
    line_migration_ids = fields.One2many(
        "cicd.test.run.line.migration", "run_id", string="Migration Tests"
    )

    def init(self):
        super().init()
        self.env.cr.execute((current_dir / "test_run_trigger.sql").read_text())

    def refresh_jobs(self):
        pass

    def _compute_queuejobs(self):
        for rec in self:
            ids = [x["id"] for x in self._get_queuejobs("all")]
            rec.queuejob_ids = [[6, 0, ids]]

    def _queuejob_log_filename(self):
        for rec in self:
            rec.queuejob_log_filename = "queuejobs.xlsx"

    def abort(self):
        for qj in self._get_queuejobs("active"):
            self.env.cr.execute(
                ("update queue_job set state = 'done' " "where id=%s "), (qj["id"],)
            )
        self.do_abort = True
        self.state = "failed"
        for field in self._get_test_run_fields():
            for test_setup in self[field]:
                test_setup.reset_at_testrun()

    def _reload(self, shell, settings, instance_folder):
        def reload():
            try:
                self.branch_id._reload(
                    shell,
                    project_name=shell.project_name,
                    settings=settings,
                    commit=self.commit_id.name,
                    force_instance_folder=instance_folder,
                    no_update_images=True,
                ),
            except Exception as ex:
                logger.error(ex)
                self._report("Exception at reload", exception=ex)
                raise
            else:
                self._report("Reloaded")

        self._report("Reloading for test run")
        try:
            reload()
        except RetryableJobError:
            raise

        except AbortException:
            pass

        except Exception as ex:  # pylint: disable=broad-except
            if "reference is not a tree" in str(ex):
                raise RetryableJobError(
                    ("Missing commit not arrived " "- retrying later.")
                ) from ex
            self._report("Error occurred", exception=ex)
            raise

    def _abort_if_required(self):
        if self.do_abort and not self.env.context.get("testrun_cleanup"):
            raise AbortException("User aborted")

    def _cleanup_testruns(self):
        with self._logsio(None) as logsio:
            logsio.info("Cleanup Testing started...")

            for line in self.iterate_all_test_settings():
                line.cleanup()
            logsio.info("Cleanup Testing done.")

    def execute_now(self):
        self.with_context(
            test_queue_job_no_delay=True, DEBUG_TESTRUN=True, FORCE_TEST_RUN=True
        ).execute()
        return True

    @contextmanager
    def _logsio(self, logsio=None):
        if logsio:
            yield logsio
        else:
            with self.branch_id.with_context(testrun="")._get_new_logsio_instance(
                "test-run-execute"
            ) as _logsio:
                yield _logsio

    def _trigger_wait_for_finish(self):
        self.as_job("wait_for_finish", False, eta=1)._wait_for_finish()

    def _wait_for_finish(self):
        self.ensure_one()
        if self.env.context.get("test_queue_job_no_delay"):
            return
        try:
            if not self.exists():
                return

            qj = self._get_queuejobs("active")
            qjobs_failed_dead = self._get_failed_queuejobs_which_wont_be_requeued()

            if qjobs_failed_dead:
                self.abort()
                exceptions = "\n".join(
                    filter(bool, [x["exc_info"] for x in qjobs_failed_dead])
                )
                self.message_post(
                    body=(
                        "Brain-Dead Queuejobs detected - aborting testrun.\n"
                        "Exceptions there: \n"
                        f"{exceptions}"
                    )
                )
            elif qj:
                raise RetryableJobError(
                    "Waiting for test finish", seconds=30, ignore_retry=True
                )

            self.duration = (
                arrow.utcnow() - arrow.get(self.date_started)
            ).total_seconds()
            self.as_job("cleanup", True)._cleanup_testruns()

            self.as_job("compute_success_state", True)._compute_success_state()
            self.as_job("inform_developer", True)._inform_developer()

        except RetryableJobError:
            raise

        except Exception as ex:  # pylint: disable=broad-except
            self.message_post(body=(f"Error occurred at wait for finish: {ex}"))
            self.env.cr.commit()
            raise RetryableJobError(str(ex), ignore_retry=True) from ex

    def _switch_to_running_state(self):
        """
        Should be called by methods that to work on test runs.
        If a queuejob is revived then the state of the test run should
        represent this.
        """
        if self.state != "running":
            self.state = "running"

        self._trigger_wait_for_finish()

    # ----------------------------------------------
    # Entrypoint
    # ----------------------------------------------
    def execute(self):
        self.ensure_one()
        self.do_abort = False
        self.date_started = fields.Datetime.now()
        self._switch_to_running_state()
        self.env.cr.commit()

        with self._logsio(None) as logsio:
            if not self.any_testing:
                logsio.info("No testing - so done")
                self.success_rate = 100
                self.state = "success"
                return

            logsio.info(f"Started Testrun {self.name}")

        for test_setup in self.iterate_all_test_settings():
            test_setup.as_job(test_setup.name).produce_test_run_lines(self)

    def _compute_success_state(self):
        self.ensure_one()
        if self._is_success:
            self.state = "success"
        else:
            self.state = "failed"
        self.branch_id._compute_state()

    def _compute_name(self):
        for rec in self:
            date = rec.create_date.strftime("%Y-%m-%d %H:%M:%S")[:10]
            rec.name = f"{date} - {rec.branch_id.name}"

    @api.model
    def _get_ttypes(self, filtered):
        for x in self._fields["ttype"].selection:
            if filtered:
                if x[0] not in filtered:
                    continue
            yield x[0]

    def rerun(self):
        for qj in self._get_queuejobs("all", include_wait_for_finish=True):
            if qj["state"] not in ["done"]:
                raise ValidationError("There are pending jobs - cannot restart")

        for qj in self._get_queuejobs("all", include_wait_for_finish=True):
            self.env.cr.execute("delete from queue_job where id = %s", (qj["id"],))

        self = self.sudo()
        self.state = "open"
        for line in self.iterate_all_test_settings():
            line.reset_at_testrun()

    @api.recordchange("state")
    def _on_state_change(self):
        for rec in self:
            if rec.state == "open":
                rec.duration = False
                rec.date_started = False

    def _inform_developer(self):
        for rec in self:
            partners = (
                rec.commit_id.author_user_id.mapped("partner_id")
                | rec.commit_id.branch_ids.mapped("assignee_id.partner_id")
                | rec.mapped("message_follower_ids.partner_id")
                | rec.branch_id.mapped("message_follower_ids.partner_id")
            )

            rec.message_post_with_view(
                "cicd.mail_testrun_result",
                subtype_id=self.env.ref("mail.mt_note").id,
                partner_ids=partners.ids,
                values={
                    "obj": rec,
                },
            )

    def _get_qj_marker(self, suffix, afterrun):
        runtype = "__after_run__" if afterrun else "__run__"
        return f"testrun-{self.id}-{runtype}" f"{suffix}"

    def as_job(self, suffix, afterrun=False, eta=None):
        marker = self._get_qj_marker(suffix, afterrun=afterrun)
        eta = arrow.utcnow().shift(minutes=eta or 0).strftime(DTF)
        return self.with_delay(channel="testruns", identity_key=marker, eta=eta)

    def _get_failed_queuejobs_which_wont_be_requeued(self):
        queuejobs = list(
            filter(lambda x: x["state"] == "failed", self._get_queuejobs("active"))
        )

        timeout_minutes = int(
            self.env.ref("cicd.test_timeout_queuejobs_testruns").value
        )
        queuejobs = list(
            filter(
                lambda x: arrow.get(x["date_created"]).shift(minutes=timeout_minutes)
                < arrow.utcnow(),
                queuejobs,
            )
        )

        # these queuejobs are considered as fully dead
        return queuejobs

    def _get_queuejobs(self, ttype, include_wait_for_finish=False):
        assert ttype in ["active", "all"]
        self.ensure_one()
        if ttype == "active":
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
        self.env.cr.execute(
            (
                "select id, state, exc_info, identity_key, date_created "
                "from queue_job "
                "where " + domain
            )
        )
        queuejobs = self.env.cr.dictfetchall()

        def _filter(qj):
            idkey = qj["identity_key"] or ""
            if not include_wait_for_finish and "wait_for_finish" in idkey:
                return False
            return True

        queuejobs = list(filter(_filter, queuejobs))
        return queuejobs
