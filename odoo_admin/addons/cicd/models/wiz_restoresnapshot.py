from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError


class RestoreSnapshot(models.TransientModel):
    _name = 'cicd.wiz.restore_snapshot'

    machine_id = fields.Many2one('cicd.machine', required=True)
    snapshot_ids = fields.Many2many(
        'cicd.wiz.restore_snapshot.snapshot',
        'cicd_wiz_restore_snapshot_lines',
    )
    snapshot_id = fields.Many2one(
        'cicd.wiz.restore_snapshot.snapshot', string="Snapshot", required=True)
    name = fields.Char("Name", required=True)

    @api.model
    def default_get(self, fields):
        res = super().default_get(fields)
        res['snapshot_ids'] = self._get_snapshots()
        return res

    def restore_snapshot(self):
        with self.machine_id.shell() as shell:
            shell.odoo("snap", "restore", self.snapshot_id.name)
        return {'type': 'ir.actions.act_window_close'}

    @api.model
    def _get_snapshots(self):
        with self.machine_id.shell() as shell:
            snapshots = shell.odoo(
                "snap", "list")['stdout'].strip().splitlines()
            self.snapshot_ids.unlink()
            for shot in snapshots:
                self.snapshot_ids = [[0, 0, {
                    'name': shot,
                }]]


class RestoreSnapshotLine(models.TransientModel):
    _name = 'cicd.wiz.restore_snapshot.snapshot'

    name = fields.Char("Name", required=True)