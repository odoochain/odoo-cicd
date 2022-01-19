from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError

class Branch(models.Model):
    _inherit = 'cicd.git.branch'

    def _get_jira_issue(self):
        self.ensure_one()
        jira = self.repo_id._get_jira_connection()
        issue = jira.issue(self.ticket_system_ref or self.name)
        return issue

    def ticketsystem_set_state(self, state):
        super().ticketsystem_set_state(state)

    def _report_new_state_to_ticketsystem(self):
        super()._report_new_state_to_ticketsystem()
        issue = self._get_jira_issue()
        if self.state in ['done', 
        self.repo_id._jira_set_state(issue, 'done')

    def _report_comment_to_ticketsystem(self):
        super()._report_comment_to_ticketsystem()

