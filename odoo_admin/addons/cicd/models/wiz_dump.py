from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError


class MakeDump(models.TransientModel):
    _name = 'cicd.wiz.dump'

    ttype = fields.Selection([
        ('backup', "Backup"),
        ('restore', "Restore")
    ], required=True)
    branch_id = fields.Many2one(
        'cicd.git.branch', string="Branch", required=True)
    machine_id = fields.Many2one(
        'cicd.machine', string="Machine", required=True)
    backup_volume_id = fields.Many2one(
        'cicd.machine.volume', domain="[('machine_id', '=', machine_id), ('ttype', 'in', ['dumps'])]", required=True)
    restore_volume_id = fields.Many2one(
        'cicd.machine.volume', domain="[('machine_id', '=', machine_id), ('ttype', 'in', ['dumps', 'dumps_in'])]", required=True)
    filename = fields.Char("Filename")
    dump_id = fields.Many2one('cicd.dump', string="Dump")

    def ok(self):
        if self.ttype == 'backup':
            assert self.filename
            self.branch_id._make_task(
                "_dump", now=False, volume=self.backup_volume_id.id,
                ignore_previous_tasks=True,
                filename=self.filename)

        elif self.ttype == 'restore':
            assert self.dump_id
            self.branch_id._make_task(
                "_restore_dump", now=False, dump=self.dump_id.id,
                ignore_previous_tasks=True,
                )

        else:
            raise NotImplementedError()
        return {'type': 'ir.actions.act_window_close'}