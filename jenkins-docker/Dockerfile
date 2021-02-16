FROM bitnami/jenkins:2-debian-10
USER root
ENV DOCKERVERSION=20.10.3
RUN curl -fsSLO https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKERVERSION}.tgz \
  && tar xzvf docker-${DOCKERVERSION}.tgz --strip 1 \
                 -C /usr/local/bin docker/docker \
  && rm docker-${DOCKERVERSION}.tgz
RUN install_packages python3 python3-pip python3-setuptools git
RUN pip3 install pip --upgrade
RUN pip3 install docker-compose arrow humanize inquirer psycopg2-binary GitPython click jira
# https://github.com/bitnami/bitnami-docker-jenkins/issues/60
RUN echo "jenkins:x:$(id -g):" >> /etc/groups
RUN echo "jenkins:x:1001:$(id -g):Jenkins:$JENKINS_HOME:/bin/false" >> /etc/passwd

RUN install_packages rsync
USER 1001
