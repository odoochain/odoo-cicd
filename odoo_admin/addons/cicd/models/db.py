from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
import humanize
class Database(models.Model):
    _name = 'cicd.database'

    size = fields.Integer("Size", compute="_compute_size")
    size_human = fields.Char("Size", compute="_compute_size")
    name = fields.Char("Name")

    def _compute_size(self):
        try:
            conn = _get_db_conn()
            try:
                cr = conn.cursor()
                cr.execute(f"select pg_database_size('{dbname}')")
                db_size = cr.fetchone()[0]
            finally:
                conn.close()
        except: db_size = 0
        return db_size

def _usages():
    while True:
        try:
            for site in db.sites.find({}):
                logger = LogsIOWriter(site['name'], 'usage-statistics')
                name = site['name']

                path = Path('/cicd_workspace') / name
                source_size = 0
                source_size_humanize = ""
                if path.exists():
                    source_size = round(sum(f.stat().st_size for f in path.glob('**/*') if f.is_file()), 0)
                    source_size_humanize = humanize.naturalsize(source_size)
                logger.info(f"Size source: {source_size_humanize }")

                settings = _get_instance_config(name)  # gibts nicht mehr anders machen
                dbname = settings.get('DBNAME', "")
                db_size = 0
                if dbname:
                    db_size = _get_db_size(dbname)
                logger.info(f"DB size: {db_size}")

                db.sites.update_one({'_id': site['_id']}, {'$set': {
                    'source_size': source_size,
                    'source_size_humanize': source_size_humanize,
                }}, upsert=False)
                logger.info(f"Usage collected for {site['name']}")

        except Exception as ex:
            logger.error(ex)

        finally:
            time.sleep(5)