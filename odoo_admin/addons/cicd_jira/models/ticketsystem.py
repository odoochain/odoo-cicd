from odoo import _, api, fields, models, SUPERUSER_ID
import re
import pudb;pudb.set_trace()
from odoo.exceptions import UserError, RedirectWarning, ValidationError

class TicketSystem(models.Model):
    _inherit = 'cicd.ticketsystem'

    jira_username = fields.Char("JIRA Username")
    jira_apitoken = fields.Char("JIRA apitoken")

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

    def _jira_set_state(self, issue, new_state_name):
        jira = self._get_jira_connection()
        state = self._jira_get_state(jira, issue, new_state_name)
        jira.transition_issue(issue, state)

    def _jira_comment(self, issue_name, comment):
        assert isinstance(issue_name, str)
        jira = self._get_jira_connection()
        jira.add_comment(issue_name, comment)

    def _compute_url(self, branch):
        if self.ttype != 'jira':
            return

        name = branch.ticket_system_ref or branch.name or ''
        if self.regex and name:
            m = re.match(self.regex, name)
            name = m.groups() and m.groups()[0] or ''
        url = (self.url or '') + name
        return url