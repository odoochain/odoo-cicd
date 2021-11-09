from odoo import registry
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
class GitBranch(models.Model):
    _name = 'cicd.git.branch'

    machine_id = fields.Many2one('cicd.machine', string="Machine", compute="_compute_machine")
    name = fields.Char("Git Branch", required=True)
    date_registered = fields.Datetime("Date registered")
    date = fields.Datetime("Date")
    repo_id = fields.Many2one('cicd.git.repo', string="Repository", required=True)
    active = fields.Boolean("Active", default=True)
    commit_ids = fields.Many2many('cicd.git.commit', string="Commits")
    task_ids = fields.One2many('cicd.task', 'branch_id', string="Tasks")
    state = fields.Selection([
        ('new', 'New'),
        ('approved', 'Approved'),
    ], string="State", default="new", required=True)
    build_state = fields.Selection([
        ('new', 'New'),
        ('fail', 'Failed'),
        ('building', 'Building'),
    ], default="new", compute="_compute_build_state")
    dump_id = fields.Many2one("cicd.dump", string="Dump")

    # autobackup = fields.Boolean("Autobackup")

    _sql_constraints = [
        ('name_repo_id_unique', "unique(name, repo_id)", _("Only one unique entry allowed.")),
    ]

    def _compute_machine(self):
        for rec in self:
            rec.machine_id = self.machine_id.sudo().search([], limit=1)

    @api.depends('task_ids', 'task_ids.state')
    def _compute_build_state(self):
        for rec in self:
            if 'new' in rec.mapped('task_ids.state'): 
                rec.build_state = 'building'
            else:
                if rec.task_ids and rec.task_ids[0].state == 'fail':
                    rec.build_state = 'failed'
                elif rec.task_ids and rec.task_ids[0].state == 'done':
                    rec.build_state = 'done'
                else:
                    rec.build_state = 'new'

    def reload_and_restart(self):
        self.ensure_one()
        self._make_task("obj._reload_and_restart()")

    def restore_dump(self):
        self.ensure_one()
        self._make_task("obj._restore_dump()")

    def _make_task(self, execute):
        if self.task_ids.filtered(lambda x: x.state == 'new' and x.name == execute):
            raise ValidationError(_("Task already exists. Not triggered again."))
        execute = execute.replace("()", "(task)")
        self.env['cicd.task'].sudo().create({
            'name': execute,
            'branch_id': self.id
        })
        return True

    @api.model
    def create(self, vals):
        res = super().create(vals)
        res.make_cron()
        return res

    def make_cron(self):
        self.ensure_one()
        self.env['cicd.task']._make_cron(
            'branches job', self, '_cron_execute_task', active=self.active
        )

    @api.constrains('active')
    def _onchange_active(self):
        for rec in self:
            rec.make_cron()
                

    def _cron_execute_task(self):
        self.ensure_one()
        tasks = self.task_ids.filtered(lambda x: x.state == 'new')
        if not tasks:
            return
        tasks = tasks[-1]
        tasks.perform()

    def _restore_dump(self, task):
        log = self.machine_id._execute_shell([
            'odoo', '--project-name', self.name, 'reload',
        ])
        log += self.machine_id._execute_shell([
            'odoo', '--project-name', self.name, 'build',
        ])
        log += self.machine_id._execute_shell([
            'odoo', '--project-name', self.name, 'down',
        ])
        log += self.machine_id._execute_shell([
            'odoo', '--project-name', self.name, '-f', 'restore', 'odoo-db', self.dump_id.name
        ])

    def _reload_and_restart(self, task):
        task.dump_used = self.dump_id.name
        log = self.machine_id._execute_shell([
            'odoo', '--project-name', self.name, 'reload',
        ])
        log += self.machine_id._execute_shell([
            'odoo', '--project-name', self.name, 'build',
        ])
        log += self.machine_id._execute_shell([
            'odoo', '--project-name', self.name, 'up', '-d',
        ])
        task.log = log
