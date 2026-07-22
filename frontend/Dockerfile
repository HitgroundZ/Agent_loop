FROM node:24-alpine AS build

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .

RUN npm run build

FROM node:24-alpine AS runtime

WORKDIR /app

ENV NODE_ENV=production
ENV PORT=5173
ENV BACKEND_URL=http://backend:8000

COPY --from=build /app/dist ./dist
COPY server.mjs ./server.mjs

USER node

EXPOSE 5173

CMD ["node", "server.mjs"]
