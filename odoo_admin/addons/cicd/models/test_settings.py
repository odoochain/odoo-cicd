from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError


class TestSettings(models.AbstractModel):
    _name = 'cicd.test.settings'

    run_unittests = fields.Boolean(
        "Run Unittests", default=True, testrun_field=True, testrun_apply=True)
    run_robottests = fields.Boolean(
        "Run Robot-Tests", default=True, testrun_field=True,
        testrun_apply=True)
    simulate_install_id = fields.Many2one(
        "cicd.dump", string="Simulate Install", testrun_field=True,
        testrun_apply=True)
    retry_unit_tests = fields.Integer(
        "Retry Unittests", default=3, testrun_apply=True)
    timeout_tests = fields.Integer(
        "Timeout Tests [s]", default=600, testrun_apply=True)
    timeout_migration = fields.Integer(
        "Timeout Migration [s]", default=1800, testrun_apply=True)
    any_testing = fields.Boolean(compute="_compute_any_testing")

    def apply_test_settings(self, victim):
        for fieldname, field in self._fields.items():
            if not getattr(field, 'testrun_apply', False):
                continue
            victim[fieldname] = self[fieldname]

    def _compute_any_testing(self):
        for rec in self:
            _fields = [
                k
                for k, v in rec._fields.items()
                if getattr(v, 'testrun_field', False)
                ]
            rec.any_testing = any(rec[f] for f in _fields)
