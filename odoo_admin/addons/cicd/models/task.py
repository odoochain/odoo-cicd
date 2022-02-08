import json
import psycopg2
from odoo.addons.queue_job.exception import RetryableJobError
import traceback
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

    state = fields.Selection(selection=STATES, compute="_compute_state", string="State", store=False)
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

    def _exec(self, now=False):
        self = self.sudo()
        short_name = self._get_short_name()
        started = arrow.get()
        # TODO unittest soll nicht reload auf branch blockieren
        try:
            self.env.cr.execute("select id, name from cicd_task where branch_id=%s for update nowait", (self.branch_id.id,))
        except psycopg2.errors.LockNotAvailable:
            raise RetryableJobError(f"Could not work exclusivley on branch {self.branch_id.name} - retrying in few seconds", ignore_retry=True, seconds=5)

        with self.branch_id._get_new_logsio_instance(short_name) as logsio:
            try:
                dest_folder = self.machine_id._get_volume('source') / self.branch_id.project_name
                with self.machine_id._shell(cwd=dest_folder, logsio=logsio, project_name=self.branch_id.project_name) as shell:
                    self.branch_id.repo_id._get_main_repo(
                        destination_folder=dest_folder,
                        machine=self.machine_id,
                        limit_branch=self.branch_id.name,
                        )
                    obj = self.env[self.model].sudo().browse(self.res_id)
                    # mini check if it is a git repository:
                    try:
                        shell.X(["git", "status"])
                    except Exception:
                        msg = traceback.format_exc()
                        raise Exception(f"Directory seems to be not a valid git directory: {dest_folder}\n{msg}")

                    sha = shell.X(["git", "log", "-n1", "--format=%H"])['stdout'].strip()
                    commit = self.branch_id.commit_ids.filtered(lambda x: x.name == sha)

                    # if not commit:
                    #     raise ValidationError(f"Commit {sha} not found in branch.")
                    # get current commit
                    args = {
                        'task': self,
                        'logsio': logsio,
                        'shell': shell,
                        }
                    if self.kwargs and self.kwargs != 'null':
                        args.update(json.loads(self.kwargs))
                    exec('obj.' + self.name + "(**args)", {'obj': obj, 'args': args})
                    self.sudo().commit_id = commit

            except RetryableJobError:
                raise

            except Exception:
                msg = traceback.format_exc()
                log = '\n'.join(logsio.get_lines())

                raise Exception(f"{msg}\n\n{log}")

            self.log = '\n'.join(logsio.get_lines())

            duration = (arrow.get() - started).total_seconds()
            self.duration = duration
            if logsio:
                logsio.info(f"Finished after {duration} seconds!")

    @api.model
    def _cron_cleanup(self):
        dt = arrow.get().shift(days=-10).strftime("%Y-%m-%d %H:%M:%S")
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