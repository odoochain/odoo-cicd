# CICD for odoo projects

- uses https://git.itewimmer.de/odoo/framework
- makes instances for every branch

## Configuration

```.env```

- PASSWD: if not set, then everybody is admin otherwise login with "admin" and the password; create further users.

### Location of input dumps:

- use docker-compose.override.yml
- mount into /input_dumps/subdir1   etc. paths where to find input dumps

## Administration

  * Backup:
```
cd cicd-app
docker-compose exec cicd_postgres pg_dumpall -U cicd |gzip > /tmp/dump/cicd.sql
```

  * Restore:
```
docker-compose ps (grab name/id of postgres container)
gunzip /tmp/dump/cicd.sql | docker exec -i <container postgres name psql -U cicd -d postgres
```
