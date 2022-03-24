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


class Task(models.Model):
    _inherit = ['mixin.queuejob.semaphore', 'cicd.mixin.extra_env']
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

    def _compute_state(self):
        for rec in self:
            qj = rec._semaphore_get_queuejob()
            if not qj:
                # keep last state as queuejobs are deleted from time to time
                pass
            else:
                if 'done' in qj.mapped('state'):
                    rec.state = 'done'
                elif 'failed' in qj.mapped('state'):
                    rec.state = 'failed'
                elif qj:
                    rec.state = 'started'
                else:
                    rec.state = False
                if qj:
                    qj = qj.sorted(lambda x: x.id, reverse=True)[0]
                    rec.log = qj.exc_info

    @api.depends('state')
    def _compute_is_done(self):
        for rec in self:
            rec.is_done = rec.state in ['done', 'failed'] \
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

    def perform(self, now=False):
        self.ensure_one()
        self._exec(now)

    @property
    def semaphore_qj_identity_key(self):
        appendix = \
            f"branch:{self.branch_id.repo_id.short}-{self.branch_id.name}:"

        if self.identity_key:
            return self.identity_key + " " + appendix
        name = self._get_short_name()
        with self._extra_env() as self2:
            project_name = self2.branch_id.project_name

        return f"{project_name}_{name} " + appendix

    def _get_short_name(self):
        name = self.name or ''
        if name.startswith("_"):
            name = name[1:]
        return name

    def _exec(self, now=False):
        if not self.branch_id:
            raise Exception("Branch not given for task.")

        with self.semaphore_with_delay(not now, ignore_states=['done']):
            self.state = 'started'
            self.started = fields.Datetime.now()

            with self.semaphore_with_delay(
                enabled=not now,
            ) as self:
                if self:
                    self._internal_exec(now)

    @api.model
    def _cron_cleanup(self):
        dt = arrow.get().shift(days=-20).strftime("%Y-%m-%d %H:%M:%S")
        self.search([
            ('create_date', '<', dt)
            ]).unlink()

    def requeue(self):
        for rec in self.filtered(lambda x: x.state in ['failed']):
            qj = rec._get_queuejob()
            if qj and qj.state in ['done', 'failed']:
                qj.unlink()
                rec._exec(now=False)
            elif qj:
                if self.state != 'started':
                    self.state = 'started'
            else: # if not qj
                rec._exec(now=False)

    def _set_failed_if_no_queuejob(self):
        return
        for task in self:
            task._compute_state()
            if task.state == 'started':
                qj = task._semaphore_get_queuejob()
                if not qj or all([x.state in ['failed'] for x in qj]):
                    task.state = 'failed'

    def _get_args(self, shell):
        self.ensure_one()
        args = {
            'task': self,
            'logsio': shell.logsio,
            'shell': shell,
            }
        if self.kwargs and self.kwargs != 'null':
            args.update(json.loads(self.kwargs))
        if not args.get('no_repo', False):
            self.branch_id.repo_id._get_main_repo(
                destination_folder=shell.cwd,
                machine=self.machine_id,
                limit_branch=self.branch_id.name,
                )
        self.env['base'].flush()
        self.env.cr.commit()
        return args

    def _internal_exec(self, now=False, delete_after=False):
        # functions called often block the repository access
        args = {}
        log = None
        commit = None
        logsio = None
        with self._extra_env() as check:
            previous = check.branch_id.task_ids.filtered(lambda x: x.id < check.id)
            if any(x in [False, 'started'] for x in previous.mapped('state')):
                raise RetryableJobError(
                    "Previous tasks exist.", ignore_retry=True, seconds=30)
        try:
            self = self.sudo().with_context(active_test=False)
            short_name = self._get_short_name()
            with self.branch_id.shell(short_name) as shell:
                logsio = shell.logsio
                self.env['base'].flush()
                self.env.cr.commit()
                args = self._get_args(shell)
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
                        commit = self.branch_id.commit_ids.filtered(
                            lambda x: x.name == sha)

                exec('obj.' + self.name + "(**args)", {
                    'obj': obj,
                    'args': args
                    })
                if shell.logsio:
                    shell.logsio.info(f"Finished!")

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

        with self.semaphore_with_delay(
            enabled=not now,
            appendix='finish',
        ) as self:
            if self:
                self._finish_task(
                    state=state,
                    duration=duration,
                    delete_after=delete_after,
                    log=log,
                    commit_id=commit and commit.id or False,
                )

    def _finish_task(self, state, duration, delete_after, log, commit_id):

        if delete_after and state == 'done':
            self.unlink()
            return

        self.write({
            'state': state,
            'log': log,
            'duration': duration,
            'commit_id': commit_id,
        })
        if self.branch_id:
            if state == 'failed':
                self.branch_id.message_post(
                    body=f"Error happened {self.name}\n{log[-250:]}")
            elif state == 'done':
                self.branch_id.message_post(
                    body=f"Successfully executed {self.name}")
        self.env['base'].flush()
        self.env.cr.commit()

    # TODO .....mmhhh
    def confirm_read_and_ignore(self):
        if self.state == 'failed':
            job = self._semaphore.get_queuejob()
            if job.state == 'failed':
                job.state = 'done'