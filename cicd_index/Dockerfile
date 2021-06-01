FROM ubuntu:20.04
<<<<<<< HEAD
RUN apt update && DEBIAN_FRONTEND="noninteractive" apt install -y curl apt-transport-https ca-certificates curl gnupg2 software-properties-common libpq-dev rsync git iproute2 sockstat
=======
ENV FLASK_RUN_HOST=0.0.0.0
RUN apt update && DEBIAN_FRONTEND="noninteractive" apt install -y curl apt-transport-https ca-certificates curl gnupg2 software-properties-common libpq-dev git rsync
>>>>>>> 3ef00bc68d8c3f07584dbd0766284068bea3e243
RUN curl -fsSL https://download.docker.com/linux/ubuntu/gpg | apt-key add -
RUN add-apt-repository "deb [arch=$(dpkg --print-architecture)] https://download.docker.com/linux/ubuntu focal stable"
RUN apt update && DEBIAN_FRONTEND="noninteractive" apt install -y python3-pip docker-ce-cli
RUN mkdir -p /root/.ssh/socket
ADD index_ident /root/.ssh/id_rsa
RUN chmod 400 /root/.ssh/id_rsa && chmod 500 /root/.ssh

WORKDIR /root
COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt
EXPOSE 5000
ADD ssh_config /root/.ssh/config
<<<<<<< HEAD
RUN pip install waitress
WORKDIR /
CMD ["waitress-serve", "--port=5000", "--call" , "app1:create_app"]
=======
CMD ["flask", "run"]
WORKDIR /
>>>>>>> 3ef00bc68d8c3f07584dbd0766284068bea3e243
