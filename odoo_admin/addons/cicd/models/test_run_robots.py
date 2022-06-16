import re
import json
import base64
from pathlib import Path
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from .test_run import SETTINGS

settings = SETTINGS + (
    "\n" "RUN_ODOO_QUEUEJOBS=1\n" "RUN_ODOO_CRONJOBS=1\n" "RUN_ROBOT=1\n"
)


def safe_filename(filename):
    for x in "/\\;!()*":
        filename = filename.replace(x, "_")
    return filename


class RobotTest(models.Model):
    _inherit = "cicd.test.run.line"
    _name = "cicd.test.run.line.robottest"

    filepath = fields.Char("Filepath")
    robot_output = fields.Binary("Robot Output", attachment=True)
    parallel = fields.Char("In Parallel")
    avg_duration = fields.Float("Avg Duration [s]")
    min_duration = fields.Float("Min Duration [s]")
    max_duration = fields.Float("Max Duration [s]")

    def _compute_name(self):
        for rec in self:
            filename = (rec.filepath or "").split("/")[-1]
            rec.name = f"{filename}"

    def _execute(self):
        # there could be errors at install all
        self.run_id.branch_id._ensure_dump("full", self.run_id.commit_id.name)

        safe_robot_file = safe_filename(self.filepath)
        self = self.with_context(testrun=f"testrun_{self.id}_robot_{safe_robot_file}")

        with self._shell(quick=True) as shell:
            dump_path = self.run_id.branch_id._ensure_dump(
                "full", self.run_id.commit_id.name
            )
            assert dump_path
            try:
                self._ensure_source_and_machines(
                    shell, start_postgres=True, settings=settings
                )
                shell.odoo("restore", "odoo-db", dump_path, force=True)
                shell.wait_for_postgres()
                shell.odoo("up", "-d")

                configuration = shell.odoo("config", "--full")["stdout"].splitlines()
                host_run_dir = [x for x in configuration if "HOST_RUN_DIR:" in x]
                host_run_dir = Path(host_run_dir[0].split(":")[1].strip())
                robot_out = host_run_dir / "odoo_outdir" / "robot_output"

                shell.odoo("up", "-d", "postgres")
                shell.wait_for_postgres()
                output = shell.odoo(
                    "robot",
                    "--parallel",
                    self.parallel,
                    "--output-json",
                    "-p",
                    "password=1",
                    self.filepath,
                    timeout=self.test_setting_id.timeout,
                )["stdout"].split("---!!!---###---")
                if len(output) == 1:
                    raise ValidationError(
                        "Did not find marker to get json result from console output"
                    )
                testdata = json.loads(output[1])
                self.avg_duration = testdata[0].get("avg_duration", False)
                self.max_duration = testdata[0].get("max_duration", False)
                self.min_duration = testdata[0].get("min_duration", False)

                excel_file = shell.sql_excel(
                    ("select id, name, state, exc_info " "from queue_job")
                )
                if excel_file:
                    self.queuejob_log = base64.b64encode(excel_file)
                self.env.cr.commit()
                if not testdata[0].get("ok"):
                    raise Exception("Tests failed - not ok from console call")

            finally:
                shell.odoo("kill", allow_error=True)
                shell.odoo("rm", allow_error=True)
                shell.odoo("down", "-v", force=True, allow_error=True)

        robot_results_tar = shell.grab_folder_as_tar(robot_out)
        robot_results_tar = (
            robot_results_tar and base64.b64encode(robot_results_tar) or False
        )
        self.robot_output = robot_results_tar

    def robot_results(self):
        return {
            "type": "ir.actions.act_url",
            "url": f"/robot_output/{self.id}",
            "target": "new",
        }


class TestSettingsRobotTests(models.Model):
    _inherit = "cicd.test.settings.base"
    _name = "cicd.test.settings.robottest"
    _line_model = "cicd.test.run.line.robottest"

    tags = fields.Char(
        "Filter to tags (comma separated, may be empty)", default="load-test"
    )
    parallel = fields.Char(
        "In Parallel",
        required=True,
        default="1,2,5,10,20,50",
        help=(
            "Executes the robot tests in parallel. List may be comma "
            "separated and the number is the number of the parallel threads. "
            "Is useful for load-testing. For each number an own testrun is "
            "started."
        ),
    )
    regex = fields.Char("Regex", default=".*")

    def get_name(self):
        return f"{self.id} - {self.tags or 'no tags'}"

    def produce_test_run_lines(self, testrun):
        super().produce_test_run_lines(testrun)
        with self.parent_id._get_source_for_analysis() as shell:
            files = shell.odoo("list-robot-test-files")["stdout"].strip()
            files = list(filter(bool, files.split("!!!")[1].split("\n")))

        for robotfile in sorted(files):
            if self.regex:
                if not re.findall(self.regex, robotfile):
                    continue
            parallel = self.parallel or "1"
            for parallel in parallel.split(","):
                self.env["cicd.test.run.line.robottest"].create(
                    self.get_testrun_values(
                        testrun,
                        {
                            "parallel": int(parallel),
                            "filepath": robotfile,
                        },
                    )
                )
