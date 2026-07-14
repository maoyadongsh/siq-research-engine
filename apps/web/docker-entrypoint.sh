#!/bin/sh
set -eu

: "${SIQ_BACKEND_URL:=http://api:18081}"
: "${SIQ_REPORT_FINDER_URL:=http://report-finder:8000}"
: "${SIQ_MEETING_STREAM_GATEWAY_URL:=$SIQ_BACKEND_URL}"

SIQ_BACKEND_URL="${SIQ_BACKEND_URL%/}"
SIQ_REPORT_FINDER_URL="${SIQ_REPORT_FINDER_URL%/}"
SIQ_MEETING_STREAM_GATEWAY_URL="${SIQ_MEETING_STREAM_GATEWAY_URL%/}"
export SIQ_BACKEND_URL SIQ_REPORT_FINDER_URL SIQ_MEETING_STREAM_GATEWAY_URL

mkdir -p /tmp/nginx-client-body /tmp/nginx-proxy /tmp/nginx-fastcgi /tmp/nginx-uwsgi /tmp/nginx-scgi

envsubst '${SIQ_BACKEND_URL} ${SIQ_REPORT_FINDER_URL} ${SIQ_MEETING_STREAM_GATEWAY_URL}' \
  < /etc/nginx/templates/siq.conf.template \
  > /tmp/nginx.conf

exec "$@"
