from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError


class TestSettingAbstract(models.AbstractModel):
    _name = "cicd.test.settings.base"

    timeout = fields.Integer("Timeout Seconds", default=600)
    retry_count = fields.Integer("Retries", default=3)
    test_setting_id = fields.Many2one(
        'cicd.test.settings', string="Test", required=True, ondelete="cascade")


class TestSettingsUnittest(models.Model):
    _inherit = "cicd.test.settings.base"
    _name = 'cicd.test.settings.unittest'

    tags = fields.Char("Filter to tags (comma separated, may be empty)")


class TestSettingsRobotTests(models.Model):
    _inherit = "cicd.test.settings.base"
    _name = 'cicd.test.settings.robottest'

    tags = fields.Char(
        "Filter to tags (comma separated, may be empty)", default="load-test")
    parallel = fields.Char(
        "In Parallel", required=True, default="1,2,5,10,20,50")
    glob = fields.Char("Glob", default="**/*.robot", required=True)


class TestSettingsMigrations(models.Model):
    _inherit = "cicd.test.settings.base"
    _name = 'cicd.test.settings.migrations'

    dump_id = fields.Many2one('cicd.dump', string="Dump"),


class TestSettings(models.Model):
    _name = 'cicd.test.settings'

    unittest_ids = fields.One2many(
        "cicd.test.settings.unittest", "test_setting_id", testrun_field=True)
    robottest_ids = fields.One2many(
        "cicd.test.settings.unittest", "test_setting_id", testrun_field=True)
    migration_ids = fields.One2many(
        "cicd.test.settings.migrations", "test_setting_id", testrun_field=True)

    any_testing = fields.Boolean(compute="_compute_any_testing")

    def apply_test_settings(self, victim):
        for fieldname, field in self._fields.items():
            if not getattr(field, 'testrun_field', False):
                continue
            victim[fieldname].unlink()
            for line in self[fieldname]:
                line.copy({'test_id': self.id})

    def _compute_any_testing(self):
        for rec in self:
            _fields = [
                k
                for k, v in rec._fields.items()
                if getattr(v, 'testrun_field', False)
                ]
            rec.any_testing = any(rec[f] for f in _fields)
