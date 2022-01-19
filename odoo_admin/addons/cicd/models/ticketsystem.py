from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
class TicketSystem(models.Model):
    _name = 'cicd.ticketsystem'

    name = fields.Char("Name")
    ttype = fields.Selection([], string="Type")
    url = fields.Char("Ticket System Base URL")