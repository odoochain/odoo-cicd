FROM wbms/odoo14-multiarch
USER root
COPY ./requirements.txt /requirements.txt
RUN \
pip3 install wheel && \
pip3 install -r requirements.txt && \
rm /requirements.txt
USER odoo
CMD odoo --addons-path=/mnt/extra-addons --dev=reload