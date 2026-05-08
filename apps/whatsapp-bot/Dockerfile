# WhatsApp bot (Baileys) — Node.js
# Expects package.json, PORT 3100, /health endpoint
FROM node:20-alpine
RUN apk add --no-cache git
WORKDIR /app

COPY package*.json ./
RUN npm install --omit=dev

COPY . .
# Build if package.json has "build" script
RUN npm run build 2>/dev/null || true

EXPOSE 3100
ENV PORT=3100
# Support dist/index.js or src/index.js or index.js
CMD ["node", "index.js"]
