# 프론트엔드 이미지 — 빌드(node) → 정적 서빙(nginx).
#
# 실행 이미지에 node 를 남기지 않는다. 산출물은 정적 파일뿐이고, nginx 는
# 서빙 말고도 **단일 오리진을 만드는 역할**을 한다(nginx.conf 참고).

FROM node:22-alpine AS build
WORKDIR /build

# 소스보다 먼저. 소스만 바뀐 재빌드에서 npm ci 레이어가 캐시된다.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
# `npm run build` 는 tsc -b 를 먼저 돈다 — 타입 오류가 있으면 여기서 멈춘다.
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /build/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
