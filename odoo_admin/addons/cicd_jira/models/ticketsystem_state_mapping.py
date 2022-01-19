from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from odoo.addons.cicd.models.consts import STATES

class JiraStateMapping(models.Model):
    _name = 'ticketsystem.jira.states'

    name = fields.Selection(STATES, "Odoo State")
    jira_state = fields.Char("Jira State")
    ticketsystem_id = fields.Many2one('cicd.ticketsystem', string="Ticketsystem", ondelete="cascade")
