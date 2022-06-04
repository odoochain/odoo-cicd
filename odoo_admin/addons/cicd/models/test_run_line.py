import re
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
from .test_run import AbortException


class CicdTestRunLine(models.AbstractModel):
    _inherit = 'cicd.open.window.mixin'
    _name = 'cicd.test.run.line'
    _order = 'started desc'

    run_id = fields.Many2one('cicd.test.run', string="Run")
    exc_info = fields.Text("Exception Info")
    queuejob_id = fields.Many2one("queue.job", string="Queuejob")
    machine_id = fields.Many2one(
        'cicd.machine', string="Machine", required=True)
    duration = fields.Integer("Duration")
    state = fields.Selection([
        ('open', 'Open'),
        ('success', 'Success'),
        ('failed', 'Failed'),
    ], default='open', required=True)
    force_success = fields.Boolean("Force Success")
    try_count = fields.Integer("Try Count")
    name = fields.Char("Name")
    name_short = fields.Char(compute="_compute_name_short")
    test_setting_id = fields.Reference([
        ("cicd.test.settings.unittest", "Unit Test"),
        ("cicd.test.settings.robottest", "Robot Test"),
        ("cicd.test.settings.migration", "Migration Test"),
    ], string="Initiating Testsetting")
    hash = fields.Char("Hash", help="For using")
    reused = fields.Boolean("Reused", readonly=True)
    started = fields.Datetime(
        "Started", default=lambda self: fields.Datetime.now())
    project_name = fields.Char("Project Name Used (for cleaning)")
    effective_machine = fields.Many2one(
        "cicd.machine", compute="_compute_machine")

    def _compute_machine(self):
        for rec in self:
            rec.effective_machine_id = \
                rec.machine_id or rec.run_id.branch_id.repo_id.machine_id

    @contextmanager
    def _shell(self, quick=False):
        assert self.env.context.get('testrun')
        with self.effective_machine_id._shell(
            cwd=self._get_source_path(),
            project_name=self.branch_id.project_name,
        ) as shell:
            if not quick:
                self._ensure_source_and_machines(shell)
            yield shell


    def open_queuejob(self):
        return {
            'view_type': 'form',
            'res_model': self.queuejob_id._name,
            'res_id': self.queuejob_id.id,
            'views': [(False, 'form')],
            'type': 'ir.actions.act_window',
            'target': 'current',
        }

    def _compute_name_short(self):
        for rec in self:
            MAX = 80
            if len(rec.name or '') > MAX:
                rec.name_short = f"{rec.name[:MAX]}..."
            else:
                rec.name_short = rec.name

    def toggle_force_success(self):
        self.sudo().force_success = not self.sudo().force_success

    @api.recordchange('force_success')
    def _onchange_force(self):
        for rec in self:
            if rec.run_id.state not in ['running']:
                rec.run_id._compute_success_rate()

    def robot_results(self):
        return {
            'type': 'ir.actions.act_url',
            'url': f'/robot_output/{self.id}',
            'target': 'new'
        }

    def check_if_test_already_succeeded(self):
        """
        Compares the hash of the module with an existing
        previous run with same hash.
        """
        import pudb;pudb.set_trace()
        res = self.search([
            ('run_id.branch_ids.repo_id', '=', testrun.branch_ids.repo_id.id),
            ('name', '=', name),
            ('hash', '=', hash),
            ('state', '=', 'success'),
        ], limit=1, order='id desc')
        if not res:
            return False

        self.create({
            'run_id': testrun.id,
            'state': 'success',
            'name': name,
            'hash': hash,
            'ttype': res.ttype,
            'reused': True,
        })

        return True

    @api.constrains("ttype")
    def _be_graceful(self):
        for rec in self:
            if rec.ttype != 'error':
                continue

            exc_info = rec.exc_info or ''
            name = rec.name or ''

            grace = [
                '.git/index.lock',
                'the database system is starting up',

            ]
            for vol in self.env['cicd.machine.volume'].search([]):
                grace += [f"{vol.name}.*No such file or directory"]
                grace += [f"No such file or directory.*{vol.name}"]
            for grace in grace:
                for line in (exc_info + name).splitlines():
                    if re.findall(grace, line):
                        rec.ttype = 'log'
                        break

    @api.model
    def create(self, vals):
        res = super().create(vals)
        res._be_graceful()
        return res

    def ok(self):
        return {'type': 'ir.actions.act_window_close'}

    def execute(self):
        breakpoint()  #Fix params:
        if self.check_if_test_already_succeeded(self):
            trycounter = 0
            while trycounter < self.try_count:
                if self.run_id.do_abort:
                    raise AbortException("Aborted by user")
                trycounter += 1

                shell.logsio.info(f"Try #{trycounter}")

                self.started = arrow.get()
                # data = {
                #     'position': position,
                #     'name': name,
                #     'ttype': ttype,
                #     'run_id': self.id,
                #     'started': started.datetime.strftime("%Y-%m-%d %H:%M:%S"),
                #     'try_count': trycounter,
                #     'hash': hash,
                #     'odoo_module': odoo_module or False,
                # }
                try:
                    shell.logsio.info(f"Running {name}")
                    result = execute_run(item)
                    if result:
                        data.update(result)

                except Exception:  # pylint: disable=broad-except
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
            self.env.cr.commit()