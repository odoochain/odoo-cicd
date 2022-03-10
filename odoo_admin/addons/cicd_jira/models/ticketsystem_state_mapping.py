from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from odoo.addons.cicd.models.consts import STATES

class JiraStateMapping(models.Model):
    _name = 'ticketsystem.jira.states'

    name = fields.Selection(STATES, "Odoo State")
    jira_state = fields.Char("Jira State")
    ticketsystem_id = fields.Many2one('cicd.ticketsystem', string="Ticketsystem", ondelete="cascade")

    def map(self, ticketsystem, cicd_state):
        state = ticketsystem.jira_state_mapping_ids.filtered(
            lambda x: x.name == cicd_state)
        if not state:
            raise NotImplementedError(cicd_state)
        return state[0].name