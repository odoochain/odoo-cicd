import tempfile
import base64
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError


class CicdExportExcel(models.TransientModel):
    _name = 'cicd.export.excel'

    branch_id = fields.Many2one(
        'cicd.git.branch', string="Branch", required=True)
    filecontent = fields.Binary("Filecontent")
    filename = fields.Char("Filename", compute="_compute_filename")
    sql = fields.Text("SQL")

    def _compute_filename(self):
        for rec in self:
            rec.filename = (
                f"{self.id}"
                ".xlsx"
            )

    def ok(self):
        with self.branch_id.shell("Excel Export") as shell:
            filename = tempfile.mktemp(suffix='.')
            shell.odoo(
                "excel",
                self.sql,
                "-f", filename
            )
            try:
                content = shell.get(filename)
                self.filecontent = base64.encodestring(content)
            finally:
                shell.remove(filename)

        return {
            'view_type': 'form',
            'res_model': self._name,
            'res_id': self.id,
            'views': [(False, 'form')],
            'type': 'ir.actions.act_window',
            'target': 'current',
        }
