# Multi-stage build: compile the React app, then serve the static bundle
# from nginx, proxying API/auth/admin routes back to the Django `web`
# container so the whole thing looks like one origin to the browser (and to
# Authelia's OIDC redirect_uri).
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
