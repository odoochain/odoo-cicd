from odoo import _, api, fields, models, SUPERUSER_ID
import humanize
from odoo.exceptions import UserError, RedirectWarning, ValidationError

class Compressor(models.Model):
    _name = 'cicd.compressor'

    source_volume_id = fields.Many2one('cicd.volume', string="Source Volume")
    regex = fields.Char("Regex", required=True, default=".*")
    active = fields.Boolean("Active", default=True)
    cronjob_id = fields.Many2one('ir.cron', string="Cronjob", required=True, ondelete="cascade")
    repo_id = fields.Many2one('cicd.git.repo', related="branch_id.repo_id")
    machine_id = fields.Many2one('cicd.machine', related="repo_id.machine_id")
    volume_id = fields.Many2one('cicd.volume', string="Output Volume", required=True, domain=[('machine_id', '=', machine_id)])
    output_filename = fields.Char("Output Filename", required=True)
    anonymize = fields.Boolean("Anonymize", required=True)
    branch_id = fields.Many2one('cicd.git.branch', string="Use Branch for compression", required=True)
    last_input_size = fields.Integer("Last Input Size")
    last_input_size_human = fields.Integer("Last Input Size")
    last_output_size = fields.Integer("Last Input Size")
    last_output_size_human = fields.Integer("Last Input Size")
    performance = fields.Integer("Performance", compute="_compute_numbers")

    def _ensure_cronjob(self):
        for rec in self:
            if rec.active and not rec.cronjob_id:
                model = self.env['ir.model'].sudo().search([('model', '=', self._name)])
                self.env['ir.cron'].sudo().create({
                    'name': self.name + " compressor",
                    'model_id': model.id,
                    'code': f'model.browse({rec.id})._start()'
                })

    def _start(self):
        self.ensure_one()
        self.branch_id._make_task("_compress", compress_job_id=self.id)


    @api.model
    def create(self, vals):
        res = super().create(vals)
        res._ensure_cronjob()
        return res

    def write(self, vals):
        res = super().write(vals)
        self._ensure_cronjob()
        return res

    @api.depends("last_input_size", "last_output_size")
    def _compute_numbers(self):
        for rec in self:
            if rec.last_input_size:
                rec.performance = 100.0 - (float(rec.last_output_size) / float(rec.last_input_size) * 100)
                rec.last_input_size_human = humanize.naturalsize(rec.last_input_size)
                rec.last_output_size_human = humanize.naturalsize(rec.last_output_size)