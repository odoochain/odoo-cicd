#!/bin/bash
docker rm -f $(docker ps -f name=cicd_delegator -a -q)
# docker-compose build cicd_delegator
docker-compose run --name cicd_delegator --rm --service-ports cicd_delegator python ./run.py --port=80
