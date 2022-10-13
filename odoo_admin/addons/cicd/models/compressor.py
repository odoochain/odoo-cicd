import re
from odoo import _, api, fields, models
import humanize
from odoo.exceptions import UserError, RedirectWarning, ValidationError


class Compressor(models.Model):
    _name = "cicd.compressor"

    source_volume_id = fields.Many2one(
        "cicd.machine.volume", string="Source Volume", required=True
    )
    regex = fields.Char("Regex", required=True, default=".*")
    active = fields.Boolean("Active", default=True)
    cronjob_id = fields.Many2one(
        "ir.cron",
        string="Cronjob",
        required=False,
        ondelete="cascade",
        readonly=True,
        copy=False,
    )
    repo_id = fields.Many2one("cicd.git.repo", related="branch_id.repo_id")
    repo_short = fields.Char(related="repo_id.short", string="Repo")
    machine_id = fields.Many2one("cicd.machine", related="repo_id.machine_id")
    anonymize = fields.Boolean("Anonymize", required=True)
    branch_id = fields.Many2one(
        "cicd.git.branch", string="Use Branch for compression", required=True
    )
    date_last_success = fields.Datetime("Date Last Success", readonly=True)
    exclude_tables = fields.Char("Exclude Tables (comma separated list)")
    last_input_size = fields.Integer("Last Input Size", readonly=True)
    last_input_size_human = fields.Char("Last Input Size", readonly=True)
    last_output_size = fields.Integer("Last Input Size", readonly=True)
    last_output_size_human = fields.Char("Last Output Size", readonly=True)
    last_log = fields.Text("Last Log")
    performance = fields.Integer("Performance", compute="_compute_numbers")
    output_ids = fields.One2many(
        "cicd.compressor.output", "compressor_id", string="Output"
    )
    timeout_hours = fields.Integer("Timeout Hours", default=24)

    def _ensure_cronjob(self):
        for rec in self:
            if rec.active and not rec.cronjob_id:
                model = self.env["ir.model"].sudo().search([("model", "=", self._name)])
                self.cronjob_id = (
                    self.env["ir.cron"]
                    .sudo()
                    .create(
                        {
                            "name": f"compressor {rec.id}",
                            "model_id": model.id,
                            "code": f"model.browse({rec.id})._start()",
                            "numbercall": -1,
                            "active": True,
                        }
                    )
                )

    def _start(self):
        breakpoint()
        self.ensure_one()
        if not self.active:
            return
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
                rec.performance = 100.0 - (
                    float(rec.last_output_size) / float(rec.last_input_size) * 100
                )
            else:
                rec.performance = 0
            rec.last_input_size_human = humanize.naturalsize(rec.last_input_size or 0)
            rec.last_output_size_human = humanize.naturalsize(rec.last_output_size or 0)

    def show_queuejobs(self):
        branch = f"{self.branch_id.repo_id.short}-{self.branch_id.name}"
        jobs = (
            self.env["queue.job"]
            .search([])
            .with_context(prefetch_fields=False)
            .filtered(lambda x: x.branch == branch)
        )

        return {
            "name": f"Compressor-Jobs of {self.branch_id.name}",
            "view_type": "form",
            "res_model": jobs._name,
            "domain": [("id", "in", jobs.ids)],
            "views": [(False, "tree"), (False, "form")],
            "type": "ir.actions.act_window",
            "target": "current",
        }

    def _get_latest_dump(self, logsio):
        self.ensure_one()

        logsio.info("Identifying latest dump")
        with self.source_volume_id.machine_id._shell(
            logsio=logsio, cwd=""
        ) as source_shell:
            output = (
                source_shell.X(["ls", "-tA", self.source_volume_id.name])["stdout"]
                .strip()
                .splitlines()
            )
            line = None
            if not output:
                raise Exception("No dump found")

            for line in output:
                if line in (".", ".."):
                    continue
                if line.startswith("."):
                    continue
                if re.findall(self.regex, line):
                    break
            else:
                logsio.info("No files found.")
                return
            return line.strip()
