#!/usr/bin/bash

# Cloud backup server information.

# Header
[ ! -f /tmp/backup_perf.txt ] && echo "Cloud Backup Server" > /tmp/backup_perf.txt && echo "-------------------" >> /tmp/backup_perf.txt && echo "`date`" >> /tmp/backup_perf.txt && echo "`hostname`" >> /tmp/backup_perf.txt && echo "Public IP: `curl -s 'https://api.ipify.org'`" >> /tmp/backup_perf.txt && echo "`ls /backups/tfrsync-* | grep paying_customer | wc -l` subscribers" >> /tmp/backup_perf.txt && echo "`ls /backups | grep tfrsync- | wc -l` /backups folders" >> /tmp/backup_perf.txt && echo "`cat /etc/passwd | grep tfrsync- | wc -l` OS users" >> /tmp/backup_perf.txt && echo "`df -h /backups | tail -1 | cut -d" " -f5`" total backup data >> /tmp/backup_perf.txt && echo "" >> /tmp/backup_perf.txt && echo "Hour Cons  Load  Space  Accounts" >> /tmp/backup_perf.txt

HOUR="`date +%H`"
NO_OF_BACKS="`ps -ef | grep sync- | grep notty | cut -d- -f2 | cut -d@ -f1 | wc -l`"
NO_OF_BACKS_RND=`printf "%2.f" $NO_OF_BACKS`
CURRENT_LOAD="`cat /proc/loadavg | cut -d' ' -f1`"
CURRENT_LOAD_RND=`printf "%3.2f" $CURRENT_LOAD`
SPACE_LEFT="`df -h /backups | tail -1 | cut -d' ' -f7`"
echo -n "$HOUR   $NO_OF_BACKS_RND    $CURRENT_LOAD_RND  $SPACE_LEFT  " >> /tmp/backup_perf.txt

for ACCT in "`ps -ef | grep sync- | grep notty | cut -d- -f2 | cut -d@ -f1`"
do
ACCT_LIST=" `echo -n $ACCT_LIST $ACCT `"
done
echo "$ACCT_LIST" >> /tmp/backup_perf.txt

[ "`cat /tmp/backup_perf.txt | wc -l`" == "35" ] && mail -s Cloud_Backup_Perf mgreen@teleflora.com,kpugh@teleflora.com,jmonazym@teleflora.com,tshilling@teleflora.com < /tmp/backup_perf.txt && mv /tmp/backup_perf.txt /tmp/backup_perf.txt.`date +%d`
