class TenantIndexNotFoundError(Exception):
    """해당 테넌트의 인덱스가 아직 만들어지지 않은 경우."""

    def __init__(self, index: str) -> None:
        self.index = index
        super().__init__(f"테넌트 인덱스를 찾을 수 없습니다: {index}")
