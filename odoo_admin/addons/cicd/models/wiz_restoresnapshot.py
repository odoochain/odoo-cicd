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
        'cicd.wiz.restore_snapshot.snapshot', string="Snapshot", required=True)

    @api.model
    def default_get(self, fields):
        res = super().default_get(fields)
        res['snapshot_ids'] = list(self._get_snapshots())
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
            shell.odoo("snap", "restore", self.snapshot_id.sudo().name)
        return {'type': 'ir.actions.act_window_close'}

    @api.model
    def _get_snapshots(self):
        breakpoint()
        with self._get_branch().shell(logs_title="list_snapshots") as shell:
            snapshots = shell.odoo(
                "snap", "list")['stdout'].strip().splitlines()
            self.snapshot_ids.unlink()
            names = set()
            for shot in snapshots[2:]:
                names.add(shot.split(" ")[0])
            for name in names:
                snapshot = self.snapshot_ids.create({'name': name})
                yield snapshot.id


class RestoreSnapshotLine(models.TransientModel):
    _name = 'cicd.wiz.restore_snapshot.snapshot'

    wiz_id = fields.Many2one("cicd.wiz.restore_snapshot", required=True)
    name = fields.Char("Name", required=True)