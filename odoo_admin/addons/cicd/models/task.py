import arrow
import traceback
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo import registry
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from . import pg_advisory_lock
from contextlib import contextmanager
from ..tools.logsio_writer import LogsIOWriter

class Task(models.Model):
    _name = 'cicd.task'
    _order = 'date desc'

    model = fields.Char("Model")
    res_id = fields.Integer("ID")
    display_name = fields.Char(compute="_compute_display_name")
    machine_id = fields.Many2one('cicd.machine', string="Machine", readonly=True)
    branch_id = fields.Many2one('cicd.git.branch', string="Branch")
    name = fields.Char("Name")
    date = fields.Datetime("Date", default=lambda self: fields.Datetime.now(), readonly=True)
    state = fields.Selection([
        ('new', 'New'),
        ('done', 'Done'),
        ('failed', 'Failed'),
    ], required=True, default='new')
    log = fields.Text("Log", readonly=True)
    error = fields.Text("Exception", readonly=True)
    dump_used = fields.Char("Dump used", readonly=True)
    duration = fields.Integer("Duration [s]", readonly=True)
    commit_id = fields.Many2one("cicd.git.commit", string="Commit", readonly=True)

    def _compute_display_name(self):
        for rec in self:
            name = rec.name
            name = name.replace("obj.", "")
            if name.startswith("_"):
                name = name[1:]
            name = name.split("(")[0]
            rec.display_name = name

    def _get_new_logsio_instance(self):
        self.ensure_one()
        rolling_file = LogsIOWriter(f"{self.branch_id.name}", f'{self.id} - {self.name}')
        rolling_file.write_text(f"Started: {arrow.get()}")
        return rolling_file

    @contextmanager
    def _get_env(self, new_one):
        if not new_one:
            yield self
            return
        
        db_registry = registry(self.env.cr.dbname)
        with api.Environment.manage(), db_registry.cursor() as cr:
            env = api.Environment(cr, self.env.user.id, {})
            self = self.with_env(env).sudo()
            yield self

    def perform(self, now=False):
        started = arrow.get()
        self.ensure_one()
        self2 = self.sudo()
        # try nicht unbedingt notwendig; bei __exit__ wird ein close aufgerufen
        db_registry = registry(self.env.cr.dbname)
        with self._get_env(new_one=not now) as self:
            pg_advisory_lock(self.env.cr, f"performat_task_{self.branch_id.id}")

            try:
                logsio = self._get_new_logsio_instance()

                dest_folder = self.machine_id._get_volume('source') / self.branch_id.name
                with self.machine_id._shellexec(dest_folder, logsio=logsio) as shell:
                    self.branch_id.repo_id._get_main_repo(
                        destination_folder=dest_folder
                        )
                    obj = self.env[self.model].sudo().browse(self.res_id)
                    sha = shell.X(["git", "log", "-n1", "--format=%H"]).output.strip()
                    commit = self.branch_id.commit_ids.filtered(lambda x: x.name == sha)
                    if not commit:
                        raise ValidationError(f"Commit {sha} not found in branch.")
                    self.commit_id = commit
                    # get current commit
                    args = {
                        'task': self,
                        'logsio': logsio,
                        'shell': shell,
                        }
                    exec('obj.' + self.name + "(**args)", {'obj': obj, 'args': args})

                self.log = '\n'.join(logsio.lines)

            except Exception as ex:
                msg = traceback.format_exc()
                self.state = 'failed'
                self.error = msg

            else:
                self.state = 'done'

            duration = (arrow.get() - started).total_seconds()
            self.duration = duration

    def _cron_run(self):
        for task in self.search([
            ('state', '=', 'new')
        ]):
            task.perform()

    def _make_cron(self, uuid, object, method, active):
        object.ensure_one()
        key = f"{uuid}_{object._name}_{object.id}"
        crons = self.env['ir.cron'].with_context(active_test=False).search([('name', '=', key)], limit=1)
        if not crons:
            crons = crons.create({
                'name': key,
                'model_id': self.env['ir.model'].search([('model', '=', object._name)]).id,
                'interval_number': 1,
                'numbercall': -1,
                'interval_type': 'minutes',
                'code': f"env['{object._name}'].browse({object.id}).{method}()"
            })
        if crons.active != active:
            crons.active = active