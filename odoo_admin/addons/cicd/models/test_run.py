from contextlib import contextmanager
from collections import deque
import traceback
import time
import arrow
from . import pg_advisory_lock
from odoo import _, api, fields, models, SUPERUSER_ID, registry
from odoo.exceptions import UserError, RedirectWarning, ValidationError
import logging
import threading
logger = logging.getLogger(__name__)

class CicdTestRun(models.Model):
    _name = 'cicd.test.run'
    _order = 'date desc'

    name = fields.Char(compute="_compute_name")
    date = fields.Datetime("Date", default=lambda self: fields.Datetime.now(), required=True)
    commit_id = fields.Many2one("cicd.git.commit", "Commit", required=True)
    commit_id_short = fields.Char(related="commit_id.short", store=True)
    branch_id = fields.Many2one('cicd.git.branch', string="Initiating branch", required=True)
    branch_id_name = fields.Char(related='branch_id.name', store=False)
    branch_ids = fields.Many2many('cicd.git.branch', related="commit_id.branch_ids", string="Branches")
    repo_short = fields.Char(related="branch_ids.repo_id.short")
    state = fields.Selection([
        ('open', 'Testing'),
        ('success', 'Success'),
        ('failed', 'Failed'),
    ], string="Result", store=True, required=True, default='open')
    success_rate = fields.Integer("Success Rate [%]")
    line_ids = fields.One2many('cicd.test.run.line', 'run_id', string="Lines")
    duration = fields.Integer("Duration [s]")

    def _wait_for_postgres(self, shell):
        timeout = 60
        started = arrow.get()
        deadline = started.shift(seconds=timeout)

        while True:
            try:
                shell.odoo("psql", "--sql", "select * from information_schema.tables limit 1;")
            except Exception:
                diff = arrow.get() - started
                logger.info(f"Waiting for postgres {diff.total_seconds()}...")
                if arrow.get() < deadline:
                    time.sleep(0.5)
                else:
                    raise
            else:
                break

    @contextmanager
    def prepare_run(self, machine, logsio):
        settings = """
RUN_POSTGRES=1
        """
        root = machine._get_volume('source')
        with machine._shellexec(cwd=root, logsio=logsio) as shell:
            shell.project_name = self.branch_id.project_name # is computed by context

            self.branch_id._reload(shell, None, logsio, project_name=shell.project_name, settings=settings, commit=self.commit_id.name)
            shell.cwd = root / shell.project_name
            try:
                shell.odoo('build')
                shell.odoo('kill', allow_error=True)
                shell.odoo('rm', allow_error=True)
                logsio.info("Upping postgres...............")
                shell.odoo('up', '-d', 'postgres')
                self._wait_for_postgres(shell)
                logsio.info("DB Reset...........................")
                shell.odoo('-f', 'db', 'reset')
                self._wait_for_postgres(shell)
                logsio.info("Update")
                shell.odoo('update')
                logsio.info("Storing snapshot")
                shell.odoo('snap', 'save', shell.project_name, force=True)
                self._wait_for_postgres(shell)

                yield shell

            finally:
                try:
                    shell.odoo('kill', allow_error=True)
                    shell.odoo('rm', force=True, allow_error=True)
                    shell.odoo('down', "-v", force=True, allow_error=True)
                    project_dir = shell.cwd
                    shell.cwd = shell.cwd.parent
                    shell.rmifexists(project_dir)
                finally:
                    if logsio:
                        logsio.stop_keepalive()

    # ----------------------------------------------
    # Entrypoint
    # ----------------------------------------------
    def execute(self, shell=None, task=None, logsio=None):
        self.ensure_one()
        b = self.branch_id
        started = arrow.get()

        with pg_advisory_lock(self.env.cr, f"testrun_{self.id}"):
            if not b.any_testing:
                self.success_rate = 100
                self.state = 'success'
                b._compute_state()
                return

            self.state = 'open'
            self.line_ids = [[6, 0, []]]
            self.env.cr.commit()

            if shell:
                machine = shell.machine
            else:
                machine = self.branch_id.repo_id.machine_id

            threads = []
            data = {
                'testrun_id': self.id,
                'machine_id': machine.id,
                'technical_errors': [],
                'run_lines': deque(),
            }

            dbname = self.env.cr.dbname
            threads.append(threading.Thread(target=self.line_collector, args=(data['run_lines'], data['technical_errors'], logsio)))

            if b.run_unittests:
                threads.append(threading.Thread(target=self._execute, args=(dbname, self._run_unit_tests, self.id, data, 'test-units')))
            if b.run_robottests:
                threads.append(threading.Thread(target=self._execute, args=(dbname, self._run_robot_tests, self.id, data, 'test-robot')))
            if b.simulate_install_id:
                threads.append(threading.Thread(target=self._execute, args=(dbname, self._run_update_db, self.id, data, 'test-migration')))

            for t in threads:
                t.daemon = True
            [x.start() for x in threads]
            [x.join() for x in threads]

            if data['technical_errors']:
                for error in data['technical_errors']:
                    data['run_lines'].append({
                        'exc_info': error,
                        'ttype': 'log',
                        'state': 'failed',
                    })
                raise Exception('\n\n\n'.join(map(str, data['technical_errors'])))
            data['run_lines'].append("DONE")

            self.duration = (arrow.get() - started).total_seconds()
            logsio.info(f"Duration was {self.duration}")
            self._compute_success_rate()

    def line_collector(self, queue, errors, logsio):
        while True:
            try:
                try:
                    line = queue.popleft()
                    if line == "DONE":
                        break
                    if line:
                        logger.info(f"New result line: {line}")
                        self.env['cicd.test.run.line'].create(line)
                        self.state = 'open'
                        self.env.cr.commit()
                except IndexError:
                    pass
                time.sleep(0.5)
            except Exception as ex:
                msg = traceback.format_exc()
                errors.append(ex)
                errors.append(msg)
                logger.error(ex)
                logger.error(msg)
                if logsio:
                    logsio.error(ex)
                    logsio.error(msg)

    def _execute(self, db_name, run, testrun_id, data, appendix):
        logsio = None
        env = None
        try:
            db_registry = registry(db_name)
            with db_registry.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                machine = env['cicd.machine'].browse(data['machine_id'])
                testrun = env['cicd.test.run'].browse(testrun_id)
                logsio = testrun.branch_id._get_new_logsio_instance(f"{appendix} - testrun")
                testrun = testrun.with_context(testrun=f"_testrun_{testrun.id}_{appendix}") # after logsio, so that logs io projectname is unchanged
                logsio.info("Running " + appendix)
                passed_prepare = False
                try:
                    started = arrow.get()
                    with testrun.prepare_run(machine, logsio) as shell:
                        logsio.info("Preparation done " + appendix)
                        data['run_lines'].append({
                            'state': 'success', 'ttype': 'log', 'name': f'preparation done: {appendix}',
                            'duration': (arrow.get() - started).total_seconds()
                            })
                        passed_prepare = True
                        run(shell, logsio, line_queue=data['run_lines'])
                except Exception as ex:
                    msg = traceback.format_exc()
                    if not passed_prepare:
                        duration = (arrow.get() - started).total_seconds()
                        data['run_lines'].append({
                            'run_id': testrun_id,
                            'started': arrow.get().datetime.strftime("%Y-%m-%d %H:%M:%S"),
                            'duration': duration,
                            'exc_info': msg,
                            'ttype': 'preparation',
                            'name': "Failed at preparation",
                            'state': 'failed',
                        })

        except Exception as ex:
            msg = traceback.format_exc()
            data['technical_errors'].append(ex)
            data['technical_errors'].append(msg)
            logger.error(ex)
            logger.error(msg)
            if logsio:
                logsio.error(ex)
                logsio.error(msg)

    def _compute_success_rate(self):
        for rec in self:
            lines = rec.mapped('line_ids')
            success_lines = len(lines.filtered(lambda x: x.state == 'success'))
            if lines and all(x.state == 'success' for x in lines):
                rec.state = 'success'
            else:
                rec.state = 'failed'
            if not self.line_ids or not success_lines:
                rec.success_rate = 0
            else:
                rec.success_rate = int(100 / float(len(self.line_ids)) * float(success_lines))

    @api.constrains('branch_ids')
    def _check_branches(self):
        for rec in self:
            if not rec.branch_ids:
                continue
            if not all(x.repo_id == rec.branch_ids[0].repo_id for x in rec.branch_ids):
                raise ValidationError("Branches must be of the same repository.")

    def _compute_name(self):
        for rec in self:
            date = rec.create_date.strftime("%Y-%m-%d %H:%M:%S")[:10]
            rec.name = f"{date} - {rec.branch_id.name}"

    @api.model
    def _get_ttypes(self, filtered):
        for x in self._fields['ttype'].selection:
            if filtered:
                if x[0] not in filtered:
                    continue
            yield x[0]

    def rerun(self):
        if self.branch_id.state not in ['testable', 'tested', 'dev']:
            raise ValidationError(_("State of branch does not allow a repeated test run"))
        self = self.sudo()
        self.state = 'open' # regular cronjob makes task for that

    def _run_create_empty_db(self, shell, task, logsio):
        self._generic_run(
            shell, logsio, [None], 
            'emptydb',
            lambda item: self.branch_id._create_empty_db(shell, task, logsio),
        )

    def _run_update_db(self, shell, logsio, line_queue, **kwargs):

        def _x(item):
            logsio.info(f"Restoring {self.branch_id.dump_id.name}")

            shell.odoo('-f', 'restore', 'odoo-db', self.branch_id.dump_id.name)
            self._wait_for_postgres(shell)
            shell.odoo('update')
            self._wait_for_postgres(shell)

        self._generic_run(
            shell, logsio, [None], 
            'migration', _x, line_queue,
        )

    def _run_robot_tests(self, shell, logsio, line_queue, **kwargs):
        files = shell.odoo('list-robot-test-files').output.strip()
        files = list(filter(bool, files.split("!!!")[1].split("\n")))

        shell.odoo('build')
        def _x(item):
            shell.odoo("snap", "restore", shell.project_name)
            self._wait_for_postgres(shell)
            shell.odoo('robot', item)

        self._generic_run(
            shell, logsio, files, 
            'robottest', _x, line_queue,
        )

    def _run_unit_tests(self, shell, logsio, line_queue, **kwargs):
        files = shell.odoo('list-unit-test-files').output.strip()
        files = list(filter(bool, files.split("!!!")[1].split("\n")))

        shell.odoo("snap", "restore", shell.project_name)
        self._wait_for_postgres(shell)

        self._generic_run(
            shell, logsio, files, 
            'unittest',
            lambda item: shell.odoo('unittest', item),
            line_queue
        )

    def _generic_run(self, shell, logsio, todo, ttype, execute_run, line_queue):
        for item in todo:
            line_queue.append({
                'name': f"Starting: {item}",
                'ttype': ttype, 
                'run_id': self.id,
                'started': arrow.get().datetime.strftime("%Y-%m-%d %H:%M:%S"),
                'state': 'success',
            })
            started = arrow.get()
            data = {
                'name': item or '',
                'ttype': ttype, 
                'run_id': self.id,
                'started': arrow.get().datetime.strftime("%Y-%m-%d %H:%M:%S"),
            }
            try:
                logsio.info(f"Running {item}")
                execute_run(item)
            except Exception as ex:
                msg = traceback.format_exc()
                logsio.error(f"Error happened: {msg}")
                data['state'] = 'failed'
                data['exc_info'] = msg
            else:
                data['state'] = 'success'
            end = arrow.get()
            data['duration'] = (end - started).total_seconds()
            line_queue.append(data)

class CicdTestRun(models.Model):
    _name = 'cicd.test.run.line'
    _order = 'started'

    ttype = fields.Selection([
        ('preparation', "Preparation"),
        ('unittest', 'Unit-Test'),
        ('robottest', 'Robot-Test'),
        ('migration', 'Migration'),
        ('emptydb', 'Migration'),
        ('log', "Log-Note"),
    ], string="Category")
    name = fields.Char("Name")
    run_id = fields.Many2one('cicd.test.run', string="Run")
    exc_info = fields.Text("Exception Info")
    duration =  fields.Integer("Duration")
    state = fields.Selection([
        ('open', 'Open'),
        ('success', 'Success'),
        ('failed', 'Failed'),
    ], default='open', required=True)
    started = fields.Datetime("Started")

    def open_form(self):
        return {
            'name': self.name,
            'view_type': 'form',
            'res_model': self._name,
            'res_id': self.id,
            'views': [(False, 'form')],
            'type': 'ir.actions.act_window',
            'target': 'current',
        }