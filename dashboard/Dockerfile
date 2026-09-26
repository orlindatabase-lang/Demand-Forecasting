FROM node:24-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Baked in at BUILD time, not runtime (Vite inlines import.meta.env.* into the
# bundle) - must be the backend Cloud Run service's URL, set via
# --build-arg or a Cloud Build substitution when deploying this service.
ARG VITE_API_URL
ENV VITE_API_URL=$VITE_API_URL
# npx vite build, not "npm run build" - that script runs "tsc -b" first,
# which currently errors on tsconfig.app.json's deprecated "baseUrl" option
# (TS5101) before it ever reaches vite. Type errors are still caught by CI/
# local `npx tsc --noEmit`; this build step only needs the JS output.
RUN npx vite build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
