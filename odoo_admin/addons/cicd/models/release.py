from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from odoo_admin.addons.cicd.tools.logsio_writer import LogsIOWriter
class Release(models.Model):
    _inherit = ['mail.thread']
    _name = 'cicd.release'

    name = fields.Char("Name", required=True)
    machine_ids = fields.Many2many('cicd.machine', string="Machines")
    repo_id = fields.Many2one(related="branch_id.repo_id", string="Repo", store=True)
    branch_id = fields.Many2one('cicd.git.branch', string="Branch", required=True)
    candidate_branch_id = fields.Many2one('cicd.git.branch', string="Candidate", required=True)
    item_ids = fields.One2many('cicd.release.item', 'release_id', string="Release")
    auto_release = fields.Boolean("Auto Release")
    auto_release_cronjob_id = fields.Many2one('ir.cron', string="Scheduled Release")
    sequence_id = fields.Many2one('ir.sequence', string="Version Sequence", required=True)
    countdown_minutes = fields.Integer("Countdown Minutes")

    @api.constrains("candidate_branch_id", "branch_id")
    def _check_branches(self):
        for rec in self:
            for field in [
                'candidate_branch_id',
                'branch_id',
            ]:
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
        if self.item_ids.filtered(lambda x: x.state == 'new'):
            return
        self.item_ids = [[0, 0, {
            'release_type': 'standard',
        }]]

class ReleaseItem(models.Model):
    _name = 'cicd.release.item'
    _order = 'id desc'

    name = fields.Char("Version")
    release_id = fields.Many2one('cicd.release', string="Release")
    planned_date = fields.Datetime("Planned Deploy Date", default=lambda self: fields.Datetime.now())
    done_date = fields.Datetime("Done")
    final_curtain = fields.Datetime("Final Curtains")

    diff_commit_ids = fields.Many2many('cicd.git.commit', string="New Commits", compute="_compute_diff_commits", help="Commits that are new since the last release")
    state = fields.Selection([
        ("new", "New"),
        ("ready", "Ready"),
    ], string="State")
    computed_summary = fields.Text("Computed Summary", compute="_compute_summary")
    commit_ids = fields.Many2many('cicd.git.commit', string="Commits", help="Commits that are released.")
    branch_ids = fields.Many2one('cicd.git.branch', string="Merged Branches")

    release_type = fields.Selection([
        ('standard', 'Standard'),
        ('hotfix', 'Hotfix'),
    ], default="standard", required=True, readonly=True)

    def do_release(self):
        self.ensure_one()
        logsio = LogsIOWriter(repod.short, "Release")

        self._select_latest_commits(logsio=logsio)
        if not self.commit_ids:
            return
    
    def collect_branches_on_candidate(self, logsio):
        """
        Iterate all branches and get the latest commit that fall into the countdown criteria.
        """
        self.ensure_one()

        # we use a working repo
        repo = self.release_id.repo_id
        machine = repo.machine_id
        repo_path = self.release_id.repo_id._get_main_repo(tempfolder=True)
        with repo.machine_id._shellexec(cwd=repo_path, logsio=logsio, env=env) as shell:
            try:
                env = repo._get_git_non_interactive()

                # clear the current candidate
                shell.X(["/usr/bin/git", "branch", "-D", repo.candidate_ checkout", "-f", branch.name])


                for branch in self.branch_ids:
                    for commit in branch.commit_ids.sorted(lambda x: x.date, reverse=True):
                        if self.final_curtain:
                            if commit.date > self.final_curtain:
                                continue

                        if not commit.force_approved and (commit.test_state != 'successful' or commit.approval_state != 'approved'):
                            continue

                        self.commit_ids = [[4, commit.id]]

                        # we use git functions to retrieve deltas, git sorting and so;
                        # we want to rely on stand behaviour git.
                        shell.X(["/usr/bin/git", "checkout", "-f", branch.name])
                        commits = shell.X(["/usr/bin/git", "log", "--pretty=format:%H", f"..{commit.name}"]).output.strip().split("\n")
                        for commit in commits:
                            # if 

            finally:
                shell.X(["rm", "-Rf", repo_path])

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

    def _compute_diff_commits(self):
        for rec in self:
            previous_release = self.release_id.item_ids.filtered(
                lambda x: x.id < rec.id).sorted(
                    lambda x: x.id, reverse=True)
            if not previous_release:
                rec.diff_commit_ids = [[6, 0, []]]
            else:
                rec.diff_commit_ids = [[6, 0, (rec.commit_ids - previous_release[0].commit_ids).ids]]
