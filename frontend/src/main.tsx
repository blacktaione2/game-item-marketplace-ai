import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import App from "./App";
import "./styles.css";

// 서버 상태만 TanStack Query가 들고, 클라이언트 상태(데모 사용자 선택)는
// useState로 충분하다. 전역 스토어는 두지 않는다.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // AI 응답은 비싸다. 창을 다시 눌렀다고 다시 부르지 않는다.
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
