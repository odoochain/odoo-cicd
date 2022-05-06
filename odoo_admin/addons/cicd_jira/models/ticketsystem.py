from odoo import _, api, fields, models, SUPERUSER_ID
import re
from odoo.exceptions import UserError, RedirectWarning, ValidationError
import jira as JIRA

class TicketSystem(models.Model):
    _inherit = 'cicd.ticketsystem'

    jira_update_state = fields.Boolean("Update State")
    jira_username = fields.Char("JIRA Username")
    jira_apitoken = fields.Char("JIRA apitoken")
    jira_state_mapping_ids = fields.One2many(
        'ticketsystem.jira.states', 'ticketsystem_id', string="Mappings")
    ttype = fields.Selection(
        selection_add=[('jira', 'JIRA')], ondelete={'jira': 'cascade'})

    jira_extract_custom_fields = fields.Char(
        "Comma separated list of fields to extract")

    def _jira_resolve_user(self, displayname):
        return self.env['res.users'].smart_find(displayname)

    def _map_state(self, odoo_name):
        mapping = self.jira_state_mapping_ids.filtered(
            lambda x: x.name == odoo_name)
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
        all_transitions = [x['name'].lower() for x in jira.transitions(issue)]
        for x in jira.transitions(issue):
            if x['name'].lower() == state.lower():
                return x['id']
        raise ValidationError((
            f"Did not find '{state}' -"
            f"all values {all_transitions}"))

    def _jira_set_state(self, issue, state):
        jira = self._get_jira_connection()
        state = self._map_state(state)
        if state:
            state = self._jira_get_state(jira, issue, state)
            jira.transition_issue(issue, state)

    def _jira_comment(self, issue_name, comment):
        assert isinstance(issue_name, str)
        jira = self._get_jira_connection()
        try:
            jira.add_comment(issue_name, comment)
        except JIRA.exceptions.JIRAError as jira_ex:
            if jira_ex.status_code != 404:
                raise