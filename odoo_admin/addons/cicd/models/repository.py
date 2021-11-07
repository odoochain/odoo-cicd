import random
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from ..tools import lib_git_fetch
from . import pg_try_advisory_lock
from odoo.addons.queue_job.exception import (
    RetryableJobError,
    JobError,
)
class Repository(models.Model):
    _name = 'cicd.git.repo'

    name = fields.Char("URL", required=True)
    login_type = fields.Selection([
        ('username', 'Username'),
        ('key', 'Key'),
    ])
    key = fields.Text("Key")
    username = fields.Char("Username")
    password = fields.Char("Password")


    _sql_constraints = [
        ('name_unique', "unique(named)", _("Only one unique entry allowed.")),
    ]

    @api.model
    def _cron_fetch(self):
        for repo in self.search([]):
            lib_git_fetch._get_new_commits(self)

    def _lock_git(self): 

        def retry(lock):
            raise RetryableJobError(f'Could not acquire advisory lock (stock move line {lock})', seconds=random.randint(5, 15), ignore_retry=True)

        for rec in self:
            lock = rec.name
            if not pg_try_advisory_lock(self.env.cr, lock):
                retry(lock)