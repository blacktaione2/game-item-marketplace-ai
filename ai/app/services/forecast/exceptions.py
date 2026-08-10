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


class HorizonTooLongError(RuntimeError):
    """요청한 예측 기간이 학습된 모델의 horizon 을 넘을 때.

    **예전에는 그냥 `ValueError` 였다** (ADR-0050 정정). 라우터가
    `except ValueError -> 400, detail=str(e)` 로 받았는데, 그건 **범주 기반
    catch** 다 — `forecast_price` 안의 ES·numpy·torch 가 언젠가 `ValueError` 를
    내면 그 내부 메시지가 그대로 400 본문이 된다.

    `tests/test_error_detail_leak.py` 는 `detail=str(e)` 를 허용하면서 그 근거를
    *"도메인 예외라서 우리가 쓴 메시지"* 라고 적었다. **그 근거가 이 한 자리에
    대해서만 거짓이었다** — 이 저장소가 반복해서 겪은
    *"묶어서 쓴 근거는 그것이 거짓인 구성원에게도 상속된다"*(ADR-0047).

    도메인 예외로 만들면 셋이 같이 해결된다: 근거가 참이 되고, 통합 진입점이
    `_SHOWABLE_STATUS` 로 같은 400 을 물려받고, 내부 예외는 다시 500(일반 문장)이
    된다.
    """

    def __init__(self, requested: int, maximum: int) -> None:
        super().__init__(
            f"모델이 예측할 수 있는 최대 기간은 {maximum}일입니다 (요청: {requested}일)"
        )


class InsufficientHistoryError(RuntimeError):
    """이력도 부족하고 트렌드를 상속받을 유사 아이템도 못 찾았을 때."""

    def __init__(self, item_id: int) -> None:
        super().__init__(
            f"아이템 {item_id}의 거래 이력이 부족하고, 트렌드를 상속받을 "
            "유사 아이템도 찾지 못했습니다."
        )
