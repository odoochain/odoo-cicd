from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
class TicketSystem(models.Model):
    _name = 'cicd.ticketsystem'

    name = fields.Char("Name", required=True)
    ttype = fields.Selection([], string="Type", required=True)
    url = fields.Char("Ticket System Base URL", required=True)
    regex = fields.Char("Regex", default=".*", required=True, help="Parsing branch to match ticket in ticketsystem")

    def _compute_url(self, branch):
        return False