import traceback
import arrow
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from ..tools.logsio_writer import LogsIOWriter
import logging
logger = logging.getLogger(__name__)
class Release(models.Model):
    _inherit = ['mail.thread']
    _name = 'cicd.release'

    active = fields.Boolean("Active", default=True, store=True)
    name = fields.Char("Name", required=True)
    project_name = fields.Char("Project Name", required=True, help="techincal name - no special characters")
    machine_ids = fields.Many2many('cicd.machine', string="Machines")
    repo_id = fields.Many2one("cicd.git.repo", required=True, string="Repo", store=True)
    branch_id = fields.Many2one('cicd.git.branch', string="Branch", required=True)
    candidate_branch = fields.Char(string="Candidate", required=True, default="master_candidate")
    item_ids = fields.One2many('cicd.release.item', 'release_id', string="Release")
    auto_release = fields.Boolean("Auto Release")
    auto_release_cronjob_id = fields.Many2one('ir.cron', string="Scheduled Release")
    sequence_id = fields.Many2one('ir.sequence', string="Version Sequence", required=True)
    countdown_minutes = fields.Integer("Countdown Minutes")
    is_latest_release_done = fields.Boolean("Latest Release Done", compute="_compute_latest_release_done")
    state = fields.Selection(related='item_ids.state')
    planned_timestamp_after_preparation = fields.Integer("Release after preparation in minutes", default=60)
    action_ids = fields.One2many('cicd.release.action', 'release_id', string="Release Actions")

    def toggle_active(self):
        for rec in self:
            rec.active = not rec.active

    @api.constrains("project_name")
    def _check_project_name(self):
        for rec in self:
            for c in " !?#/\\+:,":
                if c in rec.project_name:
                    raise ValidationError("Invalid Project-Name")

    def make_hotfix(self):
        existing = self.item_ids.filtered(lambda x: x.release_type == 'hotfix' and x.state not in ['done', 'failed'])
        if existing:
            raise ValidationError("Hotfix already exists. Please finish it before")
        self.item_ids = [[0, 0, {
            'release_type': 'hotfix',
        }]]

    def _compute_latest_release_done(self):
        for rec in self:
            items = rec.item_ids.sorted(lambda x: x.create_date, reverse=True)
            if not items:
                rec.is_latest_release_done = False
            else:
                rec.is_latest_release_done = items[0].date_done

    @api.constrains("candidate_branch", "branch_id")
    def _check_branches(self):
        for rec in self:
            for field in [
                'candidate_branch',
                'branch_id',
            ]:
                if not self[field]:
                    continue
                if self.search_count([
                    ('id', '!=', rec.id),
                    (field, '=', rec[field] if isinstance(rec[field], (bool, str)) else rec[field].id),
                ]):
                    raise ValidationError("Branches must be unique per release!")


    @api.recordchange('auto_release')
    def _onchange_autorelease(self):
        for rec in self:
            if not rec.auto_release and rec.auto_release_cronjob_id:
                rec.auto_release_cronjob_id.sudo().unlink()
            elif rec.auto_release and not rec.auto_release_cronjob_id:
                rec._make_cronjob()

    def _make_cronjob(self):
        models = self.env['ir.model'].search([('model', '=', self._name)])
        self.auto_release_cronjob_id = self.env['ir.cron'].create({
            'name': self.name + " scheduled release",
            'model_id': models.id,
            'code': f'model.browse({self.id})._cron_prepare_release()'
        })

    def _cron_prepare_release(self):
        self.ensure_one()
        new_items = self.item_ids.filtered(lambda x: x.state == 'new')
        final_curtain_dt = arrow.get().shift(minutes=self.countdown_minutes).strftime("%Y-%m-%d %H:%M:%S")
        if not new_items:
            new_items = self.item_ids.create({
                'release_id': self.id,
                'release_type': 'standard',
                'final_curtain': final_curtain_dt,
                'planned_date': arrow.get().shift(minutes=self.planned_timestamp_after_preparation).strftime("%Y-%m-%d %H:%M:%S"),
            })
        
        # check branches to put on the release
        branches = self.env[new_items.branch_ids._name]
        for branch in self.repo_id.branch_ids:
            if branch.state == 'candidate':
                branches |= branch
        new_items.branch_ids = [[6, 0, branches.ids]]

        # if release did not happen or so, then update final curtain:
        new_items.final_curtain = final_curtain_dt

    def _get_logsio(self):
        logsio = LogsIOWriter(self.repo_id.short, "Release")
        return logsio

    def _ensure_item(self):
        items = self.item_ids.sorted(lambda x: x.id, reverse=True).filtered(lambda x: x. release_type == 'standard')
        if not items or items[0].state in ['done', 'failed']:
            items = self.item_ids.create({
                'release_id': self.id,
            })
        else:
            items = items[0]
        return items

    def do_release(self):
        for rec in self:
            item = rec.item_ids.filtered(lambda x: x.state in ('new', 'failed')).sorted(lambda x: x.id)
            if not item:
                continue
            item = item[0]
            if item.planned_date > fields.Datetime.now():
                continue

            item._trigger_do_release()

    def collect_tested_branches(self):
        for rec in self:
            rec.item_ids.filtered(lambda x: x.state in ('new', 'failed'))._collect_tested_branches()