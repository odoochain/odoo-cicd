from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError


class RestoreSnapshot(models.TransientModel):
    _name = 'cicd.wiz.restore_snapshot'

    branch_id = fields.Many2one('cicd.git.branch', required=True)
    snapshot_ids = fields.One2many(
        'cicd.wiz.restore_snapshot.snapshot',
        'wiz_id',
    )
    snapshot_id = fields.Many2one(
        'cicd.wiz.restore_snapshot.snapshot', string="Snapshot",
        required=False, domain="[('wiz_id', '=', id)]"
    )

    @api.model
    def create(self, vals):
        res = super().create(vals)
        res._update_snapshots()
        return res

    def _get_branch(self):
        if not self.exists():
            return self.env['cicd.git.branch'].browse(
                self.env.context['default_branch_id']
            )
        else:
            return self.branch_id

    def restore_snapshot(self):
        if not self.snapshot_id:
            raise ValidationError("Please choose a snapshot")
        with self._get_branch().shell(logs_title="restore_snapshot") as shell:
            shell.odoo("down")
            shell.odoo("snap", "restore", self.snapshot_id.sudo().name)
            shell.odoo("up", "-d")
        return {'type': 'ir.actions.act_window_close'}

    def _update_snapshots(self):
        breakpoint()
        with self._get_branch().shell(logs_title="list_snapshots") as shell:
            snapshots = shell.odoo(
                "snap", "list")['stdout'].strip().splitlines()
            names = set()
            for shot in snapshots[2:]:
                names.add(shot.split(" ")[0])
            for name in names:
                self.snapshot_ids = [[0, 0, {
                    'name': name,
                }]]

class RestoreSnapshotLine(models.TransientModel):
    _name = 'cicd.wiz.restore_snapshot.snapshot'

    wiz_id = fields.Many2one("cicd.wiz.restore_snapshot", required=True)
    name = fields.Char("Name", required=True)