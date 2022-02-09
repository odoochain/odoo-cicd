import re
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
class TicketSystem(models.Model):
    _name = 'cicd.ticketsystem'

    name = fields.Char("Name", required=True)
    ttype = fields.Selection([], string="Type", required=True)
    url = fields.Char("Ticket System Base URL", required=True)
    regex = fields.Char("Regex", default=".*", required=True, help="Parsing branch to match ticket in ticketsystem")

    def _extract_ts_part(self, branch):
        name_orig = branch.ticket_system_ref or branch.name or ''
        if self.regex and name_orig:
            m = re.match(self.regex, name_orig)
            if m and m.group():
                name = m.group()
            else:
                return False
        return name or name_orig

    def _compute_url(self, branch):
        name = self._extract_ts_part(branch)
        url = (self.url or '') + 'browse/' + name
        return url