import base64
from pathlib import Path
from odoo import _, api, fields, models, SUPERUSER_ID
from contextlib import contextmanager, closing
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from .test_run import SETTINGS


class MigrationTest(models.Model):
    _inherit = "cicd.test.run.line"
    _name = "cicd.test.run.line.migration"

    dump_id = fields.Many2one("cicd.dump", string="Dump", required=True)

    def _compute_name(self):
        for rec in self:
            filepath = (rec.dump_id.name or "").split("/")[-1]
            rec.name = filepath

    @api.constrains("dump_id", "machine_id")
    def _check_dump(self):
        for rec in self:
            if rec.dump_id and rec.machine_id:
                if rec.dump_id.machine_id != rec.machine_id:
                    raise ValidationError("Machines must match to dump")

    @contextmanager
    def get_environment_for_execute(self):
        breakpoint()
        DBNAME = "odoo"
        if not self.dump_id.name:
            raise ValidationError("Dump required!")
        with self._shell(quick=True) as shell:
            settings = self.env["cicd.git.branch"]._get_settings_isolated_run(
                dbname=DBNAME,
                forcesettings=(
                    f"{SETTINGS}\n" f"SERVER_WIDE_MODULES=base,web\n" f"DBNAME={DBNAME}"
                ),
            )

            self._ensure_source_and_machines(
                shell,
                start_postgres=False,
                settings=settings,
            )
            shell.odoo("down", "-v", force=True, allow_error=True)

            shell.odoo("up", "-d", "postgres")
            breakpoint()
            shell.odoo(
                "restore",
                "odoo-db",
                self.dump_id.name,
                "--no-dev-scripts",
                force=True,
            )
            shell.odoo("snap", "remove", self.snapname, allow_error=True)
            shell.odoo("snap", "save", self.snapname)
            shell.wait_for_postgres()

            try:
                yield shell, {}
            finally:
                shell.odoo("snap", "remove", self.snapname, allow_error=True)
                shell.odoo("kill", allow_error=True)
                shell.odoo("rm", allow_error=True)
                shell.odoo("down", "-v", force=True, allow_error=True)

    def _execute(self, shell, runenv):
        self._report(f"Restoring {self.run_id.branch_id.dump_id.name}")

        shell.odoo("-f", "restore", "odoo-db", self.test_setting_id.dump_id.name)
        shell.wait_for_postgres()
        shell.odoo("update", timeout=self.test_setting_id.timeout)
        shell.wait_for_postgres()


class TestSettingsMigrations(models.Model):
    _inherit = "cicd.test.settings.base"
    _name = "cicd.test.settings.migrations"
    _line_model = "cicd.test.run.line.migration"

    dump_id = fields.Many2one(
        "cicd.dump",
        string="Dump",
        required=True,
        domain="[('machine_id', '=', effective_machine_id)]",
    )

    def get_name(self):
        return f"{self.id} - {self.dump_id.name}"

    def produce_test_run_lines(self, testrun):
        res = []
        super().produce_test_run_lines(testrun)
        res.append(
            self.env[self._line_model].create(
                self.get_testrun_values(
                    testrun,
                    {
                        "dump_id": self.dump_id.id,
                    },
                )
            )
        )
        return res
