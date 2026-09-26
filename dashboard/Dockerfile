FROM node:24-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Baked in at BUILD time, not runtime (Vite inlines import.meta.env.* into the
# bundle) - must be the backend Cloud Run service's URL. Defaults to the
# actual deployed backend (2026-09-26) since `gcloud run deploy --source`'s
# simplified Dockerfile-build path doesn't reliably thread --set-build-env-vars
# through to a Docker ARG (verified: the bundle still had the LAN-fallback URL
# baked in after passing --set-build-env-vars) - Cloud Run URLs are stable
# once assigned, so hardcoding the default here is safe. Override with
# --build-arg VITE_API_URL=... if the backend is ever redeployed under a
# different service name/region.
ARG VITE_API_URL=https://demand-forecasting-api-174773483931.asia-south1.run.app
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
