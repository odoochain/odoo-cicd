import sys
import base64
import os
from pathlib import Path
from copy import deepcopy
from robot.api import logger
from robot.libraries.BuiltIn import BuiltIn

DEFAULT_LANG = 'en_US'

# def params(func):
#     def wrap(*args, **kwargs):
#         result = func(*args, **kwargs)
#         return result

#     return params



def convert_args(method):
    def _convert_fields(fields):
        if isinstance(fields, str):
            fields = fields.split(",")
        return fields

    def _convert_ids(ids):
        if isinstance(ids, str):
            ids = ids.split(",")
        if isinstance(ids, list):
            ids = list(map(int, ids))
        if ids and isinstance(ids, int):
            ids = [ids]
        return ids

    def wrapper(*args, **kwargs):
        if 'fields' in kwargs:
            kwargs['fields'] = _convert_fields(kwargs['fields'])
        if 'ids' in kwargs:
            kwargs['ids'] = _convert_ids(kwargs['ids'])
        if 'id' in kwargs and kwargs['id']:
            kwargs['id'] = _convert_ids(kwargs['id'])[0]
        result = method(*args, **kwargs)

        return result

    return wrapper


class odoo(object):

    def technical_testname(self):
        testname = BuiltIn().get_variable_value("${TEST NAME}")
        if not testname:
            testname = BuiltIn().get_variable_value("${SUITE NAME}")
        testname = testname.lower().replace(" ", "_")
        return testname

    def _get_context(self, default_values, lang):
        res = dict(deepcopy(default_values or {}))  # transform robot dictionary
        res['lang'] = lang or DEFAULT_LANG
        return res

    def get_conn(self, host, dbname, user, pwd):
        from odoo_rpc_client import Client
        ssl = host.startswith('https')
        host = host.split("://", 1)[-1]
        if ":" in host:
            host, port = host.split(":")
            port = int(port)
        else:
            port = 443 if ssl else 80
        logger.debug(f"Connection to odoo with {host} {dbname} user: {user} pw: {pwd}")
        db = Client(
            host=host,
            dbname=dbname or 'odoo',
            user=user,
            pwd=pwd,
            port=80,
        )
        return db

    def rpc_client_search(self, host, dbname, user, pwd, model, domain, limit, order, count=False, lang=DEFAULT_LANG, context=None):
        db = self.get_conn(host, dbname, user, pwd)
        context = self._get_context(context, lang)
        limit = int(limit) if limit else None
        domain = eval(domain)
        logger.debug(f"Searching for records with domain {domain} {type(domain)}")
        obj = db[model]
        return obj.search(domain, count=count, limit=limit, order=order, context=context)

    def rpc_client_search_records(self, host, dbname, user, pwd, model, domain, limit, order, count=False, lang=DEFAULT_LANG, context=None):
        db = self.get_conn(host, dbname, user, pwd)
        context = self._get_context(context, lang)
        limit = int(limit) if limit else None
        domain = eval(domain)
        logger.debug(f"Searching for records with domain {domain} {type(domain)}")
        obj = db[model]
        res = obj.search_records(domain, count=count, limit=limit, order=order, context=context)
        return res

    @convert_args
    def rpc_client_search_read_records(self, host, dbname, user, pwd, model, domain, fields, limit, order, count=False, lang=DEFAULT_LANG, context=None):
        db = self.get_conn(host, dbname, user, pwd)
        context = self._get_context(context, lang)
        limit = int(limit) if limit else None
        domain = eval(domain)
        logger.debug(f"Searching for records with domain {domain} {type(domain)}")
        obj = db[model]
        res = obj.search_records(domain, count=count, limit=limit, order=order, context=context)
        res = obj.read(res, fields)
        return res

    @convert_args
    def rpc_client_read(self, host, dbname, user, pwd, model, ids, fields=[], lang=DEFAULT_LANG, context=None):
        context = self._get_context(context, lang)
        db = self.get_conn(host, dbname, user, pwd)
        obj = db[model]
        return obj.read(ids, fields=fields, context=context)

    @convert_args
    def rpc_client_write(self, host, dbname, user, pwd, model, ids, values, lang=DEFAULT_LANG, context=None):
        context = self._get_context(context, lang)
        db = self.get_conn(host, dbname, user, pwd)
        obj = db[model]
        return obj.write(ids, values, context=context)

    @convert_args
    def rpc_client_execute(
        self, host, dbname, user, pwd, model, ids=None, method=None,
        params=[], kwparams={}, context=None, lang=DEFAULT_LANG
    ):
        context = self._get_context(context, lang)
        db = self.get_conn(host, dbname, user, pwd)
        obj = db[model]
        kwparams['context'] = context
        if not ids:
            # model functions
            return getattr(obj, method)(*params, **kwparams)
        else:
            return getattr(obj, method)(ids, *params, **kwparams)

    def rpc_client_create(self, host, dbname, user, pwd, model, values, context=None, lang=DEFAULT_LANG):
        context = self._get_context(context, lang)
        db = self.get_conn(host, dbname, user, pwd)
        obj = db[model]
        res = obj.create(values)
        return res

    def rpc_client_ref_id(self, host, dbname, user, pwd, xml_id, context=None, lang=DEFAULT_LANG):
        context = self._get_context(context, lang)
        obj = self.rpc_client_ref(host, dbname, user, pwd, xml_id)
        return obj.id

    def rpc_client_ref(self, host, dbname, user, pwd, xml_id):
        xml_id = xml_id.lower()
        db = self.get_conn(host, dbname, user, pwd)
        res = db.ref(xml_id)
        return res

    @convert_args
    def rpc_client_get_field(self, host, dbname, user, pwd, model, id, field, context=None, lang=DEFAULT_LANG):
        object_informations = self.rpc_client_read(host, dbname, user, pwd, model, [id], [field], context=context, lang=lang)
        if object_informations:
            object_information = object_informations[0]
            return object_information[field]
        else:
            return False

    def put_file(self, host, dbname, user, pwd, file_path, dest_path_on_odoo_container):
        file_path = Path(file_path).absolute()
        logger.debug(f"Putting file content from {file_path} to {dest_path_on_odoo_container}")
        content = Path(file_path).read_bytes()
        content = base64.encodebytes(content).decode('utf-8')

        db = self.get_conn(host, dbname, user, pwd)
        obj = db['robot.data.loader']
        return obj.put_file(content, dest_path_on_odoo_container)

    def load_file(self, host, dbname, user, pwd, filepath, module_name):
        filepath = Path(filepath).absolute()
        logger.debug(f"FilePath: {filepath}, cwd: {os.getcwd()}")
        db = self.get_conn(host, dbname, user, pwd)
        obj = db['robot.data.loader']
        filepath = Path(filepath)
        content = Path(filepath).read_text()
        suffix = filepath.suffix

        # replace some environment variables:
        test_name = self.technical_testname()
        content = content.replace("${CURRENT_TEST}", test_name)

        module_name = module_name.lower()

        # will make problems if paths not matching on remote sides and local if used at put file
        # for c in ";\\';\"?-":
        #     module_name = module_name.replace(c, "_")
        obj.load_data(content, suffix, module_name, filename=filepath.name)

    def exec_sql(self, host, dbname, user, pwd, sql):
        db = self.get_conn(host, dbname, user, pwd)
        obj = db['robot.data.loader']
        return obj.execute_sql(sql)
