import traceback
from . import pg_advisory_lock
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
    commit_id = fields.Many2one('cicd.git.commit', string="Released commit", help="After merging all tested commits this is the commit that holds all merged commits.")

    release_type = fields.Selection([
        ('standard', 'Standard'),
        ('hotfix', 'Hotfix'),
    ], default="standard", required=True, readonly=True)

    @api.constrains("state")
    def _ensure_one_item_only(self):
        for rec in self:
            if rec.state in ['new', 'failed']:
                if rec.release_id.item_ids.filtered(lambda x: x.release_type == 'standard' and x.id != rec.id and x.state in ['new', 'failed']):
                    raise ValidationError(_("There may only be one new or failed standard item!"))

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
        if not self.release_id.active:
            return
        if self.state not in ['new', 'failed']:
            raise ValidationError("Needs state new/failed to be validated, not: {self.state}")
        if self.release_type == 'hotfix' and not self.branch_ids:
            raise ValidationError("Hotfix requires explicit branches.")
        if not self.commit_id:  # needs a collected commit with everything on it
            return
        if self.commit_id.test_state != 'success':
            return

        pg_advisory_lock(self.env.cr, f"release_{self.release_id.id}")
        logsio = self.release_id._get_logsio()
        try:
            self.try_counter += 1
            release = self.release_id
            repo = self.release_id.repo_id.with_context(active_test=False)
            candidate_branch = repo.branch_ids.filtered(lambda x: x.name == self.candidate_branch)
            candidate_branch.ensure_one()
            if not candidate_branch.active:
                raise UserError(f"Candidate branch '{self.release_id.candidate_branch}' is not active!")
            changed_lines = repo._merge(
                candidate_branch,
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

    def _collect_tested_branches(self):
        for rec in self:
            if rec.state not in ['new', 'failed']:
                continue
            if rec.release_type != 'standard':
                continue
            release = rec.release_id
            ignored_branch_names = (release.candidate_branch, release.branch_id.name)
            branches = self.env['cicd.git.branch'].search([
                ('state', 'in', ['tested']),
                ('block_release', '=', False),
                ('name', '!=', rec.release_id.candidate_branch),
                ('id', 'not in', (rec.release_id.branch_id).ids),
            ]).filtered(lambda x: x.name not in ignored_branch_names)
            for b in branches:
                if b not in rec.branch_ids:
                    rec.branch_ids += b
            for b in rec.branch_ids:
                if b.state not in ['tested', 'candidate', 'done', 'release']:
                    rec.branch_ids -= b
            rec._trigger_recreate_candidate_branch_in_git()

    def _recreate_candidate_branch_in_git(self):
        """
        Heavy function - takes longer and does quite some work.
        """
        self.ensure_one()
        if self.state not in ('new', 'failed', 'ignore'):
            raise ValidationError("Branches can only be changed in state 'new', 'failed' or 'ignore'")

        # fetch latest commits:
        logsio = self.release_id._get_logsio()
        repo = self.release_id.repo_id.with_context(active_test=False)
        # remove blocked 
        self.branch_ids -= self.branch_ids.filtered(lambda x: x.block_release)
        message_commit, commits = repo._collect_latest_tested_commits(
            source_branches=self.branch_ids,
            target_branch_name=self.release_id.candidate_branch,
            logsio=logsio,
            critical_date=self.final_curtain or arrow.get().datetime,
            make_info_commit_msg=
                f"Release Item {self.id}\n"
                f"Includes latest commits from:\n{', '.join(self.mapped('branch_ids.name'))}"
        )
        message_commit.approval_state = 'approved'
        self.commit_ids = [[6, 0, commits.ids]]
        self.commit_id = message_commit
        candidate_branch = repo.branch_ids.filtered(lambda x: x.name == self.release_id.candidate_branch)
        candidate_branch.ensure_one()

        (self.release_id.branch_id | self.branch_ids | candidate_branch)._compute_state()

    def _trigger_recreate_candidate_branch_in_git(self):
        self.ensure_one()
        job = self.with_delay(
            identity_key=f"recreate_candidate_branch_in_git: {self.release_id.name}",
            eta=arrow.get().shift(minutes=1).datetime.strftime("%Y-%m-%d %H:%M:%S"),
        )._recreate_candidate_branch_in_git()
        qj = self.env['queue.job'].sudo().search([('uuid', '=', job.uuid)])
        self.queuejob_ids |= qj

    @api.fieldchange("branch_ids")
    def _on_change_branches(self, changeset):
        for rec in self:
            rec._trigger_recreate_candidate_branch_in_git()
            (changeset['branch_ids']['old'] | changeset['branch_ids']['new'])._compute_state()

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