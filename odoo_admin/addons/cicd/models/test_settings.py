# pylint: disable=W0212,E0401
import arrow
from contextlib import contextmanager
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.addons.queue_job.exception import RetryableJobError
from odoo.models import NewId
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT as DTF


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
            ("cicd.git.repo", "Repository"),
        ],
        string="Test",
        required=True,
        copy=False,
    )
    preparation_done = fields.Boolean(
        ("Set at testruns when the preparation of test run lines succeeded"),
        copy=False,
    )
    name = fields.Char(compute="_compute_name", store=False)
    machine_id = fields.Many2one("cicd.machine", string="Machine")
    lines_per_worker = fields.Integer(string="Worker Batch", default=8)
    effective_machine_id = fields.Many2one(
        "cicd.machine", compute="_compute_effective_machine"
    )
    use_btrfs = fields.Boolean(related="effective_machine_id.postgres_server_id.btrfs")

    @api.model
    def default_get(self, fields):
        res = super().default_get(fields)
        params = self.env.context.get("params", {})
        if params and params.get("id"):
            parent = self.env[params.get("model")].browse(params["id"])
            res["effective_machine_id"] = self._get_machine(parent)
        return res

    def _get_machine(self, parent):
        machine = self.machine_id
        if not machine:
            if hasattr(parent, "branch_ids"):
                machine = parent.branch_ids.repo_id.machine_id
        if not machine:
            if hasattr(parent, "branch_id"):
                machine = parent.branch_id.repo_id.machine_id
        return machine

    @api.depends("machine_id", "parent_id")
    def _compute_effective_machine(self):
        for rec in self:
            machine = self._get_machine(rec.parent_id)
            rec.effective_machine_id = machine

    def as_job(self, suffix, afterrun=False, eta=None):
        """Puts the execution of a line into a queuejob.

        Args:
            suffix (string): a unique identifier
            afterrun (bool, optional): If True, it is considered as a cleanup job.
            If only cleanups are running, then the test is considered as done. Defaults to False.
            eta (int, optional): Parameter eta passed to queuejobs. Defaults to None.

        Returns:
            TestSettingAbstract: a queuejobified version of self
        """
        marker = self.parent_id._get_qj_marker(suffix, afterrun=afterrun)
        eta = arrow.utcnow().shift(minutes=eta or 0).strftime(DTF)
        return self.with_delay(channel="testruns", identity_key=marker, eta=eta)

    def _compute_name(self):
        for rec in self:
            rec.name = rec.get_name()

    def get_name(self):
        raise NotImplementedError()

    def reset_at_testrun(self):
        """If a testrun is restarted to reset fields."""
        self.preparation_done = False

    def get_testrun_values(self, testrun, defaults=None):
        """Returns minimum settings for a setup configuration line. The minimum values
        are taken from the base model, that all others inherit from.

        Args:
            testrun (odoo.model): Test Run Instance
            defaults (dict, optional): Values to be set for specific
            setting like unittest/robottest. Defaults to None.

        Returns:
            dict: all combined values
        """
        vals = defaults or {}
        assert isinstance(defaults, dict)
        vals.update(
            {
                "test_setting_id": f"{self._name},{self.id}",
                "run_id": testrun.id,
                "machine_id": self.machine_id.id
                or self.parent_id.branch_id.repo_id.machine_id.id,
            }
        )
        return vals

    def produce_test_run_lines(self, testrun):  # pylint: disable=unused-argument
        """
        Produces the real tests, that are run individually from this setting.
        Called by specific classes like for unittests, robottests.
        Basic setup is done here.
        """
        self.preparation_done = True

    def init_testrun(self, testrun):
        unprepared = self.filtered(lambda x: not x.preparation_done)
        if not unprepared:
            return
        lines = unprepared.produce_test_run_lines(
            testrun
        )
        for i in range(0, len(lines or []), self.lines_per_worker):
            batch = lines[i : i + self.lines_per_worker]
            ids = [x.id for x in batch]
            browse_lines = batch[0].browse(ids)
            browse_lines._create_worker_queuejob()


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
        "Success Rate [%]", compute="_compute_success_rate_factor", tracking=True
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
            # deleted?
            lines = list(rec.iterate_all_test_settings())
            for field in self._get_test_run_fields():
                existing = self.env[self._fields[field].comodel_name].search(
                    [("parent_id", "=", f"{self._name},{rec.id}")]
                )
                for ex in existing:
                    if ex not in lines:
                        ex.unlink()

            for line in lines:

                def is_transferable_value(line, field):
                    obj_field = line._fields[field]
                    if obj_field.compute and not obj_field.store:
                        return False
                    if field in [
                        "id",
                        "display_name",
                        "test_run_line_ids",
                        "parent_id",
                        "create_uid",
                        "create_date",
                        "write_uid",
                        "write_date",
                        "__last_update",
                    ]:
                        return False
                    return True

                def adapt(fieldname, x):
                    if isinstance(x, models.AbstractModel) and x:
                        if line._fields[fieldname].type == "many2one":
                            return x.id
                        else:
                            return [[6, 0, x.ids]]
                    return x

                values = {
                    x: adapt(x, line[x])
                    for x in line._fields.keys()
                    if is_transferable_value(line, x)
                }
                values["parent_id"] = f"{rec._name},{rec.id}"
                if isinstance(line.id, NewId):
                    self.env[line._name].create(values)
                else:
                    self.env[line._name].browse(line.id).write(values)

    def _compute_success_rate_factor(self):
        for rec in self:
            if rec._name != "cicd.test.run":
                rec.success_rate = 0
            else:
                try:
                    success_lines = float(
                        len([x for x in rec.iterate_testlines() if x._is_success()])
                    )
                except RetryableJobError:
                    success_lines = 0
                count_lines = float(len(list(rec.iterate_testlines())))
                if not count_lines:
                    rec.success_rate = 0
                else:
                    rec.success_rate = 100 * success_lines / count_lines

    def iterate_all_test_settings(self):
        """Iterates all fields, that contain test-settings.

        Yields:
            inherited cicd.test.setting.base: A test setting
        """
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
        """Transfers settings of test setup e.g. from branch to testrun,
        from repo to branch

        Initially for all branches:

        Args:
            victim (odoo.model): Inherited from cicd.test.settings
        """
        for fieldname, field in self._fields.items():
            if not getattr(field, "testrun_field", False):
                continue
            victim[fieldname].unlink()
            for line in self[fieldname]:
                line.copy({"parent_id": f"{victim._name},{victim.id}"})

    def _compute_any_testing(self):
        for rec in self:
            _fields = [
                k for k, v in rec._fields.items() if getattr(v, "testrun_field", False)
            ]
            rec.any_testing = any(rec[f] for f in _fields)

    def _is_success(self):
        if not hasattr(self, "iterate_testlines"):
            return False
        for line in self.iterate_testlines():
            if not line._is_success():
                return False
        return True

    @contextmanager
    def _get_source_for_analysis(self):
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
                breakpoint()
                shell.checkout_commit(self.commit_id.name)
                yield shell
