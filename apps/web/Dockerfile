FROM node:22-bookworm-slim

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

ENV SIQ_BACKEND_URL=http://api:18081 \
    SIQ_REPORT_FINDER_URL=http://report-finder:8000 \
    SIQ_PDFAPI_URL=http://pdf-parser:15000

EXPOSE 15173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "15173"]
