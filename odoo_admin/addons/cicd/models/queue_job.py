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

    @api.model
    def prefix(self, job_uuid, prefix):
        if not job_uuid:
            return
        queuejob = self.sudo().search([('uuid', '=', job_uuid)])
        self.env.cr.execute("update queue_job set name=%s where id =%s", (
            f"{prefix}: {queuejob.name}",
            queuejob.id,
        ))
        return queuejob