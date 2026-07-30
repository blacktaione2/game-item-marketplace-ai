from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.services.llm.base import LLMClient
from app.services.llm.dependencies import get_llm_client

router = APIRouter(prefix="/api/llm", tags=["llm"])


class LLMTestRequest(BaseModel):
    prompt: str = "이 API가 정상 동작하면 '연동 성공'이라고만 답해줘."


class LLMTestResponse(BaseModel):
    provider: str
    response: str


@router.post("/test")
async def test_llm(
    request: LLMTestRequest,
    llm_client: LLMClient = Depends(get_llm_client),
) -> LLMTestResponse:
    """OpenAI 연동 왕복 테스트용 엔드포인트 (Phase 2 범위)."""
    try:
        completion = await llm_client.complete(request.prompt)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM 호출 실패: {e}") from e
    return LLMTestResponse(provider="openai", response=completion)
