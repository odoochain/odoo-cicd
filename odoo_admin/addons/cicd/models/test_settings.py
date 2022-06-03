from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from odoo.addons.queue_job.exception import RetryableJobError


class TestSettingAbstract(models.AbstractModel):
    """
    This is used for specific settings for example timeout, glob on tests
    for robot tests, unittests.
    """
    _name = "cicd.test.settings.base"

    timeout = fields.Integer("Timeout Seconds", default=600)
    retry_count = fields.Integer("Retries", default=3)
    test_setting_id = fields.Many2one(
        'cicd.test.settings', string="Test", required=True, ondelete="cascade")
    preparation_done = fields.Boolean((
        "Set at testruns when the preparation of test run lines succeeded"))
    test_run_line_ids = fields.Many2many(
        "cicd.test.run.line", compute="_compute_test_run_lines",
        store=False, copy=False)
    success_rate = fields.Float(compute="_compute_success_rate", store=False)
    name = fields.Char(compute="_compute_name", store=False)

    def _compute_test_run_lines(self):
        for rec in self:
            ref = f"{self._name},{rec.id}"
            self.test_run_line_ids = \
                self.env['cicd.test.run.line'].search([
                    ('test_setting_id', '=', ref)
                ])

    def _compute_name(self):
        for rec in self:
            rec.name = rec.get_name()

    def get_name(self):
        raise NotImplementedError()

    def reset_at_testrun(self):
        self.preparation_done = False

    def _compute_success_rate(self):
        breakpoint()
        for rec in self:
            if not rec.preparation_done or \
                    rec.test_setting_id._name != 'cicd.test.run':
                rec.success_rate = 0
            else:
                success_lines = float(len(
                    x for x
                    in rec.test_setting_id.test_run_line_ids.filtered_domain([(
                        'state', '=', 'success')])))
                count_lines = float(len(rec.test_run_line_ids))
                if not count_lines:
                    rec.success_rate = 0
                else:
                    rec.success_rate = 100 * success_lines / count_lines

    def _is_success(self):
        for line in self.test_run_line_ids:
            if not line.preparation_done or not line.state:
                raise RetryableJobError(
                    "Not all lines done", ignore_retry=True)
            if line.ttype not in ('preparation', 'log'):
                if line.state == 'failed':
                    return False

    def produce_test_run_lines(self, testrun):
        raise NotImplementedError()


class TestSettings(models.Model):
    """
    This is the container of test settings so you can configure
    a robot run with a glob, a unittest with a glob and then another robot run
    """
    _name = 'cicd.test.settings'

    unittest_ids = fields.One2many(
        "cicd.test.settings.unittest", "test_setting_id", testrun_field=True)
    robottest_ids = fields.One2many(
        "cicd.test.settings.unittest", "test_setting_id", testrun_field=True)
    migration_ids = fields.One2many(
        "cicd.test.settings.migrations", "test_setting_id", testrun_field=True)

    any_testing = fields.Boolean(compute="_compute_any_testing")
    success_rate = fields.Float(
        "Success Rate", compute="_compute_success_rate_factor", tracking=True)

    def _compute_success_rate_factor(self):
        for rec in self:
            success_rates = list(map(
                lambda x: x.success_rate, self.iterate_all_test_settings()))
            if not success_rates:
                rec.success_rate = 0
            else:
                rec.success_rate = \
                    float(sum(success_rates)) / float(len(success_rates))

    def iterate_all_test_settings(self):
        for field in self._get_test_run_fields():
            for line in self[field]:
                yield line

    @api.model
    def _get_test_run_fields(self):
        for fieldname, field in self._fields.items():
            if not getattr(field, 'testrun_field', False):
                continue
            yield fieldname

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

    def _is_success(self):
        for line in self.iterate_all_test_settings():
            if not line._is_success():
                return False
        return True