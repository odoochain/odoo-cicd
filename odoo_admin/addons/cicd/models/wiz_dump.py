from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
class MakeDump(models.Model):
    _name = 'cicd.wiz.dump'

    ttype = fields.Selection([
        ('backup', "Backup"),
        ('restore', "Restore")
    ], required=True)
    branch_id = fields.Many2one('cicd.git.branch', string="Branch", required=True)
    machine_id = fields.Many2one('cicd.machine', string="Machine", required=True)
    volume_id = fields.Many2one(
        'cicd.volume', domain="[('machine_id', '=', machine_id)]", required=True)
    filename = fields.Char("Filename")
    dump_id = fields.Many2one('cicd.dump', string="Dump")

    def ok(self):
        if self.ttype == 'backup':
            assert self.filename
            self.branch_id._make_task(
                "_dump", now=False, volume=self.volume_id.id,
                filename=self.filename)

        elif self.ttype == 'restore':
            assert self.dump_id
            self.branch_id._make_task(
                "_restore_dump", now=False, dump=self.dump_id.id)

        else:
            raise NotImplementedError()

        self.branch_id.