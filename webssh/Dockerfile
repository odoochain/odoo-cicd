FROM ubuntu:20.04
CMD [ "bash", "/root/launch.sh" ]
RUN apt update && DEBIAN_FRONTEND="noninteractive" apt install -y --no-install-recommends \
    nginx \
    procps \
    python3-pip \
    python3 \
    python3-setuptools \
    ssh \
    apt-transport-https \
    ca-certificates \
    curl \
    gnupg-agent \
    software-properties-common \
    python3.8-dev \
    libpq-dev \
    python3.8 \
    python3-pip \
    build-essential \
    git
RUN curl -fsSL https://download.docker.com/linux/ubuntu/gpg | apt-key add - && \
add-apt-repository \
    "deb [arch=$(dpkg --print-architecture)] https://download.docker.com/linux/ubuntu \
    $(lsb_release -cs) \
    stable" && \
    apt-get update && \
    apt-get install -y docker-ce docker-ce-cli containerd.io && \
    pip install pip --upgrade & \
    pip install webssh

ADD launch.sh /root
RUN chmod a+x /root/launch.sh

RUN echo "root:odoo" | chpasswd root

COPY requirements.txt .
RUN pip3 install -r requirements.txt


RUN sed -i 's/\#PermitRootLogin\ prohibit-password/PermitRootLogin yes/g' /etc/ssh/sshd_config 
RUN cat /etc/ssh/sshd_config |grep Permit
EXPOSE 8080