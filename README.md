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

If you get a strange SCRAM error message at authentication (https://stackoverflow.com/questions/62807717/how-can-i-solve-postgresql-scram-authentifcation-problem) then change the pg_hba.conf of the cicd_postgres as
follows:

```
# TYPE  DATABASE        USER            ADDRESS                 METHOD

# "local" is for Unix domain socket connections only
local   all             all                                     trust
# IPv4 local connections:
host    all             all             127.0.0.1/32            trust
# IPv6 local connections:
host    all             all             ::1/128                 trust
# Allow replication connections from localhost, by a user with the
# replication privilege.
local   replication     all                                     trust
host    replication     all             127.0.0.1/32            trust
host    replication     all             ::1/128                 trust

# INSERT this line to fix the scram error on M1 chips.
#host all all all scram-sha-256
host all all all trust
```