class ForecastModelNotTrainedError(RuntimeError):
    """시세 예측 모델이 아직 학습되지 않았을 때.

    models/ 는 gitignore 대상이라 새로 clone한 환경에서 반드시 마주치는
    상황이다. 원인을 추측하게 두지 말고 재생성 명령까지 알려준다.
    """

    def __init__(self, model_dir: str) -> None:
        super().__init__(
            f"시세 예측 모델이 없습니다({model_dir}). "
            "`python -m scripts.train_forecast` 로 먼저 학습하세요."
        )


class ItemNotFoundError(RuntimeError):
    def __init__(self, item_id: int) -> None:
        super().__init__(f"아이템 {item_id}을(를) 인덱스에서 찾을 수 없습니다.")


class InsufficientHistoryError(RuntimeError):
    """이력도 부족하고 트렌드를 상속받을 유사 아이템도 못 찾았을 때."""

    def __init__(self, item_id: int) -> None:
        super().__init__(
            f"아이템 {item_id}의 거래 이력이 부족하고, 트렌드를 상속받을 "
            "유사 아이템도 찾지 못했습니다."
        )
