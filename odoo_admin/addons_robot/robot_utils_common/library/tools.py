from datetime import date
import arrow
from pathlib import Path
import json
import shutil
import os
import time
import uuid
import xmlrpc.client
from robot.api.deco import keyword
from robot.utils.dotdict import DotDict


class Encoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, DotDict):
            return dict(obj)
        return super().default(obj)


class tools(object):
    def _odoo(self, server, db, username, password):
        """
        I'm sitting here xmlrpc.client.
        :return: None
        """
        common = xmlrpc.client.ServerProxy(f"{server}/xmlrpc/2/common")
        uid = common.authenticate(db, username, password, {})
        odoo = xmlrpc.client.ServerProxy(f"{server}/xmlrpc/2/object")
        return odoo, uid

    def execute_sql(self, server, db, user, password, sql, context=None):
        odoo, uid = self._odoo(server, db, user, password)
        odoo.execute_kw(db, uid, password, "robot.data.loader", "execute_sql", [sql])
        return True

    def get_res_id(self, server, db, user, password, model, module, name):
        odoo, uid = self._odoo(server, db, user, password)
        ir_model_obj = odoo.execute_kw(
            db,
            uid,
            password,
            "ir.model.data",
            "search_read",
            [[["model", "=", model], ["module", "=", module], ["name", "=", name]]],
            {
                "fields": [
                    "res_id",
                ]
            },
        )
        if not ir_model_obj:
            return False
        return ir_model_obj[0]["res_id"]

    def make_same_passwords(self, server, db, user, password):
        odoo, uid = self._odoo(server, db, user, password)
        sql = (
            "update res_users set password = "
            "(select password from res_users where id = 1)"
        )
        odoo.execute_kw(db, uid, password, "robot.data.loader", "execute_sql", [sql])

    def do_get_guid(self):
        return str(uuid.uuid4())

    def get_current_date(self):
        return date.today()

    def get_now(self):
        return arrow.get().datetime

    def copy_file(self, source, destination):
        shutil.copy(source, destination)

    def get_json_content(self, filepath):
        return json.loads(Path(filepath).absolute().read_text())

    def set_dict_key(self, data, key, value):
        data[key] = value

    def get_menu_res_id(self, server, db, user, password, module, name):
        return self.get_res_id(
            server, db, user, password, model="ir.ui.menu", module=module, name=name
        )

    def get_button_res_id(self, server, db, user, password, model, module, name):
        return self.get_res_id(
            server, db, user, password, model=model, module=module, name=name
        )

    def internal_set_wait_marker(self, server, db, user, password, name):
        odoo, uid = self._odoo(server, db, "admin", password)
        marker_name = f"robot-marker{name}"

        exists = odoo.execute_kw(
            db,
            uid,
            password,
            "ir.config_parameter",
            "search_count",
            [[["key", "=", marker_name]]],
        )
        if not exists:
            odoo.execute_kw(
                db,
                uid,
                password,
                "ir.config_parameter",
                "create",
                [
                    {
                        "key": marker_name,
                        "value": "1",
                    },
                ],
            )

    def internal_wait_for_marker(self, server, db, user, password, name, timeout=120):
        odoo, uid = self._odoo(server, db, user, password)
        deadline = arrow.get().shift(seconds=timeout)
        marker_name = f"robot-marker{name}"
        while arrow.get() < deadline:
            if odoo.execute_kw(
                db,
                uid,
                password,
                "ir.config_parameter",
                "search",
                [[["key", "=", marker_name]]],
            ):
                break
            time.sleep(1)
        else:
            raise Exception("Timeout")

    def odoo_convert_to_dictionary(self, value):
        value = value or {}
        return json.loads(json.dumps(value, cls=Encoder))
