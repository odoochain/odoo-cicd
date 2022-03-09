from odoo import _, api, fields, models, SUPERUSER_ID
import re
from odoo.exceptions import UserError, RedirectWarning, ValidationError

class TicketSystem(models.Model):
    _inherit = 'cicd.ticketsystem'

    jira_username = fields.Char("JIRA Username")
    jira_apitoken = fields.Char("JIRA apitoken")
    jira_state_mapping_ids = fields.One2many(
        'ticketsystem.jira.states', 'ticketsystem_id', string="Mappings")
    ttype = fields.Selection(
        selection_add=[('jira', 'JIRA')], ondelete={'jira': 'cascade'})

    def _map_state(self, odoo_name):
        mapping = self.state_mapping_ids.filtered(lambda x: x.name == odoo_name)
        if mapping:
            return mapping[0].jira_state

    def _get_jira_connection(self):
        self.ensure_one()
        from jira import JIRA
        jira = JIRA(
            server=self.url,
            basic_auth=(self.jira_username, self.jira_apitoken),
        )
        return jira

    def _jira_get_state(self, jira, issue, state):
        for x in jira.transitions(issue):
            if x['name'].lower() == state.lower():
                return x['id']
        raise ValidationError(f"Did not find {state}")

    def _jira_set_state(self, issue, state):
        jira = self._get_jira_connection()
        state = self._map_state(state)
        if state:
            state = self._jira_get_state(jira, issue, state)
            jira.transition_issue(issue, state)

    def _jira_comment(self, issue_name, comment):
        assert isinstance(issue_name, str)
        jira = self._get_jira_connection()
        jira.add_comment(issue_name, comment)