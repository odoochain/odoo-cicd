from odoo import registry
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
class GitBranch(models.Model):
    _name = 'cicd.git.branch'

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
        ('ready', 'Ready'),
        ('building', 'Building'),
    ], default="new")
    lock_building = fields.Datetime("Lock Building")

    # autobackup = fields.Boolean("Autobackup")

    _sql_constraints = [
        ('name_repo_id_unique', "unique(name, repo_id)", _("Only one unique entry allowed.")),
    ]

    def build(self):
        self.ensure_one()

        # check if building then dont touch
        # try nicht unbedingt notwendig; bei __exit__ wird ein close aufgerufen
        db_registry = registry(self.env.cr.dbname)
        with api.Environment.manage(), db_registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            branch2 = env[GitBranch._name].browse(self.id)
            branch2.lock_building = fields.Datetime.now()
            cr.commit()
        
        self.ensure_one()

    @api.model
    def create(self, vals):
        res = super().create(vals)
        self.env['cicd.task']._make_cron(res, active=True)
        return res

    @api.constrains('active')
    def _onchange_active(self):
        for rec in self:
            self.env['cicd.task']._make_cron(res, active=rec.active)
                