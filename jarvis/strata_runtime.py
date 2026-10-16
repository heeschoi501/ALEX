"""Checks shared by Strata startup, switching and requests."""
from __future__ import annotations


def validate_strata_health(health: dict, model: str = "", num_ctx: int = 0) -> None:
    """A live port is not proof that the requested model/context is running.

    A larger server context is safe; an unloaded model can be loaded on demand.
    Refuse incompatible servers before releasing the old backend or submitting work.
    """
    if health.get("service") != "strata" or health.get("error"):
        raise ValueError("이 주소에서 정상 Strata 서버를 확인하지 못했습니다.")
    if model and health.get("model") != model:
        raise ValueError(f"Strata 모델이 다릅니다: 실행 중 {health.get('model', '알 수 없음')}, "
                         f"ALEX 설정 {model}. 선택한 strata_config로 Strata를 다시 시작하세요.")
    actual = health.get("max_context")
    if num_ctx and (type(actual) is not int or actual < num_ctx):
        raise ValueError(f"Strata 문맥 창이 ALEX 설정보다 작습니다: 실행 중 {actual}, ALEX {num_ctx}. "
                         "strata_num_ctx와 Strata의 --max-context를 맞추세요.")
