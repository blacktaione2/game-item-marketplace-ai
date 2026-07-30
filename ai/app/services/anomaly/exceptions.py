class AnomalyModelNotTrainedError(RuntimeError):
    def __init__(self, model_dir: str) -> None:
        super().__init__(
            f"이상거래 탐지 모델이 없습니다({model_dir}). "
            "`python -m scripts.train_anomaly` 로 먼저 학습하세요."
        )


class UnknownTenantError(RuntimeError):
    """임계값이 학습되지 않은 테넌트.

    임계값은 테넌트별이라, 새 게임사가 붙으면 그 테넌트 데이터로 다시 잡아야
    한다. 다른 테넌트 임계값을 빌려 쓰면 거래 분포가 달라 알림량이 어긋난다.
    """

    def __init__(self, tenant_code: str, known: list[str]) -> None:
        super().__init__(
            f"테넌트 '{tenant_code}'의 임계값이 없습니다. "
            f"학습된 테넌트: {', '.join(known)}"
        )


class TradeNotFoundError(RuntimeError):
    def __init__(self, trade_id: int) -> None:
        super().__init__(f"거래 {trade_id}을(를) 찾을 수 없습니다.")
