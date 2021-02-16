#!/bin/bash
docker rm -f $(docker ps -f name=cicd_index -a -q)
docker-compose build cicd_index
docker-compose run --name cicd_index --rm --service-ports cicd_index flask run
