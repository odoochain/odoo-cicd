#!/bin/bash
./cicd robot tests/test_release.robot --param CICD_HOME=$( pwd ) --param ROBOTTEST_SSH_USER=$USER