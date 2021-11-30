from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
class Release(models.Model):
    _name = 'cicd.release'

    name = fields.Char("Name")
    machine_ids = fields.Many2many('cicd.machine', string="Machines")
    branch_id = fields.Many2one('cicd.git.branch', string="Branch")
    candidate_branch = fields.Many2one('cicd.git.branch', string="Candidate", default="pre_master", required=True)
    branch_ids = fields.Many2one('cicd.git.branch', string="Consolidated Branches")
    commit_ids = fields.Many2many('cicd.git.commit', string="Commits")
    computed_summary = fields.Text("Computed Summary", compute="_compute_summary")
    planned_date = fields.Datetime("Planned Deploy Date", default=lambda self: fields.Datetime.now())
    state = fields.Selection([
        ("new", "New"),
        ("ready", "Ready"),
    ], string="State")
    diff_commit_ids = fields.Many2many('cicd.git.commit', string="New Commits", compute="_compute_diff_commits")
    target_branch = fields.Many2one('cicd.git.branch', "Target Branch", default="master", required=True)
    final_curtains = fields.Datetime("Final Curtains")

    release_type = fields.Selection([
        ('standard', 'Standard'),
        ('hotfix', 'Hotfix'),
    ], default="standard", required=True)
    
    def _compute_summary(self):
        for rec in self:
            summary = []
            for branch in rec.branch_ids.sorted(lambda x: x.date):
                summary.append(f"* {branch.enduser_summary}")
            rec.computed_summary = '\n'.join(summary)

    def _compute_diff_commits(self):
        for rec in self:
            rec.diff_commit_ids = [[6, 0, []]]
