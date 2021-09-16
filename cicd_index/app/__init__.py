#TODO clean source not only in workspace
import time
import os
import subprocess
from flask import Flask
from flask_caching import Cache
from bson.json_util import dumps
import flask_login
import logging
from flask_login import login_required
from pymongo import MongoClient
from pathlib import Path



rolling_log_dir = Path("/tmp") / 'rolling_log'
rolling_log_dir.mkdir(exist_ok=True)

login_manager = flask_login.LoginManager()


logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("paramiko").setLevel(logging.WARNING)
logging.getLogger("paramiko.transport").setLevel(logging.WARNING)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

"""
                     MONGO CONNECTION                                  
"""

mongoclient = MongoClient(
    os.environ["MONGO_HOST"],
    int(os.environ['MONGO_PORT']),
    username=os.environ['MONGO_USERNAME'],
    password=os.environ['MONGO_PASSWORD'],
    connectTimeoutMS=20000, socketTimeoutMS=20000, serverSelectionTimeoutMS=20000,
)
db = mongoclient.get_database('cicd_sites')
"""
                     HOST IP
"""
host_ip = '.'.join(subprocess.check_output(["/usr/bin/hostname", "-I"]).decode('utf-8').strip().split(".")[:3]) + '.1'

"""
                     CONSTANTS
"""

MAIN_FOLDER_NAME = '_main'

"""
                     LOGGING SETUP                                     
"""
FORMAT = '[%(levelname)s] %(name) -12s %(asctime)s %(message)s'
logging.basicConfig(format=FORMAT)
logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger('')  # root handler
logger.info(f"Host IP: {host_ip}")

"""
                     APP SETUP                                         
"""
from .app_utils import cronjob_builder
from .app_utils import cronjob_usage
from .app_utils import cronjob_docker
from .app_utils import cronjob_fetch_git
from .app_utils import cronjob_backup

if os.getenv("CICD_CRONJOBS") == "1":

    cronjob_builder.start()
    cronjob_docker.start()
    cronjob_fetch_git.start()
    cronjob_usage.start()
    cronjob_backup.start()

    while True:
        time.sleep(1000)

app = None
cache = None

def create_app():
    global app
    global cache
    app = Flask(
        __name__,
        static_url_path='/static', 
        static_folder='templates/static'
    )
    app.config.from_mapping({
        # "DEBUG": True, 
        "CACHE_TYPE": "SimpleCache",
        "CACHE_DEFAULT_TIMEOUT": 300
    })
    cache = Cache(app)
    app.secret_key = 'asajdkasj24242184*$@'
    from .app_utils.tools import JSONEncoder
    app.json_encoder = JSONEncoder
    login_manager.init_app(app)
    from .app_utils import auth
    from .app_utils import web_application
    from .app_utils import logs
    from .app_utils import web_user_admin
    from .app_utils import web_instance_control
    from .app_utils import web_app_settings
    from .app_utils.tools import JSONEncoder
    from . import app_utils
    return app