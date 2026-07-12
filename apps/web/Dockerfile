FROM node:22-bookworm-slim AS build

WORKDIR /app

COPY package*.json ./
RUN npm ci

ARG SIQ_AUTH_COOKIE_MODE=0
ENV VITE_SIQ_AUTH_COOKIE_MODE=${SIQ_AUTH_COOKIE_MODE}

COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine

ENV SIQ_BACKEND_URL=http://api:18081 \
    SIQ_REPORT_FINDER_URL=http://report-finder:8000

ENV NODE_ENV=production

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf.template /etc/nginx/templates/siq.conf.template
COPY docker-entrypoint.sh /usr/local/bin/siq-web-entrypoint.sh

EXPOSE 15173

USER 101

ENTRYPOINT ["sh", "/usr/local/bin/siq-web-entrypoint.sh"]
CMD ["nginx", "-c", "/tmp/nginx.conf", "-g", "daemon off;"]
