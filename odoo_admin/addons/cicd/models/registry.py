from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError


class Registry(models.Model):
    _name = 'cicd.registry'

    name = fields.Char("Name")
    host = fields.Char("Host", required=True)
    port = fields.Integer("Port", default=5000, required=True)
    username = fields.Char("Username")
    password = fields.Char("Password")
    path = fields.Char("Path", default="/myspace1", required=True)
    hub_url = fields.Char("HUB Url", compute="_compute_hub_url")
    hub_url_readonly = fields.Char(
        "HUB Url (readonly access)", compute="_compute_hub_url")
    username_readonly = fields.Char("Username Readonly Access")
    password_readonly = fields.Char("Password Readonly Access")

    @api.constrains("path")
    def _check_path(self):
        for rec in self:
            path = rec.path.strip()
            if not path.startswith("/"):
                path = "/" + path
            while path.endswith("/") and len(path) > 1:
                path = rec.path[:-1]

            if path != rec.path:
                rec.path = path

    def _check_passwordusername(self):
        for rec in self:
            for c in ":@":
                if c in (rec.password or '') + (rec.username or ''):
                    raise ValidationError((
                        f"Invalid Char: {c}"
                    ))

    @api.depends(
        "username",
        "password",
        "host",
        "port",
        "path",
    )
    def _compute_hub_url(self):
        for rec in self:
            username = rec.username
            pwd = rec.password
            username_ro = rec.username_readonly or username
            pwd_ro = rec.password_readonly or pwd

            rec.hub_url = (
                f"{username}:{pwd}@{rec.host}:{rec.port}{rec.path}"
            )
            rec.hub_url_readonly = (
                f"{username_ro}:{pwd_ro}@{rec.host}:{rec.port}{rec.path}"
            )
