FROM nginx:latest
ADD entrypoint.sh /entrypoint.sh
COPY nginx.conf /etc/nginx/templates/nginx.conf.template
#ENTRYPOINT /entrypoint.sh
#CMD ["nginx", "-g", "daemon off;"]