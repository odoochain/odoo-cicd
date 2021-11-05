#!/bin/bash
docker-compose down adminodoo
docker-compose up adminodoo
sleep 3
docker-compose exec adminodoo -u cicd