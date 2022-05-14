from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError


class TestSettings(models.AbstractModel):
    _name = 'cicd.test.settings'

    run_unittests = fields.Boolean(
        "Run Unittests", default=True, testrun_field=True)
    run_robottests = fields.Boolean(
        "Run Robot-Tests", default=True, testrun_field=True)
    simulate_install_id = fields.Many2one(
        "cicd.dump", string="Simulate Install", testrun_field=True)
    unittest_all = fields.Boolean("All Unittests")
    retry_unit_tests = fields.Integer("Retry Unittests", default=3)
    timeout_tests = fields.Integer("Timeout Tests [s]", default=600)
    timeout_migration = fields.Integer("Timeout Migration [s]", default=1800)
    any_testing = fields.Boolean(compute="_compute_any_testing")

    def _compute_any_testing(self):
        for rec in self:
            _fields = [
                k
                for k, v in rec._fields.items()
                if getattr(v, 'testrun_field', False)
                ]
            rec.any_testing = any(rec[f] for f in _fields)
