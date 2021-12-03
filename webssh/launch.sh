#!/bin/bash
set -x
set -eux

service nginx start
service ssh start

set -r
wssh --address=0.0.0.0 --port=8080 --xsrf=False --origin='*' --xheaders=False --debug --wpintvl=3600
