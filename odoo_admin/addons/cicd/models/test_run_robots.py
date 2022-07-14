# pylint: disable=self-cls-assignment
# pylint: disable=R0903
import uuid
import re
import json
import base64
from pathlib import Path
from odoo import fields, models
from odoo.exceptions import ValidationError
from contextlib import contextmanager, closing
from .test_run import SETTINGS

# There are tests, that put files into /tmp so better run in one container
ROBOT_SETTINGS = "\n" "RUN_ROBOT=1\nDEFAULT_DEV_PASSWORD=1\n"


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
    tags = fields.Char("Tags")

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
        DBNAME = "odoo"
        with self._shell(quick=True) as shell:
            # there could be errors at install all
            dump_path = self.run_id.branch_id._ensure_dump(
                "full", self.run_id.commit_id.name, dumptype="wodoobin", dbname=DBNAME
            )
            self.env.cr.commit()  # publish the dump; there is a cache instruction on the branch

            breakpoint()
            settings = self.env["cicd.git.branch"]._get_settings_isolated_run(
                dbname=DBNAME,
                forcesettings=(
                    f"{SETTINGS}\n"
                    f"{ROBOT_SETTINGS}\n"
                    f"SERVER_WIDE_MODULES=base,web\n"
                    f"DBNAME={DBNAME}"
                ),
            )
            assert dump_path

            self._ensure_source_and_machines(
                shell,
                start_postgres=False,
                settings=settings,
            )
            shell.odoo("down", "-v", force=True, allow_error=True)

            shell.odoo("up", "-d", "postgres")
            shell.wait_for_postgres()  # wodoo bin needs to check version
            shell.odoo("restore", "odoo-db", dump_path, "--no-dev-scripts", force=True)
            if self.test_setting_id.use_btrfs:
                shell.odoo("snap", "remove", self.snapname, allow_error=True)
                shell.wait_for_postgres()
                shell.odoo("turn-into-dev")
                shell.odoo("snap", "save", self.snapname)
            shell.wait_for_postgres()

            configuration = shell.odoo("config", "--full")["stdout"].splitlines()
            host_run_dir = [x for x in configuration if "HOST_RUN_DIR:" in x]
            host_run_dir = Path(host_run_dir[0].split(":")[1].strip())
            robot_out = host_run_dir / "odoo_outdir" / "robot_output"

            try:
                yield shell, {"robot_out": robot_out, "dump_path": dump_path}
            finally:
                if self.test_setting_id.use_btrfs:
                    shell.odoo("snap", "clear", allow_error=True)
                shell.odoo("kill", allow_error=True)
                shell.odoo("rm", allow_error=True)
                shell.odoo("down", "-v", force=True, allow_error=True)

    def _execute(self, shell, runenv):
        self._reset_fields()

        shell.odoo("kill")
        if self.test_setting_id.use_btrfs:
            shell.odoo("snap", "restore", self.snapname)
        else:
            shell.odoo(
                "restore",
                "odoo-db",
                runenv["dump_path"],
                "--no-dev-scripts",
                force=True,
            )
            shell.odoo("turn-into-dev")
        shell.odoo("up", "-d", "postgres")
        shell.wait_for_postgres()
        shell.odoo("up", "-d", "odoo")
        shell.odoo("up", "-d")
        results_file = f"results_file.{uuid.uuid4()}.json"
        cmd = [
            "robot",
            "--parallel",
            self.parallel,
            "--keep-token-dir",
            "--output-json",
            "--results-file",
            results_file,
            "-p",
            "password=1",
        ]
        if self.tags:
            cmd += ["--tags", self.tags]
        cmd += [self.filepath]
        process = shell.odoo(
            *cmd,
            timeout=self.test_setting_id.timeout,
            allow_error=True,
        )

        breakpoint()
        results_path = runenv["robot_out"] / results_file
        del results_file
        if not shell.exists(results_path):
            testdata = []
        else:
            testdata = json.loads(shell.get(results_path))
            shell.rm(results_path)
            testdata = self._eval_test_output(testdata)
            self._grab_robot_output(shell, testdata[0]["testoutput"])

        try:
            excel_file = shell.sql_excel(
                ("select id, name, state, exc_info " "from queue_job")
            )
        except Exception as ex:
            self.run_id.message_post(body=str(ex))
        else:
            if excel_file:
                self.queuejob_log = base64.b64encode(excel_file)

        if not testdata:
            raise Exception(f"{process['stdout']}\n" f"{process['stderr']}")

        if not testdata[0].get("all_ok"):
            raise Exception(
                (
                    "Tests failed - not ok from console call\n"
                    f"Data:\n{json.dumps(testdata, indent=4)}"
                )
            )

    def _eval_test_output(self, testdata):
        """
        Wodoo outputs the test results of each test in a json table
        """

        self.avg_duration = testdata[0].get("avg_duration", False)
        self.max_duration = testdata[0].get("max_duration", False)
        self.min_duration = testdata[0].get("min_duration", False)
        return testdata

    def _grab_robot_output(self, shell, folder):
        robot_out = Path(folder)
        robot_results_tar = shell.grab_folder_as_tar(robot_out)
        robot_results_tar = (
            base64.b64encode(robot_results_tar) if robot_results_tar else False
        )
        self.robot_output = robot_results_tar
        shell.rm(robot_out)

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

    tags = fields.Char("Filter to tags (comma separated, may be empty)", default="")
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
