import os
from .. import db
from .. import app
import flask_login
from flask_login import login_required
from flask import render_template
import flask
from flask import jsonify
from flask import request
from bson import ObjectId
from .tools import _get_config, _set_config

@app.route("/data/app_settings", methods=["GET"])
def app_settings_get():
    result = {
        'concurrent_builds': _get_config('concurrent_builds', 5),
        'no_i18n': _get_config('no_i18n', True),
        'odoo_settings': _get_config('odoo_settings', ""),
    }
    return jsonify(result)

@app.route("/data/app_settings", methods=["POST"])
def app_settings_post():
    for k, v in request.form.items():

        if k in ['concurrent_builds']:
            v = int(v)
        if k in ['no_i18n']:
            v = v in ["1", "on", "True", True, 1]
        _set_config(k, v)
    return jsonify({'result': 'ok'})
