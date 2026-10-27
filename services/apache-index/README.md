# Apache directory listing theme

The stylesheet uses the dashboard's monospace font and MYKOS color palette. Apache still generates the file inventory and sorting links.

Install on a host whose Apache document root is `/var/www/html`:

```bash
sudo install -m 0644 services/apache-index/mykos-index.css /var/www/html/.mykos-index.css
sudo install -m 0644 services/apache-index/mykos-flower.svg /var/www/html/.mykos-flower.svg
sudo install -m 0644 services/apache-index/mykos-autoindex.conf /etc/httpd/conf.d/mykos-autoindex.conf
sudo apachectl configtest
sudo systemctl reload httpd
```

Back up an existing theme or configuration before replacing it. Adjust the Directory path and stylesheet destination for other document roots. The theme also applies to generated listings below the document root; it does not replace index files or change repository content.
