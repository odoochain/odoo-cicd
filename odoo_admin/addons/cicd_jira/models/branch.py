from odoo import _, api, fields, models, SUPERUSER_ID
import json
from odoo.exceptions import UserError, RedirectWarning, ValidationError
import jira as JIRA


class Branch(models.Model):
    _inherit = 'cicd.git.branch'

    jira_json = fields.Text("JSON stored to debug")

    def _get_jira_issue(self, name=None):
        self.ensure_one()
        jira = self.repo_id.ticketsystem_id._get_jira_connection()
        try:
            issue_name = name or self.repo_id.ticketsystem_id._extract_ts_part(self)
            issue = jira.issue(issue_name)
        except JIRA.exceptions.JIRAError as jira_ex:
            if 'Issue does not exist or you do not have permission to see it' in str(jira_ex):
                return None
            raise
        return issue

    def ticketsystem_set_state(self, state):
        super().ticketsystem_set_state(state)

    def _report_new_state_to_ticketsystem(self):
        super()._report_new_state_to_ticketsystem()
        for rec in self:
            if rec.repo_id.ticketsystem_id.ttype == 'jira':
                ts = rec.repo_id.ticketsystem_id
                issue = self._get_jira_issue()
                if issue:
                    state = ts.jira_state_mapping_ids.map(ts, rec.state)
                    ts._jira_set_state(issue, state)

    def _report_comment_to_ticketsystem(self, comment):
        super()._report_comment_to_ticketsystem(comment)
        for rec in self:
            rec._jira_comment(comment)

    def _jira_comment(self, comment):
        for rec in self:
            ts = rec.repo_id.ticketsystem_id.filtered(
                lambda x: x.ttype == 'jira')
            if not ts:
                return
            ts._jira_comment(rec.ticket_system_ref or rec.name, comment)

    def _event_new_test_state(self, new_state):
        super()._event_new_test_state(new_state)
        comment = None
        if new_state == 'success':
            comment = "Tests Succeeded"
        elif new_state == 'failed':
            comment = "Tests failed"
        self._jira_comment(comment)

    def _fetch_ts_data(self):
        for rec in self:
            if rec.repo_id.ticketsystem_id.ttype == 'jira':
                rec._fetch_ts_data_jira()

    def _fetch_ts_data_jira(self):
        issue = self._get_jira_issue()
        if not issue:
            return
        self.jira_json = json.dumps(issue.raw, indent=4)
        enduser_summary_ticketsystem = \
            [issue.raw['fields']['description']]
        self.name_ticketsystem = issue.raw['fields']['summary'] or ''

        it_issue = issue
        while True:
            try:
                if it_issue.raw['fields'].get('parent'):
                    if it_issue.raw['fields']['parent']['fields']['issuetype']['name'] == 'Epic':
                        epic = it_issue.raw['fields']['parent']['fields']['summary']
                        self.epic_id = self.env['cicd.branch.epic'].ensure_exists(epic)
                        break
                    else:
                        it_issue = self._get_jira_issue(it_issue.raw[
                            'fields']['parent']['key'])
            except (IndexError, KeyError):
                epic = False
                break

        try:
            ttype = issue.raw['fields']['issuetype']['name']
        except (IndexError, KeyError):
            ttype = False
        else:
            if ttype:
                self.type_id = self.env['cicd.branch.type'].ensure_exists(
                    ttype)
            else:
                self.type_id = False

        for further_summary_field in (
            self.repo_id.ticketsystem_id.jira_extract_custom_fields or ''
        ).split(','):
            if not further_summary_field:
                continue
            further_summary_field = further_summary_field.strip()
            enduser_summary_ticketsystem.append(
                issue.raw['fields'][further_summary_field])

        self.enduser_summary_ticketsystem = '\n'.join(
            filter(bool, enduser_summary_ticketsystem))
