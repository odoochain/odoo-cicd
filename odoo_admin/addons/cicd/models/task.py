import json
import psycopg2
from odoo.addons.queue_job.exception import RetryableJobError
import traceback
from . import pg_advisory_lock
import os
import arrow
import traceback
from odoo import _, api, fields, models, SUPERUSER_ID, registry
from odoo import registry
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from odoo.addons.queue_job.models.queue_job import STATES
from contextlib import contextmanager
import logging
logger = logging.getLogger('cicd_task')

class Task(models.Model):
    _name = 'cicd.task'
    _order = 'date desc'

    model = fields.Char("Model")
    res_id = fields.Integer("ID")
    display_name = fields.Char(compute="_compute_display_name")
    machine_id = fields.Many2one('cicd.machine', string="Machine", readonly=True)
    branch_id = fields.Many2one('cicd.git.branch', string="Branch")
    name = fields.Char("Name")
    date = fields.Datetime("Date", default=lambda self: fields.Datetime.now(), readonly=True)
    is_done = fields.Boolean(compute="_compute_is_done", store=False)

    state = fields.Selection(selection=STATES, string="State")
    log = fields.Text("Log", readonly=True)
    error = fields.Text("Exception", compute="_compute_state")
    dump_used = fields.Char("Dump used", readonly=True)
    duration = fields.Integer("Duration [s]", readonly=True)
    commit_id = fields.Many2one("cicd.git.commit", string="Commit", readonly=True)
    queuejob_uuid = fields.Char("Queuejob UUID")
    queue_job_id = fields.Many2one('queue.job', compute="_compute_queuejob")

    kwargs = fields.Text("KWargs")
    identity_key = fields.Char()

    def _compute_state(self):
        for rec in self:
            if not rec.queuejob_uuid:
                rec.state = False
                rec.error = False
                continue

            self.env.cr.execute("select state, exc_info from queue_job where uuid=%s", (rec.queuejob_uuid,))
            qj = self.env.cr.fetchone()
            if not qj:
                rec.state = False
                rec.error = False
            else:
                rec.state = qj[0]
                rec.error = qj[1]

    @api.depends('state')
    def _compute_is_done(self):
        for rec in self:
            rec.is_done = rec.state in ['done', 'failed'] if rec.state else True

    def _compute_display_name(self):
        for rec in self:
            name = rec.name
            name = name.replace("obj.", "")
            if name.startswith("_"):
                name = name[1:]
            name = name.split("(")[0]
            rec.display_name = name

    def perform(self, now=False):
        breakpoint()
        self.ensure_one()

        if not now:
            job = self.with_delay(
                identity_key=self._get_identity_key(),
                eta=arrow.get().shift(seconds=10).strftime("%Y-%m-%d %H:%M:%S"),
            )._exec(now)
            self.queuejob_uuid = job.uuid
        else:
            self._exec(now)

    def _get_identity_key(self):
        appendix = f"branch:{self.branch_id.repo_id.short}-{self.branch_id.name}:"
        if self.identity_key:
            return self.identity_key + " " + appendix
        name = self._get_short_name()
        return f"{self.branch_id.project_name}_{name} " + appendix

    def _get_short_name(self):
        name = self.name or ''
        if name.startswith("_"):
            name = name[1:]
        return name

    @contextmanager
    def _new_cursor(self, new_cursor):
        if new_cursor:
            with self.env.registry.cursor() as cr:
                env2 = api.Environment(cr, SUPERUSER_ID, {})
                yield env2
        else:
            yield self.env

    def _exec(self, now=False):
        self = self.sudo()
        args = {}
        short_name = self._get_short_name()
        started = arrow.get()
        # TODO make testruns not block reloading
        self = self.with_context(active_test=False)
        if not self.branch_id:
            breakpoint()
            raise Exception("Branch not given for task.")
        with pg_advisory_lock(self.env.cr, self.branch_id.id, detailinfo=f"taskid: {self.id} - {self.name}"):
            with self._new_cursor(not now) as env2:
                self = env2[self._name].browse(self.id)
                self.state = 'started'
                self.env.cr.commit()

                self = self.with_env(env2)
                with self.branch_id._get_new_logsio_instance(short_name) as logsio:
                    try:
                        dest_folder = self.machine_id._get_volume('source') / self.branch_id.project_name
                        with self.machine_id._shell(cwd=dest_folder, logsio=logsio, project_name=self.branch_id.project_name) as shell:
                            # functions called often block the repository access
                            args = {
                                'task': self,
                                'logsio': logsio,
                                'shell': shell,
                                }
                            if self.kwargs and self.kwargs != 'null':
                                args.update(json.loads(self.kwargs))
                            if not args.get('no_repo', False):
                                self.branch_id.repo_id._get_main_repo(
                                    destination_folder=dest_folder,
                                    machine=self.machine_id,
                                    limit_branch=self.branch_id.name,
                                    )
                            obj = self.env[self.model].sudo().browse(self.res_id)
                            # mini check if it is a git repository:
                            commit = None
                            if not args.get('no_repo', False):
                                try:
                                    shell.X(["git", "status"])
                                except Exception:
                                    pass
                                else:
                                    sha = shell.X(["git", "log", "-n1", "--format=%H"])['stdout'].strip()
                                    commit = self.branch_id.commit_ids.filtered(lambda x: x.name == sha)

                            # if not commit:
                            #     raise ValidationError(f"Commit {sha} not found in branch.")
                            # get current commit
                            try:
                                exec('obj.' + self.name + "(**args)", {'obj': obj, 'args': args})
                            finally:
                                if commit:
                                    self.sudo().commit_id = commit
                                    self.env.cr.commit()

                    except RetryableJobError:
                        raise

                    except Exception:
                        self.env.cr.rollback()
                        msg = traceback.format_exc()
                        log = msg + '\n' + '\n'.join(logsio.get_lines())
                        self.state = 'failed'
                        if self.branch_id:
                            self.branch_id.message_post(f"Error happened {self.name}\n{msg}")
                    else:
                        self.state = 'done'
                        log = '\n'.join(logsio.get_lines())
                        if self.branch_id:
                            self.branch_id.message_post(f"Successfully executed {self.name}")
                    finally:
                        self.env.cr.commit()

                    duration = (arrow.get() - started).total_seconds()
                    if logsio:
                        logsio.info(f"Finished after {duration} seconds!")

                    self.duration = duration
                    self.log = log
                    if args.get("delete_task"):
                        if self.state == 'done':
                            self.unlink()

    @api.model
    def _cron_cleanup(self):
        dt = arrow.get().shift(days=-20).strftime("%Y-%m-%d %H:%M:%S")
        self.search([
            ('create_date', '<', dt)
            ]).unlink()

    def requeue(self):
        for rec in self.filtered(lambda x: x.state in ['failed']):
            rec.queue_job_id.requeue()

    def _compute_queuejob(self):
        for rec in self:
            if rec.queuejob_uuid:
                rec.queue_job_id = self.env['queue.job'].search([('uuid', '=', rec.queuejob_uuid)], limit=1)
            else:
                rec.queue_job_id = False

    def _set_failed_if_no_queuejob(self):
        for task in self:
            if not task.queue_job_id:
                if not task.state or task.state in ['started']:
                    task.state = 'failed'
                continue
            if task.queue_job_id.state in ['failed', 'done']:
                if not task.state or task.state == 'started':
                    task.state = 'failed'