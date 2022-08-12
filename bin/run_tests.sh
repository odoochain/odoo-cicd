#!/bin/bash
./cicd -f db reset
./cicd update
./cicd dev-env set-password-all-users 1
./cicd robot tests_all
