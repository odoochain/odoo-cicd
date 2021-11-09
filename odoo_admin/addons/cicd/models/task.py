import traceback
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo import registry
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from . import pg_advisory_lock
import threading

class Task(models.Model):
    _name = 'cicd.task'
    _order = 'date desc'

    branch_id = fields.Many2one('cicd.git.branch', string="Branch")
    name = fields.Char("Name")
    date = fields.Datetime("Date", default=lambda self: fields.Datetime.now())
    state = fields.Selection([
        ('new', 'New'),
        ('done', 'Done'),
        ('failed', 'Failed'),
    ], required=True, default='new')
    log = fields.Text("Log")
    error = fields.Text("Exception")
    dump_used = fields.Char("Dump used")

    def perform(self):
        self.ensure_one()
        self2 = self.sudo()
        # try nicht unbedingt notwendig; bei __exit__ wird ein close aufgerufen
        db_registry = registry(self.env.cr.dbname)
        with api.Environment.manage(), db_registry.cursor() as cr:
            env = api.Environment(cr, self.env.user.id, {})
            self = self.with_env(env).sudo()
        
            pg_advisory_lock(cr, f"performat_task_{self.branch_id.id}")
            try:
                exec(self.name, {'obj': self.branch_id, 'task': self})

            except Exception as ex:
                msg = traceback.format_exc()
                self2.state = 'failed'
                self2.error = msg

            else:
                self2.state = 'done'

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