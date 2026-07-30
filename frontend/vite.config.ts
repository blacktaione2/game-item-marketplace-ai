import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 백엔드가 둘(Spring Boot 8080, FastAPI 8000)이지만 브라우저에는 단일
// 오리진만 보이게 한다. 이러면 양쪽 서버에 CORS 설정을 추가하지 않아도 된다 —
// 기존 서버 코드를 건드리지 않는 게 이 선택의 핵심이다.
// 배포 시에는 리버스 프록시가 같은 역할을 하며, 그건 Phase 8 작업이다.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api/backend": {
        target: "http://localhost:8080",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/backend/, "/api"),
      },
      "/api/ai": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/ai/, "/api"),
      },
    },
  },
});
