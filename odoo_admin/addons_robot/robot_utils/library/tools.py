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


class tools(object):

    def _odoo(self, server, db, username, password):
        """
        I'm sitting here xmlrpc.client.
        :return: None
        """
        common = xmlrpc.client.ServerProxy(f'{server}/xmlrpc/2/common')
        uid = common.authenticate(db, username, password, {})
        odoo = xmlrpc.client.ServerProxy(f'{server}/xmlrpc/2/object')
        return odoo, uid

    def execute_sql(self, server, db, user, password, sql, context=None):
        odoo, uid = self._odoo(server, db, user, password)
        odoo.execute_kw(db, uid, password, 'robot.data.loader', 'execute_sql', [sql])
        return True

    def get_res_id(self, server, db, user, password, model, module, name):
        odoo, uid = self._odoo(server, db, user, password)
        ir_model_obj = odoo.execute_kw(
            db, uid, password, 'ir.model.data', 'search_read',
            [[['model', '=', model], ['module', '=', module], ['name', '=', name]]],
            {'fields': ['res_id', ]}
            )
        if not ir_model_obj:
            return False
        return ir_model_obj[0]['res_id']

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
        return self.get_res_id(server, db, user, password, model='ir.ui.menu', module=module, name=name)

    def get_button_res_id(self, server, db, user, password, model, module, name):
        return self.get_res_id(server, db, user, password, model=model, module=module, name=name)
