import arrow
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from ..tools.logsio_writer import LogsIOWriter
class Release(models.Model):
    _inherit = ['mail.thread']
    _name = 'cicd.release'

    name = fields.Char("Name", required=True)
    project_name = fields.Char("Project Name", required=True, help="techincal name - no special characters")
    machine_ids = fields.Many2many('cicd.machine', string="Machines")
    repo_id = fields.Many2one("cicd.git.repo", required=True, string="Repo", store=True)
    branch_id = fields.Many2one('cicd.git.branch', string="Branch", required=True)
    candidate_branch_id = fields.Many2one('cicd.git.branch', string="Candidate", required=True)
    item_ids = fields.One2many('cicd.release.item', 'release_id', string="Release")
    auto_release = fields.Boolean("Auto Release")
    auto_release_cronjob_id = fields.Many2one('ir.cron', string="Scheduled Release")
    sequence_id = fields.Many2one('ir.sequence', string="Version Sequence", required=True)
    countdown_minutes = fields.Integer("Countdown Minutes")
    is_latest_release_done = fields.Boolean("Latest Release Done", compute="_compute_latest_release_done")
    state = fields.Selection(related='item_ids.state')
    planned_timestamp_after_preparation = fields.Integer("Release after preparation in minutes", default=60)

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

    @api.constrains("candidate_branch_id", "branch_id")
    def _check_branches(self):
        for rec in self:
            for field in [
                'candidate_branch_id',
                'branch_id',
            ]:
                import pudb;pudb.set_trace()
                if not self[field]:
                    continue
                if self.search_count([
                    ('id', '!=', rec.id),
                    (field, '=', rec[field].id),
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
        final_curtain_dt = arrow.get().shift(minutes=self.countdown_minutes).datetime,
        if not new_items:
            new_items = self.item_ids.create({
                'release_id': self.id,
                'release_type': 'standard',
                'final_curtain': final_curtain_dt,
                'planned_date': arrow.get().shift(minutes=self.planned_timestamp_after_preparation),
            })
        
        # check branches to put on the release
        branches = self.env[new_items.branch_ids]
        for branch in self.repo_id.branch_ids:
            if branch.state == 'candidate':
                branches |= branch
        new_items.branch_ids = [[6, 0, branches.ids]]

        # if release did not happen or so, then update final curtain:
        new_items.final_curtain = final_curtain_dt

    def _get_logsio(self):
        logsio = LogsIOWriter(self.repo_id.short, "Release")
        return logsio

    def collect_branches_on_candidate(self):
        logsio = self._get_logsio()
        item = self._ensure_item()
        self.repo_id._collect_branches(
            source_branches=item.branch_ids,
            target_branch=self.candidate_branch_id,
            logsio=logsio,
        )

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
        self.ensure_one()
        logsio = self._get_logsio()
        item = self.item_ids.filtered(lambda x: x.state == 'new')
        if not item:
            return
        if item.planned_date > arrow.get().datetime:
            return

        item._do_release()

class ReleaseItem(models.Model):
    _name = 'cicd.release.item'
    _order = 'id desc'

    name = fields.Char("Version")
    release_id = fields.Many2one('cicd.release', string="Release")
    planned_date = fields.Datetime("Planned Deploy Date", default=lambda self: fields.Datetime.now())
    done_date = fields.Datetime("Done")
    changed_lines = fields.Integer("Changed Lines")
    final_curtain = fields.Datetime("Final Curtains")
    log_release = fields.Text("Log")

    state = fields.Selection([
        ("new", "New"),
        ("ready", "Ready"),
        ('done', 'Done'),
        ('failed', 'Failed'),
    ], string="State", state='new')
    computed_summary = fields.Text("Computed Summary", compute="_compute_summary")
    commit_ids = fields.Many2many('cicd.git.commit', string="Commits", help="Commits that are released.")
    branch_ids = fields.Many2one('cicd.git.branch', string="Merged Branches")

    release_type = fields.Selection([
        ('standard', 'Standard'),
        ('hotfix', 'Hotfix'),
    ], default="standard", required=True, readonly=True)


    def on_done(self):
        if not self.changed_lines:
            msg = "Nothing new to deploy"
        msg = '\n'.join(filter(bool, self.mapped('commit_ids.branch_ids.enduser_summary')))
        self.release_id.message_post(body=msg)
        self.done_date = fields.Datetime.now()
    
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

    def _do_release(self):
        self.ensure_item()
        logsio = self.release_id._get_logs()
        try:
            import pudb;pudb.set_trace()
            for machine in self.release_id.machine_ids:
                res = self.repo_id._merge(
                    self.release_id.candidate_branch_id,
                    self.release_id.branch_id,
                )
                if not res.diffs_exists:
                    self._on_done()
                    continue

                main_repo_path = self.release_id.repo_id._get_main_repo(tempfolder=True)
                with self.release_id.repo_id.machine_id._shell_exec(cwd=main_repo_path, logsio=logsio) as shell:
                    try:
                        shell.X("git", "checkout", "-f", self.release_id.branch_id.name)
                        shell.X("git", "tag", "-f", self.name)
                        shell.X("git", "push", "--follow-tags")
                    finally:
                        shell.X("rm", "-Rf", main_repo_path)

                path = machine._get_volume("source") / self.release_id.project_name
                self.repo_id._get_main_repo(destination_folder=path, machine=machine)
                with machine._shell_exec(cwd=path, logsio=logsio) as shell:
                    shell.X("odoo", "reload")
                    shell.X("odoo", "build")
                    shell.X("odoo", "update")
            self.release_id.message_post(body=f"Deployment of version {self.version} succeeded!")
        except Exception as ex:
            self.release_id.message_post(body=f"Deployment of version {self.version} failed: {ex}")

        self.log = logsio.get_lines()