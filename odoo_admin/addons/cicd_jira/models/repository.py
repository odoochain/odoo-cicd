from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from jira import JIRA

class TicketSystem(models.Model):
    _inherit = 'cicd.ticketsystem'

    jira_username = fields.Char("JIRA Username")
    jira_apitoken = fields.Char("JIRA apitoken")

    def _get_jira_connection(self):
        self.ensure_one()
        jira = JIRA(
            server='ticket_system_base_url',
            basic_auth=(self.jira_username, self.jira_apitoken),
        )
        return jira

    def _jira_get_state(self, jira, issue, state):
        for x in jira.transitions(issue):
            if x['name'].lower() == state.lower():
                return x['id']
        raise ValidationError(f"Did not find {state}")

    def _jira_set_state(self, issue, new_state_name):
        jira = self._get_jira_connection()
        state = self._jira_get_state(jira, issue, new_state_name)
        jira.transition_issue(issue, state)

    def _jira_comment(self, issue_name, comment):
        assert isinstance(issue_name, str)
        jira = self._get_jira_connection()
        jira.add_comment(issue_name, comment)