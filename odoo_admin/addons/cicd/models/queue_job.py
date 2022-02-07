import re
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from odoo.addons.queue_job.job import Job

class queuejob(models.Model):
    _inherit = 'queue.job'

    def run_now(self):
        for self in self:
            self.ensure_one()

            job = Job.load(self.env, self.uuid)

            job.set_started()
            job.store()
            job.perform()
            job.set_done()
            job.store()

    branch = fields.Char(compute="_compute_branch", store=False)

    def _compute_branch(self):
        for rec in self:
            b = False
            if rec.identity_key:
                re.findall(r'branch:([^:]*):', rec.identity_key)
            rec.branch = b