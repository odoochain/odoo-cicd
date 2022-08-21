#!/bin/bash
CICD_USER=$(whoami)
CICD_HOME=$(pwd)

./cicd down -v
./cicd up -d
./cicd up -d postgres
sleep 5
./cicd -f db reset
./cicd update
./cicd dev-env set-password-all-users 1
./cicd up -d
sleep 10
./cicd robot tests_all \
	--param ROBOTTEST_SSH_USER=$CICD_USER  \
	--param CICD_HOME=$CICD_HOME
