import re
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError


class TicketSystem(models.Model):
    _name = 'cicd.ticketsystem'

    name = fields.Char("Name", required=True)
    ttype = fields.Selection([], string="Type", required=True)
    url = fields.Char("Ticket System Base URL", required=True)
    regex = fields.Char(
        "Regex", default=".*", required=True,
        help="Parsing branch to match ticket in ticketsystem")

    test_branch_id = fields.Many2one('cicd.git.branch', string="Test Branch")
    link_to_test_branch = fields.Char("Link to branch", compute="_compute_link")

    def _extract_ts_part(self, branch):
        name_orig = branch.ticket_system_ref or branch.name or ''
        name = None
        if self.regex and name_orig:
            m = re.match(self.regex, name_orig)
            if m and m.group():
                name = m.group()
            else:
                return False
        return name or name_orig

    def _compute_url(self, branch):
        name = self._extract_ts_part(branch)
        url = (self.url or '') + 'browse/' + (name or "???????")
        return url

    def test_ticketsystem(self):
        self.ensure_one()
        if not self.test_branch_id:
            raise ValidationError("Please choose a branch")
        self.test_branch_id._report_comment_to_ticketsystem("test")

    @api.depends("test_branch_id")
    def _compute_link(self):
        for rec in self:
            rec.link_to_test_branch = rec._compute_url(rec.test_branch_id)