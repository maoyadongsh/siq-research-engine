FROM node:22-bookworm-slim AS build

WORKDIR /app

COPY package*.json ./
RUN npm ci

ARG SIQ_AUTH_COOKIE_MODE=0
ENV VITE_SIQ_AUTH_COOKIE_MODE=${SIQ_AUTH_COOKIE_MODE}

COPY . .
RUN npm run build

ENV SIQ_BACKEND_URL=http://api:18081 \
    SIQ_REPORT_FINDER_URL=http://report-finder:8000 \
    SIQ_PDFAPI_URL=http://pdf-parser:15000

FROM node:22-bookworm-slim

ENV NODE_ENV=production

WORKDIR /app

RUN npm install -g serve@14 \
    && npm cache clean --force
COPY --from=build --chown=node:node /app/dist ./dist

EXPOSE 15173

USER node

CMD ["serve", "-s", "dist", "-l", "15173"]
