# pylint: disable=self-cls-assignment
# pylint: disable=R0903
import re
import json
import base64
from pathlib import Path
from odoo import fields, models
from odoo.exceptions import ValidationError
from contextlib import contextmanager, closing
from .test_run import SETTINGS

ROBOT_SETTINGS = SETTINGS + (
    "\n" "RUN_ODOO_QUEUEJOBS=1\n" "RUN_ODOO_CRONJOBS=1\n" "RUN_ROBOT=1\n"
)


def safe_filename(filename):
    """removes invalid filesystem characters.

    Args:
        filename (str): Filename to make safe

    Returns:
        str: The safe filename
    """
    for character in "/\\;!()*":
        filename = filename.replace(character, "_")
    return filename


class RobotTest(models.Model):
    """Represents concrete robot-tests."""

    _inherit = "cicd.test.run.line"
    _name = "cicd.test.run.line.robottest"

    filepath = fields.Char("Filepath")
    robot_output = fields.Binary("Robot Output", attachment=True)
    parallel = fields.Char("In Parallel")
    avg_duration = fields.Float("Avg Duration [s]")
    min_duration = fields.Float("Min Duration [s]")
    max_duration = fields.Float("Max Duration [s]")
    queuejob_log = fields.Binary("Queuejob Log")
    queuejob_log_filename = fields.Char(compute="_queuejob_log_filename")

    def _queuejob_log_filename(self):
        for rec in self:
            name = self.filepath.split("/")[-1]
            rec.queuejob_log_filename = f"queuejobs-logs-{name}-{self.id}.xlsx"

    def _compute_name(self):
        for rec in self:
            filename = (rec.filepath or "").split("/")[-1]
            rec.name = f"{filename}"

    def _reset_fields(self):
        self.robot_output = False
        self.avg_duration = 0
        self.min_duration = 0
        self.max_duration = 0
        self.queuejob_log = False

    @contextmanager
    def get_environment_for_execute(self):
        names = ",".join(sorted(self.mapped("name")))
        DBNAME = "odoo"
        self = self.with_context(testrun=(f"testrun_{self[0].batchids}_{names}"))
        with self._shell(quick=True) as shell:
            # there could be errors at install all
            dump_path = self.run_id.branch_id._ensure_dump(
                "full", self.run_id.commit_id.name, dumptype="wodoobin", dbname=DBNAME
            )
            self.env.cr.commit()  # publish the dump; there is a cache instruction on the branch
            ids_as_string = "_".join(sorted(map(str, self.ids)))

            settings = SETTINGS + (f"\nSERVER_WIDE_MODULES=base,web\nDBNAME={DBNAME}")
            assert dump_path

            self._ensure_source_and_machines(
                shell,
                start_postgres=False,
                settings=settings,
            )
            shell.odoo("down", "-v", force=True, allow_error=True)

            snapname = f"snap_{ids_as_string}"
            breakpoint()
            shell.odoo("up", "-d", "postgres")
            shell.odoo("restore", "odoo-db", dump_path, "--no-dev-scripts", force=True)
            shell.odoo("snap", "remove", snapname, allow_error=True)
            shell.odoo("snap", "save", snapname)
            shell.wait_for_postgres()

            try:
                yield shell, {
                    "snapname": snapname,
                }
            finally:
                shell.odoo("snap", "remove", snapname, allow_error=True)
                shell.odoo("kill", allow_error=True)
                shell.odoo("rm", allow_error=True)
                shell.odoo("down", "-v", force=True, allow_error=True)

    def _execute(self, shell, runenv):
        safe_robot_file = safe_filename(self.filepath)
        self = self.with_context(testrun=f"testrun_{self.id}_robot_{safe_robot_file}")

        self._reset_fields()

        shell.odoo("snap", "restore", runenv["snapname"])
        shell.odoo("up", "-d", "postgres")
        shell.wait_for_postgres()

        shell.odoo("up", "-d")
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
            allow_error=True,
        )["stdout"].split("---!!!---###---")

        self._grab_robot_output(shell)

        if len(output) == 1:
            raise ValidationError(
                "Did not find marker to get json result from console output here "
                f"in {output}"
            )
        testdata = self._eval_test_output(output[1])

        excel_file = shell.sql_excel(
            ("select id, name, state, exc_info " "from queue_job")
        )
        if excel_file:
            self.queuejob_log = base64.b64encode(excel_file)

        if not testdata[0].get("all_ok"):
            raise Exception(
                (
                    "Tests failed - not ok from console call\n"
                    f"Data:\n{json.dumps(testdata, indent=4)}"
                )
            )

    def _eval_test_output(self, output):
        """
        Wodoo outputs the test results of each test in a json table
        """
        testdata = json.loads(output)
        self.avg_duration = testdata[0].get("avg_duration", False)
        self.max_duration = testdata[0].get("max_duration", False)
        self.min_duration = testdata[0].get("min_duration", False)
        return testdata

    def _grab_robot_output(self, shell):
        configuration = shell.odoo("config", "--full")["stdout"].splitlines()
        host_run_dir = [x for x in configuration if "HOST_RUN_DIR:" in x]
        host_run_dir = Path(host_run_dir[0].split(":")[1].strip())

        robot_out = host_run_dir / "odoo_outdir" / "robot_output"
        robot_results_tar = shell.grab_folder_as_tar(robot_out)
        robot_results_tar = (
            base64.b64encode(robot_results_tar) if robot_results_tar else False
        )
        self.robot_output = robot_results_tar

    def robot_results(self):
        """Action for displaying the robot results per web-controller.

        Returns:
            dict: open url action
        """
        return {
            "type": "ir.actions.act_url",
            "url": f"/robot_output/{self.id}",
            "target": "new",
        }


class TestSettingsRobotTests(models.Model):
    """Settings for robot tests"""

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
        """internal unique name

        Returns:
            string: Name
        """
        return f"{self._name}:{self.id} - {self.tags or 'no tags'}"

    def produce_test_run_lines(self, testrun):
        """Creates the concrete testable lines.

        Args:
            testrun (odoo-model): The test run.
        """
        res = []
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
                res.append(
                    self.env["cicd.test.run.line.robottest"].create(
                        self.get_testrun_values(
                            testrun,
                            {
                                "parallel": int(parallel),
                                "filepath": robotfile,
                            },
                        )
                    )
                )
        return res
