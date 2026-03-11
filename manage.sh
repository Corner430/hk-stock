#!/bin/bash
CONF=/etc/supervisor/supervisord.conf
case "$1" in
  status)  supervisorctl -c $CONF status ;;
  restart) supervisorctl -c $CONF restart all ;;
  stop)    supervisorctl -c $CONF stop all ;;
  start)   supervisorctl -c $CONF start all ;;
  log-dash) tail -50 /var/log/hkstock-dashboard.log ;;
  log-cron) tail -50 /var/log/hkstock-cron.log ;;
  *)
    echo "用法: ./manage.sh [status|restart|stop|start|log-dash|log-cron]"
    ;;
esac
