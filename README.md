# CICD for odoo projects

- uses https://git.itewimmer.de/odoo/framework
- makes instances for every branch

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

## Configuration .env file

- PASSWD: if not set, then everybody is admin otherwise login with "admin" and the password; create further users.

### Location of input dumps

- use docker-compose.override.yml
- mount into /input_dumps/subdir1   etc. paths where to find input dumps

## Administration

- Backup

```bash
cd cicd-app
docker-compose exec cicd_postgres pg_dumpall -U cicd |gzip > /tmp/dump/cicd.sql
```

- Restore

```bash
docker-compose ps (grab name/id of postgres container)
gunzip /tmp/dump/cicd.sql | docker exec -i <container postgres name psql -U cicd -d postgres
```


## Minimal Settings

# Recommended configuration for candidate branch
```bash
# put sha of git commit into final image
SHA_IN_DOCKER=1

```