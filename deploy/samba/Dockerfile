# Domain-member Samba serving the Artifact Buckets folder tree to Windows.
FROM ubuntu:24.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      samba winbind libnss-winbind libpam-winbind krb5-user acl attr python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY smb.conf.template /etc/samba/smb.conf.template
COPY entrypoint.sh status.py /usr/local/bin/
RUN chmod +x /usr/local/bin/entrypoint.sh && mkdir -p /run/samba /var/log/samba
EXPOSE 445
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
