import arrow
import re
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from odoo.addons.queue_job.job import Job
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT as DTF
from odoo.tools import DEFAULT_SERVER_DATE_FORMAT


class queuejob(models.Model):
    _inherit = "queue.job"

    duration = fields.Float("Duration", compute="_compute_duration")

    @api.depends("date_started", "date_done")
    def _compute_duration(self):
        for rec in self:
            if not rec.date_done or not rec.date_started:
                rec.duration = 0
                continue
            duration = (
                arrow.get(rec.date_done) - arrow.get(rec.date_started)
            ).total_seconds()
            rec.duration = max(0, duration)

    def run_now(self):
        for self in self:
            self.ensure_one()

            job = Job.load(self.env, self.uuid)

            job.set_started()
            job.store()
            job.perform()
            job.set_done()
            job.store()

    branch = fields.Char(compute="_compute_branch", store=False)

    def _compute_branch(self):
        for rec in self:
            b = False
            if rec.identity_key:
                b = re.findall(r"branch:([^:]*):", rec.identity_key)
                if b:
                    b = b[0]
                else:
                    b = False
            if b != rec.branch:
                rec.branch = b

    @api.model
    def requeue_jobs(self):

        delete = [
            "docker-containers-",
            "machine-update-vol-sizes",
        ]
        for delete in delete:
            self.search(
                [("state", "=", "failed"), ("identity_key", "ilike", f"%{delete}%")]
            ).unlink()

        # delete unimported missed jobs
        for delete_excinfo in [
            "Cannot start two jobs for same identity key",
            "Cannot start two test runs for same commit",
            "cicd.models.test_run.AbortException",
        ]:
            self.search(
                [("state", "in", ["failed"]), ("exc_info", "ilike", delete_excinfo)]
            ).unlink()

        reasons = [
            "could not serialize access due to concurrent update",
            "cannot stat",
            "server closed the connection unexpectedly",
            "RetryableJobError",
            "Failed to put the local file",
            "psycopg2.errors",
            ".git/index.lock",
            'duplicate key value violates unique constraint "cicd_git_commit_name"',
            # following happened when using bitbucket - was slow on that day and timed out
            "Could not read from remote repository",
            "Lock could not be acquired",
            "LockNotAvailable",
            "Permission denied",
            "does not exist yet",
            "current transaction is aborted",
            "psycopg2.errors.InFailedSqlTransaction",
            "func_trigger_queuejob_state_check_at_commit",
            "-bash: line%No such file or directory",
            "could not lock config file",  # changing with git config
            "could not connect to server: Connection refused",
            "server closed the connection unexpectedly",
            "ssh_exchange_identification: read: Connection reset by peer",
            # git pull timeout
            "Connection timed out",
            # at test release the pull in odoo_main1 failed because feature1
            # cannot be overriden? perhaps analyze - redoing it works without prob
            "The following untracked working tree files would be overwritten by merge",
            "error: could not fetch origin",
            "FATAL: the database system is shutting down",
            "FATAL: the database system is starting up",
            "FATAL:  the database system is shutting down",
            "FATAL:  the database system is starting up",
        ]

        ignore_idkeys = [
            "docker-containers",
            "dump-update",
            "last-access-",
            "machine-update-vol-sizes-",
            "update_databases",
        ]
        idkeys = set()
        crit_date = arrow.utcnow().shift(days=-1).strftime(DTF)

        for reason in reasons:
            for job in self.search(
                [
                    ("state", "=", "failed"),
                    ("date_created", ">", crit_date),
                    "|",
                    ("exc_info", "ilike", reason),
                    ("result", "ilike", reason),
                ]
            ):
                for ignore in ignore_idkeys:
                    if ignore in (job.identity_key or ""):
                        break
                else:
                    if job.identity_key:
                        if job.identity_key not in idkeys:
                            if not self.search_count(
                                [
                                    ("state", "not in", ["cancel", "failed", "done"]),
                                    ("identity_key", "=", job.identity_key),
                                ]
                            ):
                                job.requeue()
                        idkeys.add(job.identity_key)
                    else:
                        job.requeue()

    def _message_failed_job(self):
        # deactivate error mails as jobs are requeud
        return ""
