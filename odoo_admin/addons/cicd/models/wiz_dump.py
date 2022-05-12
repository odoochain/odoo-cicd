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
        'cicd.machine.volume',
        domain=(
            "[('machine_id', '=', machine_id), "
            "('ttype', 'in', ['dumps'])]"
        ))
    filename = fields.Char("Filename")
    dump_id = fields.Many2one(
        'cicd.dump', string="Dump",
        domain="[('machine_id', '=', machine_id)]")

    @api.model
    def default_get(self, fields):
        res = super().default_get(fields)
        if res.get('machine_id') and not res.get('backup_volume_id'):
            machine = self.env['cicd.machine'].browse(res['machine_id'])
            if res['ttype'] == 'backup':
                volumes = machine.volume_ids.filtered(
                    lambda x: x.ttype in ['dumps'])
                if len(volumes) == 1:
                    res['backup_volume_id'] = volumes.id
        return res

    def do_dump(self):
        if self.ttype == 'backup':
            assert self.filename
            self.branch_id._make_task(
                "_dump", now=False, volume=self.backup_volume_id.id,
                filename=self.filename)

        elif self.ttype == 'restore':
            assert self.dump_id
            self.branch_id._make_task(
                "_restore_dump", now=False, dump=self.dump_id.id,
                )

        else:
            raise NotImplementedError()
        return {'type': 'ir.actions.act_window_close'}