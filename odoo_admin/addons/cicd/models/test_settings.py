import arrow
from contextlib import contextmanager
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from odoo.addons.queue_job.exception import RetryableJobError
from odoo.models import NewId
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT as DTF
from odoo.tools import DEFAULT_SERVER_DATE_FORMAT


class TestSettingAbstract(models.AbstractModel):
    """
    This is used for specific settings for example timeout, glob on tests
    for robot tests, unittests.
    """

    _name = "cicd.test.settings.base"

    timeout = fields.Integer("Timeout Seconds", default=600)
    retry_count = fields.Integer("Retries", default=3)
    parent_id = fields.Reference(
        [
            ("cicd.test.run", "Test-Run"),
            ("cicd.git.branch", "Branch"),
            ("cicd.release", "Release"),
        ],
        string="Test",
        required=True,
    )
    preparation_done = fields.Boolean(
        ("Set at testruns when the preparation of test run lines succeeded")
    )
    test_run_line_ids = fields.Many2many(
        "cicd.test.run.line", compute="_compute_test_run_lines", store=False, copy=False
    )
    success_rate = fields.Float(compute="_compute_success_rate", store=False)
    name = fields.Char(compute="_compute_name", store=False)

    def as_job(self, suffix, afterrun=False, eta=None):
        marker = self.parent_id._get_qj_marker(suffix, afterrun=afterrun)
        eta = arrow.utcnow().shift(minutes=eta or 0).strftime(DTF)
        return self.with_delay(channel="testruns", identity_key=marker, eta=eta)

    def _compute_test_run_lines(self):
        for rec in self:
            ref = f"{self._name},{rec.id}"
            self.test_run_line_ids = self.env["cicd.test.run.line"].search(
                [("test_setting_id", "=", ref)]
            )

    def _compute_name(self):
        for rec in self:
            rec.name = rec.get_name()

    def get_name(self):
        raise NotImplementedError()

    def reset_at_testrun(self):
        self.preparation_done = False

    def _compute_success_rate(self):
        for rec in self:
            if not rec.preparation_done or rec.parent_id._name != "cicd.test.run":
                rec.success_rate = 0
            else:
                success_lines = float(
                    len(
                        x
                        for x in rec.test_setting_id.test_run_line_ids.filtered_domain(
                            [("state", "=", "success")]
                        )
                    )
                )
                count_lines = float(len(rec.test_run_line_ids))
                if not count_lines:
                    rec.success_rate = 0
                else:
                    rec.success_rate = 100 * success_lines / count_lines

    def _is_success(self):
        self.ensure_one()
        for line in self.test_run_line_ids:
            if not self.preparation_done or not line.state:
                raise RetryableJobError("Not all lines done", ignore_retry=True)
            if line.ttype not in ("preparation", "log"):
                if line.state == "failed":
                    return False

    def get_testrun_values(self, testrun, defaults=None):
        vals = defaults or {}
        vals.update(
            {"test_setting_id": f"{self._name},{self.id}", "run_id": testrun.id}
        )
        return vals

    def produce_test_run_lines(self, testrun):
        self.preparation_done = True


class TestSettings(models.Model):
    """
    This is the container of test settings so you can configure
    a robot run with a glob, a unittest with a glob and then another robot run
    """

    _name = "cicd.test.settings"

    unittest_ids = fields.One2many(
        "cicd.test.settings.unittest",
        testrun_field=True,
        compute="_compute_testsetting_ids",
        inverse="_set_testsetting_ids",
    )
    robottest_ids = fields.One2many(
        "cicd.test.settings.robottest",
        testrun_field=True,
        compute="_compute_testsetting_ids",
        inverse="_set_testsetting_ids",
    )
    migration_ids = fields.One2many(
        "cicd.test.settings.migrations",
        testrun_field=True,
        compute="_compute_testsetting_ids",
        inverse="_set_testsetting_ids",
    )

    any_testing = fields.Boolean(compute="_compute_any_testing")
    success_rate = fields.Float(
        "Success Rate", compute="_compute_success_rate_factor", tracking=True
    )
    state = fields.Selection([])

    def _compute_testsetting_ids(self):
        for rec in self:
            rec.unittest_ids = self.env["cicd.test.settings.unittest"].search(
                [("parent_id", "=", f"{rec._name},{rec.id}")]
            )
            rec.robottest_ids = self.env["cicd.test.settings.robottest"].search(
                [("parent_id", "=", f"{rec._name},{rec.id}")]
            )
            rec.migration_ids = self.env["cicd.test.settings.migrations"].search(
                [("parent_id", "=", f"{rec._name},{rec.id}")]
            )

    def _set_testsetting_ids(self):
        for rec in self:
            breakpoint()
            for line in rec.iterate_all_test_settings():

                def ok(field):
                    # TODO function in models?
                    if field in [
                        "id",
                        "create_uid",
                        "create_date",
                        "write_uid",
                        "write_date",
                        "__last_update",
                    ]:
                        return False
                    if field in ["display_name"]:
                        return False
                    return True

                values = {x: line[x] for x in line._fields.keys() if ok(x)}
                values["parent_id"] = f"{rec._name},{rec.id}"
                if isinstance(line.id, NewId):
                    self.env[line._name].create(values)
                else:
                    self.env[line._name].browse(line.id).write(values)

    def _compute_success_rate_factor(self):
        for rec in self:
            success_rates = list(
                map(lambda x: x.success_rate, self.iterate_all_test_settings())
            )
            if not success_rates:
                rec.success_rate = 0
            else:
                rec.success_rate = float(sum(success_rates)) / float(len(success_rates))

    def iterate_all_test_settings(self):
        for field in self._get_test_run_fields():
            for line in self[field]:
                yield line

    @api.model
    def _get_test_run_fields(self):
        for fieldname, field in self._fields.items():
            if not getattr(field, "testrun_field", False):
                continue
            yield fieldname

    def apply_test_settings(self, victim):
        for fieldname, field in self._fields.items():
            if not getattr(field, "testrun_field", False):
                continue
            victim[fieldname].unlink()
            for line in self[fieldname]:
                line.copy({"test_id": self.id})

    def _compute_any_testing(self):
        for rec in self:
            _fields = [
                k for k, v in rec._fields.items() if getattr(v, "testrun_field", False)
            ]
            rec.any_testing = any(rec[f] for f in _fields)

    def _is_success(self):
        for line in self.iterate_all_test_settings():
            if not line._is_success():
                return False
        return True

    @contextmanager
    def _get_source_for_analysis(self):
        breakpoint()
        repo = self.branch_ids.repo_id
        machine = repo.machine_id
        if self._name != "cicd.test.run":
            raise ValidationError("Parent must be a test-run")

        project_name = self.with_context(
            testrun="_analyze_source"
        ).branch_id.project_name

        with repo._temp_repo(machine, self.branch_id) as folder:
            with machine._shell(
                cwd=folder,
                project_name=project_name,
            ) as shell:
                shell.checkout_commit(self.commit_id.name)
                yield shell
