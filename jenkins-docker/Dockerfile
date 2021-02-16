FROM bitnami/jenkins:2-debian-10
USER root
# https://github.com/bitnami/bitnami-docker-jenkins/issues/60
RUN echo "jenkins:x:$(id -g):" >> /etc/groups
RUN echo "jenkins:x:1001:$(id -g):Jenkins:$JENKINS_HOME:/bin/false" >> /etc/passwd
USER 1001
