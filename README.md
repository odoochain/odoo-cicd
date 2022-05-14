# ODOO CICD

- uses https://github.com/marcwimmer/wodoo
- every branch testable as feature
- workflow for deployment
- ticketsystem (jira.. ) integration
- notifications about releases, testruns


# Setup
## setup with wodoo, recommended settings

```
~/.cicd/settings
HUB_URL=.....
DOCKER_IMAGE_TAG=cicd
PROJECT_NAME=cicd
DBNAME=cicdadmin
RUN_PROXY_PUBLISHED=0
ODOO_QUEUEJOBS_CHANNELS=testruns:5,others:10
RUN_ODOO_QUEUEJOBS=1
RUN_ODOO_CRONJOBS=1
RESTART_CONTAINERS=1
ODOO_MAX_CRON_THREADS=10

CICD_NETWORK_NAME=cicd_net
CICD_BINDING=0.0.0.0:80
CICD_DB_HOST=172.16.130.156
CICD_DB_USER=cicd
CICD_DB_PASSWORD=cicd_is_cool
CICD_DB_PORT=5454
CICD_POSTGRES_VERSION=14.0
```

## M1 cicd postgres compatibilty
```
./cicd pghba-conf-wide-open --no-scram

```
