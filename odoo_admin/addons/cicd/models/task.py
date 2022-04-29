import json
import traceback
from . import pg_advisory_lock
import arrow
import traceback
from contextlib import contextmanager, closing
import logging
from odoo.addons.queue_job.exception import RetryableJobError
from odoo import _, api, fields, models, SUPERUSER_ID, registry
from odoo import registry
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from odoo.addons.queue_job.models.queue_job import STATES
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT
from odoo.tools import DEFAULT_SERVER_DATE_FORMAT
logger = logging.getLogger('cicd_task')

PENDING = "pending"
ENQUEUED = "enqueued"
DONE = "done"
STARTED = "started"
FAILED = "failed"

class Task(models.Model):
    _inherit = ['mixin.queuejob.semaphore']
    _name = 'cicd.task'
    _order = 'date desc'

    model = fields.Char("Model")
    res_id = fields.Integer("ID")
    display_name = fields.Char(
        compute="_compute_display_name", store=True)
    machine_id = fields.Many2one(
        'cicd.machine', string="Machine", readonly=True)
    branch_id = fields.Many2one('cicd.git.branch', string="Branch")
    name = fields.Char("Name")
    date = fields.Datetime(
        "Date", default=lambda self: fields.Datetime.now(), readonly=True)
    is_done = fields.Boolean(
        compute="_compute_is_done", store=False, prefetch=False)

    state = fields.Selection(selection=STATES, string="State")
    log = fields.Text("Log", readonly=True)
    dump_used = fields.Char("Dump used", readonly=True)
    duration = fields.Integer("Duration [s]", readonly=True)
    commit_id = fields.Many2one(
        "cicd.git.commit", string="Commit", readonly=True)
    testrun_id = fields.Many2one('cicd.test.run')

    kwargs = fields.Text("KWargs")
    identity_key = fields.Char()
    started = fields.Datetime("Started")
    ignore_previous_tasks = fields.Boolean("Ignore previous tasks")

    def _compute_state(self):
        for rec in self:
            qj = rec._semaphore_get_queuejob()
            if not qj:
                # keep last state as queuejobs are deleted from time to time
                pass
            else:
                if DONE in qj.mapped('state'):
                    rec.state = DONE
                elif FAILED in qj.mapped('state'):
                    rec.state = FAILED
                elif qj:
                    rec.state = STARTED
                else:
                    rec.state = False
                if qj:
                    qj = qj.sorted(lambda x: x.id, reverse=True)[0]
                    rec.log = qj.exc_info

    @api.depends('state')
    def _compute_is_done(self):
        for rec in self:
            rec.is_done = rec.state in [DONE, FAILED] \
                if rec.state else False

    @api.depends('name')
    def _compute_display_name(self):
        for rec in self:
            name = rec.name
            name = name.replace("obj.", "")
            if name.startswith("_"):
                name = name[1:]
            name = name.split("(")[0]
            rec.display_name = name

    def perform(self, now=False, ignore_previous_tasks=False):
        if now:
            breakpoint()
        self.ensure_one()
        self.env.cr.commit()
        self._exec(now, ignore_previous_tasks=ignore_previous_tasks)

    @property
    def semaphore_qj_identity_key(self):
        with self._extra_env() as x_self:
            appendix = (
                f"branch:{x_self.branch_id.repo_id.short}"
                f"-{x_self.branch_id.name}:"
            )

            if x_self.identity_key:
                return x_self.identity_key + " " + appendix

        name = self._get_short_name()
        with self._extra_env() as self2:
            project_name = self2.branch_id.project_name

        return f"{project_name}_{name} " + appendix

    def _get_short_name(self):
        name = self._unblocked('name') or ''
        if name.startswith("_"):
            name = name[1:]
        return name

    def _check_previous_tasks(self):
        with self._extra_env(enabled=not now) as check:
            previous = check.branch_id.task_ids.filtered(
                lambda x: x.id < check.id).filtered(
                    lambda x: x.state in [False, STARTED])
            
            if previous:
                raise RetryableJobError((
                    "Previous tasks exist: "
                    f"IDs: {previous.ids}"
                ), ignore_retry=True, seconds=30)


    def _exec(self, now=False, ignore_previous_tasks=False):
        if not self._unblocked('branch_id'):
            raise Exception("Branch not given for task.")

        if not ignore_previous_tasks:
            try:
                self._check_previous_tasks()
            except RetryableJobError as ex:
                if now:
                    raise ValidationError("Previous Task exists.") from ex
                else:
                    return

        with self.semaphore_with_delay(not now, ignore_states=[DONE]):
            self.state = STARTED
            self.started = fields.Datetime.now()

            with self.semaphore_with_delay(
                enabled=not now,
            ) as self:
                if self:
                    self._internal_exec(
                        now, ignore_previous_tasks=ignore_previous_tasks
                    )

    @api.model
    def _cron_cleanup(self):
        dt = arrow.get().shift(days=-20).strftime("%Y-%m-%d %H:%M:%S")
        self.search([
            ('create_date', '<', dt)
            ]).unlink()

    def requeue(self):
        for rec in self.filtered(lambda x: x.state in [FAILED]):
            qj = rec._semaphore_get_queuejob()
            qj = qj and qj[0]
            if qj and qj.state in [DONE, FAILED]:
                qj.unlink()
                rec._exec(now=False)
            elif qj:
                if self.state != STARTED:
                    self.state = STARTED 
            else: # if not qj
                rec._exec(now=False)

    def _ensure_source_code(self, shell):

        self.branch_id._checkout_latest(
            shell, machine=self.machine_id,
            instance_folder=shell.cwd,
        )

    def _get_args(self, shell):
        self.ensure_one()
        args = {
            'task': self,
            'logsio': shell.logsio,
            'shell': shell,
            }
        if self.kwargs and self.kwargs != 'null':
            args.update(json.loads(self.kwargs))
        self.env.cr.commit()
        return args

    def _internal_exec(
        self, now=False, delete_after=False, ignore_previous_tasks=False
    ):
        # functions called often block the repository access
        args = {}
        log = None
        commit_ids = None
        logsio = None
        if not now and not self.ignore_previous_tasks:
            self._check_previous_tasks()

        try:
            self = self.sudo().with_context(active_test=False)
            short_name = self._get_short_name()
            with self.branch_id.shell(short_name) as shell:
                logsio = shell.logsio
                args = self._get_args(shell)
                if not args.get('no_repo', False):
                    self._ensure_source_code(shell)
                delete_after = args.get('delete_task')
                obj = self.env[self.model].sudo().browse(self.res_id)
                if self.res_id and not obj.exists():
                    raise Exception((
                        f"Not found: {self.res_id} {self.model}"
                    ))

                # mini check if it is a git repository:
                if not args.get('no_repo', False):
                    try:
                        shell.X(["git", "status"])
                    except Exception:
                        pass
                    else:
                        sha = shell.X([
                            "git", "log", "-n1",
                            "--format=%H"])['stdout'].strip()

                        commit_ids = self.branch_id.commit_ids.filtered(
                            lambda x: x.name == sha).ids
                self.env.cr.commit()

                exec('obj.' + self._unblocked('name') + "(**args)", {
                    'obj': obj,
                    'args': args
                    })
                if shell.logsio:
                    shell.logsio.info("Finished!")

        except Exception:
            self.env.cr.rollback()
            self.env.clear()
            logsio_lines = logsio.get_lines() if logsio else ""
            log = traceback.format_exc() + \
                '\n' + '\n'.join(logsio_lines)
            state = 'failed'
        else:
            state = 'done'
            log = ""
            if logsio:
                log = '\n'.join(logsio.get_lines())

        duration = 0
        if self.started:
            duration = (arrow.utcnow() - arrow.get(self.started)) \
                .total_seconds()

        self.with_delay()._finish_task(
            state=state,
            duration=duration,
            delete_after=delete_after,
            log=log,
            commit_id=commit_ids and commit_ids[0] or False
        )

    def _finish_task(self, state, duration, delete_after, log, commit_id):

        if delete_after and state == DONE:
            self.unlink()
            return

        self.write({
            'state': state,
            'log': log,
            'duration': duration,
            'commit_id': commit_id,
        })
        if self.branch_id:
            if state == FAILED:
                self.branch_id.message_post(
                    body=f"Error happened {self.name}\n{log[-250:]}")
            elif state == DONE:
                self.branch_id.message_post(
                    body=f"Successfully executed {self.name}")

    @api.model
    def _cron_check_states_vs_queuejobs(self):
        for task in self.search([('state', '=', 'started')]):
            task._compute_state()
            # jobs = self.env['queue.job'].search([
            #     ('identity_key', '=', task.semaphore_qj_identity_key)
            # ])
            # if not jobs:
            #     task.state = FAILED
            #     continue
            # if all(x.state in (FAILED, 'cancel') for x in jobs):
            #     task.state = FAILED
            #     continue

        # think what to do; failed job may be requeued
        # if self.state == 'started':
        #     jobs = self._semaphore.get_queuejob()
        #     if jobs and jobs[0].state in ['done']job.state == 'failed':
        #         job.state = 'done'
