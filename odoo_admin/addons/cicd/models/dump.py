from odoo import _, api, fields, models
import arrow
import logging
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT as DTF

logger = logging.getLogger(__name__)


class Dump(models.Model):
    _inherit = ["cicd.mixin.size"]
    _name = "cicd.dump"
    _order = "date_modified desc"

    active = fields.Boolean("Active", default=True)
    name = fields.Char("Name", required=True, readonly=True)
    machine_id = fields.Many2one(
        "cicd.machine", string="Machine", required=True, readonly=True
    )
    date_modified = fields.Datetime("Date Modified", readonly=True)
    # volume_id = fields.Many2one('cid.machine.volume', string="Volume", compute="_compute_volume") compute error at unlink; perhaps of test prefetch fields base override - perhaps

    @property
    def volume_id(self):
        self.ensure_one()
        volumes = self.env["cicd.machine.volume"].search(
            [
                ("machine_id", "=", self.machine_id.id),
                ("name", "=like", f"{self.name}%"),
            ],
            limit=1,
        )
        return volumes

    def download(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": f"/download/dump/{self.id}",
            "target": "new",
        }

    def unlink(self):
        if not self.env.context.get("dump_no_file_delete", False):
            for rec in self:
                volume = rec.volume_id
                if volume and volume.ttype == "dumps":
                    with rec.machine_id._shell() as shell:
                        shell.rm(rec.name)

        return super().unlink()

    @api.constrains("name")
    def _check_name(self):
        for rec in self:
            while rec.name.endswith("/"):
                rec.name = rec.name[:-1]

    def _update_dumps(self, machine):
        with machine._shell() as shell:
            self.env.cr.commit()

            for volume in machine.volume_ids.filtered(
                lambda x: x.ttype in ["dumps", "dumps_in"]
            ):
                volname = volume.name or ""
                self.env.cr.commit()

                splitter = "_____SPLIT_______"
                if not volname.endswith("/"):
                    volname += "/"
                try:
                    files = (
                        shell.X(
                            [
                                "find",
                                volname,
                                "-maxdepth",
                                "1",
                                "-printf",
                                f"%f{splitter}%TY%Tm%Td %TH%TM%TS{splitter}%s\\n",
                            ]
                        )["stdout"]
                        .strip()
                        .splitlines()
                    )
                except shell.TimeoutConnection:
                    logger.error("Timeout finding files.", exc_info=True)
                    continue

                Files = {}
                for line in files:
                    filename, date, size = line.split(splitter)
                    if filename.endswith("/"):
                        continue
                    date = arrow.get(date[:15])
                    path = volname + filename
                    Files[path] = {
                        "date": date.strftime(DTF),
                        "size": int(size),
                    }
                    del path, date, filename, size, line

                dumps = None
                for filepath, file in Files.items():

                    dumps = (
                        self.sudo()
                        .with_context(active_test=False, prefetch_fields=False)
                        .search(
                            [("name", "=", filepath), ("machine_id", "=", machine.id)],
                            order='id asc',
                        )
                    )
                    if not dumps:
                        dumps = dumps.sudo().create(
                            {
                                "name": filepath,
                                "machine_id": machine.id,
                            }
                        )

                    if len(dumps) > 1:
                        dumps[1:].unlink()
                        dumps = dumps[0]

                    dumps.ensure_one()
                    if (
                        not dumps.date_modified
                        or dumps.date_modified.strftime(DTF) != file["date"]
                    ):
                        dumps.date_modified = file["date"]
                    if dumps.size != file["size"]:
                        dumps.size = file["size"]
                    self.env.cr.commit()

                if not dumps:
                    continue
                for dump in dumps.search(
                    [
                        ("name", "like", volname),
                        ("machine_id", "=", machine.id),
                    ]
                ):
                    if dump.name.startswith(volname):
                        if dump.name not in Files:
                            dump.with_context(dump_no_file_delete=True).unlink()
                            self.env.cr.commit()
