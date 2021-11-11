FROM nginx:latest
ARG CICD_NETWORK_NAME
ADD entrypoint.sh /entrypoint.sh
COPY nginx.conf.template /etc/nginx/templates/nginx.conf.template
#ENTRYPOINT /entrypoint.sh
#CMD ["nginx", "-g", "daemon off;"]