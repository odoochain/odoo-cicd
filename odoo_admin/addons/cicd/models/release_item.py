import traceback
import arrow
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from ..tools.logsio_writer import LogsIOWriter
import logging
logger = logging.getLogger(__name__)


class ReleaseItem(models.Model):
    _inherit = ['mail.thread']
    _name = 'cicd.release.item'
    _order = 'id desc'

    name = fields.Char("Version")
    release_id = fields.Many2one('cicd.release', string="Release")
    planned_date = fields.Datetime("Planned Deploy Date", default=lambda self: fields.Datetime.now(), track_visibility="onchange")
    done_date = fields.Datetime("Done", track_visibility="onchange")
    changed_lines = fields.Integer("Changed Lines", track_visibility="onchange")
    final_curtain = fields.Datetime("Final Curtains", track_visibility="onchange")
    log_release = fields.Text("Log")
    state = fields.Selection([
        ("new", "New"),
        ('done', 'Done'),
        ('failed', 'Failed'),
        ('ignore', 'Ignore'),
    ], string="State", default='new', required=True, track_visibility="onchange")
    computed_summary = fields.Text("Computed Summary", compute="_compute_summary", track_visibility="onchange")
    commit_ids = fields.Many2many('cicd.git.commit', string="Commits", help="Commits that are released.", track_visibility="onchange")
    branch_ids = fields.Many2many('cicd.git.branch', string="Branches", track_visibility="onchange")
    queuejob_ids = fields.Many2many('queue.job', string="Queuejobs")
    count_failed_queuejobs = fields.Integer("Failed Jobs", compute="_compute_failed_jobs")
    try_counter = fields.Integer("Try Counter", track_visibility="onchange")

    release_type = fields.Selection([
        ('standard', 'Standard'),
        ('hotfix', 'Hotfix'),
    ], default="standard", required=True, readonly=True)

    def open_window(self):
        self.ensure_one()
        return {
            'view_type': 'form',
            'res_model': self._name,
            'res_id': self.id,
            'views': [(False, 'form')],
            'type': 'ir.actions.act_window',
            'target': 'current',
        }

    def _on_done(self):
        if not self.changed_lines:
            msg = "Nothing new to deploy"
        self.release_id.message_post(body=self.computed_summary)
        self.done_date = fields.Datetime.now()
        self.release_id.message_post(body=f"Deployment of version {self.name} succeeded!")
        self.state = 'done'

    @api.depends('queuejob_ids')
    def _compute_failed_jobs(self):
        for rec in self:
            rec.count_failed_queuejobs = len(rec.queuejob_ids.filtered(lambda x: x.state == 'failed'))
    
    @api.model
    def create(self, vals):
        release = self.env['cicd.release'].browse(vals['release_id'])
        vals['name'] = release.sequence_id.next_by_id()
        res = super().create(vals)
        return res

    def _compute_summary(self):
        for rec in self:
            summary = []
            for branch in rec.branch_ids.sorted(lambda x: x.date):
                summary.append(f"* {branch.enduser_summary}")
            rec.computed_summary = '\n'.join(summary)

    def _trigger_do_release(self):
        for rec in self:
            job = rec.with_delay(
                identity_key=f"release {rec.release_id.name}",
            )._do_release()
            rec.queuejob_ids |= self.env['queue.job'].sudo().search([('uuid', '=', job.uuid)])

    def _do_release(self):
        if self.state != 'new':
            raise ValidationError("Needs state new to be validated.")
        if self.release_type == 'hotfix' and not self.branch_ids:
            raise ValidationError("Hotfix requires explicit branches.")
        logsio = self.release_id._get_logsio()
        try:
            self.try_counter += 1
            release = self.release_id
            changed_lines = release.repo_id._merge(
                release.candidate_branch_id,
                release.branch_id,
                set_tags=[f'release-{self.name}'],
                logsio=logsio,
            )
            self.changed_lines += changed_lines

            if not self.changed_lines:
                self._on_done()
                return

            for machine in self.release_id.machine_ids:
                path = machine._get_volume("source") / release.project_name
                release.repo_id._get_main_repo(destination_folder=path, machine=machine)
                with machine._shellexec(cwd=path, logsio=logsio, project_name=release.project_name) as shell:
                    shell.odoo("reload")
                    shell.odoo("build")
                    shell.odoo("update")

            self._on_done()

        except Exception as ex:
            msg = traceback.format_exc()
            self.release_id.message_post(body=f"Deployment of version {self.name} failed: {msg}")
            self.state = 'failed'
            logger.error(msg)

        self.log_release = logsio.get_lines()

    def trigger_collect_branches(self):
        for rec in self:
            job = rec.with_delay(
                identity_key=f"collect_branches {rec.release_id.name}",
            ).collect_branches()
            rec.queuejob_ids |= self.env['queue.job'].sudo().search([('uuid', '=', job.uuid)])

    def collect_branches(self):
        for rec in self:
            repo = rec.release_id.repo_id
            if rec.state not in ['new']:
                continue
            if rec.release_type != 'standard':
                continue

            rec.branch_ids = [[6, 0, self.env['cicd.git.branch'].search([
                ('state', 'in', ['tested']),
                ('id', 'not in', (repo.branch_id | repo.candidate_branch_id).ids),
            ]).ids]]

    @api.constrains("branch_ids")
    def _onchange_branches(self):
        for rec in self:
            if rec.state != 'new':
                raise ValidationError("Branches can only be changed in state 'new'")
            # fetch latest commits:
            logsio = self.release_id._get_logsio()
            repo = rec.release_id.repo_id
            commits = repo._collect_latest_tested_commits(
                source_branches=rec.branch_ids,
                target_branch=rec.release_id.candidate_branch_id,
                logsio=logsio,
                critical_date=rec.final_curtain or arrow.get().datetime,
            )
            rec.commit_ids = [[6, 0, commits.ids]]

    def set_to_ignore(self):
        for rec in self:
            if rec.state not in ['failed', 'new']:
                raise ValidationError("Cannot set state to ignore")
            rec.state = 'ignore'
    
    def reschedule(self):
        for rec in self:
            if rec.state not in ['ignore']:
                raise ValidationError("Cannot set state to new")
            rec.state = 'new'