import re
import traceback
from functools import partial
import arrow
from contextlib import contextmanager
from . import pg_advisory_lock
import traceback
from odoo import _, api, fields, models
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT as DTF
from odoo.addons.queue_job.exception import RetryableJobError
from odoo.exceptions import ValidationError
from pathlib import Path
from contextlib import contextmanager
from .test_run import AbortException


class WrongShaException(Exception):
    pass


class CicdTestRunLine(models.AbstractModel):
    _inherit = "cicd.open.window.mixin"
    _name = "cicd.test.run.line"
    _order = "started desc"

    run_id = fields.Many2one("cicd.test.run", string="Run", required=True)
    project_name = fields.Char(compute="_compute_project_name")
    exc_info = fields.Text("Exception Info")
    queuejob_id = fields.Many2one("queue.job", string="Queuejob")
    machine_id = fields.Many2one("cicd.machine", string="Machine")
    duration = fields.Integer("Duration")
    batchids = fields.Char("Batch IDS")
    state = fields.Selection(
        [
            ("open", "Open"),
            ("success", "Success"),
            ("failed", "Failed"),
            ("running", "Running"),
        ],
        default="open",
        required=True,
    )
    force_success = fields.Boolean("Force Success")
    try_count = fields.Integer("Try Count")
    name = fields.Char("Name", compute="_compute_name", store=False)
    test_setting_id = fields.Reference(
        [
            ("cicd.test.settings.unittest", "Unit Test"),
            ("cicd.test.settings.robottest", "Robot Test"),
            ("cicd.test.settings.migrations", "Migration Test"),
        ],
        string="Initiating Testsetting",
        required=True,
    )
    reused = fields.Boolean("Reused", readonly=True)
    started = fields.Datetime("Started", default=lambda self: fields.Datetime.now())
    effective_machine_id = fields.Many2one("cicd.machine", compute="_compute_machine")
    logfile_path = fields.Char("Logfilepath", compute="_compute_logfilepath")
    log = fields.Text("Log")

    def _reset_logfile(self):
        Path(self.logfile_path).write_text("")

    def _compute_logfilepath(self):
        for rec in self:
            rec.logfile_path = f"/opt/out_dir/testrunline_logs/testrunline_log_{rec.id}"
            Path(rec.logfile_path).parent.mkdir(exist_ok=True, parents=True)

    def _compute_machine(self):
        for rec in self:
            machine = rec.machine_id or rec.test_setting_id.effective_machine_id
            rec.effective_machine_id = machine

    @contextmanager
    def get_environment_for_execute(self):
        # shell, dictenv
        yield None, None

    @contextmanager
    def _shell(self, quick=False):
        project_name = self[0].project_name
        with self.effective_machine_id._shell(
            cwd=self._get_source_path(self[0].effective_machine_id),
            project_name=project_name,
        ) as shell:
            if not quick:
                self._ensure_source_and_machines(shell)
            yield shell

    def open_queuejob(self):
        """Display single queuejob in form view.

        Returns:
            dict: Odoo Action
        """
        return {
            "view_type": "form",
            "res_model": self.queuejob_id._name,
            "res_id": self.queuejob_id.id,
            "views": [(False, "form")],
            "type": "ir.actions.act_window",
            "target": "current",
        }

    def _compute_name(self):
        for rec in self:
            rec.name = "missing"

    def toggle_force_success(self):
        self.sudo().force_success = not self.sudo().force_success

    @api.recordchange("force_success")
    def _onchange_force(self):
        for rec in self:
            if rec.run_id.state not in ["running"]:
                rec.run_id._compute_success_state()

    @api.model
    def create(self, vals):
        if not vals.get("machine_id"):
            testrun = self.env["cicd.test.run"].browse(vals["run_id"])
            vals["machine_id"] = testrun.branch_id.repo_id.machine_id.id
        res = super().create(vals)

        return res

    def ok(self):
        return {"type": "ir.actions.act_window_close"}

    def close_window(self):
        return {"type": "ir.actions.act_window_close"}

    def handle_run_exception(self, msg):
        self.state = "failed"
        self.exc_info = msg

        # no grace anymore
        # # grace -->
        # if (
        #     ("No such file or directory" in msg and f"testrun_{self.run_id.id}" in msg)
        #     or "wodoo/myconfigparser.py" in msg
        #     or "wodoo/click_config.py" in msg
        # ):
        #     raise RetryableJobError("Recoverable error", ignore_retry=True, seconds=60)

    def execute(self):
        self.run_id._switch_to_running_state()
        breakpoint()
        logfile = Path(self[0].logfile_path)
        self.write({"state": "running"})
        self.env.cr.commit()

        @contextmanager
        def _get_env():
            try:
                with self._get_contexted().get_environment_for_execute() as (
                    shell,
                    runenv,
                ):
                    yield shell, runenv
            except RetryableJobError:
                raise
            except Exception:
                msg = traceback.format_exc()
                self.filtered(lambda x: x.state == "running").write({"state": "failed"})
                self.run_id.message_post(
                    body=("Exception at preparation occurred:\n" f"{msg}")
                )
                self.write({"log": msg})
                self.env.cr.commit()
                raise

        with _get_env() as (shell, runenv):
            for rec in self:
                try:
                    rec.try_count = 0
                    while rec.try_count < (rec.test_setting_id.retry_count or 1):
                        rec.log = False
                        if rec.run_id.do_abort:
                            raise AbortException("Aborted by user")
                        rec.try_count += 1

                        with rec.run_id._logsio() as logsio:
                            logsio.info(f"Try #{rec.try_count}")

                            rec.started = fields.Datetime.now()
                            rec.env.cr.commit()
                            try:
                                logsio.info(f"Running {rec.name}")
                                rec._execute(shell, runenv)

                            except RetryableJobError:
                                rec.started = False
                                rec.env.cr.commit()
                                raise

                            except Exception:  # pylint: disable=broad-except
                                msg = traceback.format_exc()
                                rec.handle_run_exception(msg)
                                logsio.error(f"Error happened: {msg}")

                            else:
                                # e.g. robottests return state from run
                                rec.state = "success"
                                rec.exc_info = False
                        end = arrow.get()
                        rec.duration = (
                            end - arrow.get(rec.started or arrow.utcnow())
                        ).total_seconds()
                        if rec.state == "success":
                            break

                        rec.log = False
                        if logfile.exists():
                            rec.log = logfile.read_text()
                            logfile.unlink()

                except RetryableJobError:
                    raise

                except Exception:  # pylint: disable=broad-except
                    if logfile.exists():
                        logfile.unlink()
                    msg = traceback.format_exc()
                    log = (
                        ",".join(map(str, set(filter(bool, self.mapped("log")))))
                    ) or ""
                    self.write({"log": f"{msg}\n{log}"})
                    raise

                finally:
                    rec.env.cr.commit()

    def _report(self, msg=None, exception=None):
        if exception:
            if isinstance(exception, RetryableJobError):
                return

        if exception:
            msg = (msg or "") + "\n" + str(exception)

        if not msg:
            return

        with open(self.logfile_path, "a") as file:
            file.write(msg)
            file.write("\n")

        with self.run_id._logsio(None) as logsio:
            logsio.info(msg)

    def _log(self, func, comment="", allow_error=False):
        started = arrow.utcnow()
        if comment:
            self._report(comment)
        params = {}
        try:
            func(self)
        except Exception as ex:  # pylint: disable=broad-except
            self._report(exception=ex)
            if not allow_error:
                raise
        finally:
            duration = (arrow.utcnow() - started).total_seconds()
            self._report(f"Duration: {duration}s; {comment}", **params)
        self.run_id._abort_if_required()

    def _lo(self, shell, *params, comment=None, **kwparams):
        comment = comment or " ".join(map(str, params))
        self._log(
            lambda self: shell.odoo(*params, **kwparams),
            comment=comment,
            allow_error=kwparams.get("allow_error"),
        )

    def _clean_instance_folder(self):
        machine = self.effective_machine_id
        folder = self._get_source_path(machine)
        with machine._shell() as shell:
            if shell.exists(folder):
                shell.remove(folder)

    def _ensure_source_and_machines(
        self,
        shell,
        start_postgres=False,
        settings="",
        reload_only_on_need=False,
    ):
        assert list(filter(bool, self.mapped("batchids")))
        selfone = self[0]
        selfone._log(
            lambda self: self._checkout_source_code(shell.machine, selfone.batchids),
            "checkout source",
        )

        lo = partial(selfone._lo, shell)
        self.run_id._reload(shell, settings, shell.cwd)

        lo("regpull", allow_error=True)
        lo("build")
        lo("kill", allow_error=True)
        lo("rm", allow_error=True)
        if start_postgres:
            lo("up", "-d", "postgres")
            shell.wait_for_postgres()

    def _get_source_path(self, machine):
        path = Path(machine._get_volume("source"))
        batchids = ",".join(list(set(filter(bool, self.mapped("batchids")))))
        # one source directory for all tests; to have common .dirhashes
        # and save disk space
        # 22.06.2022 too many problems - directory missing in tests
        # back again
        # better structure with prepare environment
        path = path / f"testrun_{batchids}"
        return path

    def _checkout_source_code(self, machine, batchids):
        assert machine._name == "cicd.machine"

        with pg_advisory_lock(self.env.cr, f"testrun_{batchids}"):
            path = self._get_source_path(machine)
            with machine._shell(cwd=path.parent) as shell:

                def refetch_dir():
                    shell.remove(path)
                    self._report("Checking out source code...")
                    self.run_id.branch_id.repo_id._technical_clone_repo(
                        path=path,
                        machine=machine,
                        branch=self.run_id.branch_id.name,
                    )
                    with shell.clone(cwd=path) as shell2:
                        shell2.checkout_commit(self.run_id.commit_id.name)

                if not shell.exists(path / ".git"):
                    refetch_dir()

                def matches_commit():
                    current_commit = shell.X(["git-cicd", "log", "-n1", "--format=%H"])[
                        "stdout"
                    ].strip()
                    dirty = bool(
                        shell.X(["git-cicd", "status", "-s"])["stdout"].strip()
                    )
                    return current_commit == self.run_id.commit_id.name and not dirty

                with shell.clone(cwd=path) as shell:
                    if not matches_commit():
                        refetch_dir()
                        if not matches_commit():
                            raise WrongShaException(
                                (
                                    f"After force checkout directory is not clean and "
                                    f"matching test sha {self.run_id.commit_id.name}"
                                    f"in {shell.cwd}"
                                )
                            )
                    self._report("Commit matches")
                    self._report(f"Checked out source code at {shell.cwd}")

    def cleanup(self):
        if not self.batchids:
            return
        instance_folder = self._get_source_path(self.effective_machine_id)

        with self.effective_machine_id._shell(
            project_name=self.project_name,
        ) as shell:
            if shell.exists(instance_folder):
                with shell.clone(cwd=instance_folder) as shell2:
                    shell2.odoo("down", "-v", force=True, allow_error=True)
                    shell2.odoo("rm", force=True, allow_error=True)

                shell.remove(instance_folder)

    def as_job(self, suffix, afterrun=False, eta=None):
        marker = self.run_id._get_qj_marker(suffix, afterrun=afterrun)
        eta = arrow.utcnow().shift(minutes=eta or 0).strftime(DTF)
        return self.with_delay(channel="testruns", identity_key=marker, eta=eta)

    def _create_worker_queuejob(self):
        ids_string = ",".join(map(str, sorted(self.ids)))
        names = ",".join(self.mapped("name"))
        idkey = f"testrunline-{ids_string}-{names}"
        job = self.as_job(suffix=idkey).execute()
        jobs = self.env["queue.job"].search([("uuid", "=", job.uuid)])
        if not jobs:
            raise Exception("Could not create queuejob")
        self.write({"queuejob_id": jobs[0], "batchids": ids_string})

    def _get_contexted(self):
        batchids = "_".join(list(map(str, sorted(set(self.mapped("batchids"))))))
        return self.with_context(testrun=f"testrun_{batchids}")

    def _compute_project_name(self):
        for rec in self:
            rec_testrun = rec._get_contexted()
            project_name = rec_testrun.run_id.branch_id.project_name
            rec.project_name = project_name

    def _is_success(self):
        for rec in self:
            if rec.state in [False, "open", "running"]:
                if rec.run_id.do_abort:
                    return False
                raise RetryableJobError(
                    "Line is open - have to wait.", ignore_retry=True, seconds=30
                )
            if rec.state == "failed" and not rec.force_success:
                return False
        return True

    def retry(self):
        for rec in self:
            rec.state = "open"
            rec._create_worker_queuejob()

    @property
    def snapname(self):
        batchids = "_".join(
            list(sorted(set(map(str, self.mapped("batchids")))))
        ).replace(",", "_")
        return f"snap_{batchids}"

    @api.model
    def _clean_old_testdirs(self, days=3):
        breakpoint()
        for repo in self.search([]):
            machine = repo.machine_id
            srcfolder = machine._get_volume("source")
            with machine._shell(cwd=srcfolder) as shell:

                all_folders = list(
                    map(
                        lambda x: x.split("/")[-1],
                        shell.X(
                            [
                                "find",
                                ".",
                                "-maxdepth",
                                "1",
                                "-type",
                                "d",
                                "-mtime",
                                f"+{days}",
                            ]
                        )["stdout"].splitlines(),
                    )
                )

                for item in filter(lambda x: x.startswith("testrun"), all_folders):
                    shell.remove(item)
                for branch in self.env["cicd.git.branch"].search(
                    [("active", "=", False), ("repo_id", "=", repo.id)]
                ):
                    if not branch.technical_branch_name:
                        continue
                    if branch.technical_branch_name in all_folders:
                        shell.remove(srcfolder / branch.technical_branch_name)

                for item in release_branches:
                    if [x for x in release_branches if item in x]:
                        shell.remove(srcfolder / item)

                for item in filter(lambda x: x.startswith("prj"), all_folders):
                    if item not in active_branches:
                        shell.remove(srcfolder / item)


