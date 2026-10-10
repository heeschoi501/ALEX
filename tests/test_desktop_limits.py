"""프로필별 런타임 상한 회귀 테스트.

2026-09-19 에 랩탑 실측으로 num_ctx 8192 / num_predict 6144 / timeout 180 을 정했는데,
이 값들이 전역이라 Desktop 프로필에도 그대로 적용되고 있었다. 특히 num_ctx 는 설정되어
있으면 NUM_CTX_BY_PROFILE[DESKTOP](16384) 을 덮어써서, 16GB 장비를 8GB 기준으로 묶는다.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.app import NUM_CTX_BY_PROFILE, effective_runtime_limits  # noqa: E402
from jarvis.config import ConfigError, Settings, validate  # noqa: E402
from jarvis.hardware import PerformanceProfile  # noqa: E402

LAPTOP = PerformanceProfile.LAPTOP
DESKTOP = PerformanceProfile.DESKTOP


def laptop_tuned(**over) -> Settings:
    """2026-09-19 에 랩탑에서 실측해 정한 값."""
    base = dict(num_ctx=8192, local_max_output_tokens=6144, request_timeout=180)
    base.update(over)
    return Settings(**base)


class LaptopUnchangedTests(unittest.TestCase):
    def test_laptop_uses_the_global_values(self):
        self.assertEqual(effective_runtime_limits(laptop_tuned(), LAPTOP), (8192, 6144, 180))

    def test_zero_num_ctx_falls_back_to_the_profile_default(self):
        ctx, _, _ = effective_runtime_limits(Settings(num_ctx=0), LAPTOP)
        self.assertEqual(ctx, NUM_CTX_BY_PROFILE[LAPTOP])

    def test_desktop_overrides_do_not_leak_into_laptop(self):
        s = laptop_tuned(desktop_num_ctx=32768, desktop_max_output_tokens=8192,
                         desktop_request_timeout=600)
        self.assertEqual(effective_runtime_limits(s, LAPTOP), (8192, 6144, 180))


class DesktopOverrideTests(unittest.TestCase):
    def test_desktop_overrides_win(self):
        s = laptop_tuned(desktop_num_ctx=32768, desktop_max_output_tokens=8192,
                         desktop_request_timeout=600)
        self.assertEqual(effective_runtime_limits(s, DESKTOP), (32768, 8192, 600))

    def test_each_override_is_independent(self):
        s = laptop_tuned(desktop_num_ctx=16384)
        self.assertEqual(effective_runtime_limits(s, DESKTOP), (16384, 6144, 180))

    def test_without_overrides_behaviour_is_unchanged(self):
        """기존 동작을 깨지 않는다 — 전역값이 그대로 간다."""
        self.assertEqual(effective_runtime_limits(laptop_tuned(), DESKTOP), (8192, 6144, 180))

    def test_desktop_with_nothing_set_uses_its_profile_ctx(self):
        ctx, _, _ = effective_runtime_limits(Settings(), DESKTOP)
        self.assertEqual(ctx, NUM_CTX_BY_PROFILE[DESKTOP])
        self.assertGreater(ctx, NUM_CTX_BY_PROFILE[LAPTOP])


class DesktopValidationTests(unittest.TestCase):
    def test_zero_means_inherit_and_is_accepted(self):
        validate(Settings(desktop_num_ctx=0, desktop_max_output_tokens=0,
                                   desktop_request_timeout=0))

    def test_out_of_range_desktop_ctx_is_rejected(self):
        with self.assertRaises(ConfigError):
            validate(Settings(desktop_num_ctx=512))

    def test_out_of_range_desktop_output_tokens_is_rejected(self):
        with self.assertRaises(ConfigError):
            validate(Settings(desktop_max_output_tokens=99_999))

    def test_out_of_range_desktop_timeout_is_rejected(self):
        with self.assertRaises(ConfigError):
            validate(Settings(desktop_request_timeout=99_999))


if __name__ == "__main__":
    unittest.main()


class OutputBudgetClampTests(unittest.TestCase):
    """출력 예산이 컨텍스트 창을 넘지 않게 막는다.

    UNKNOWN 프로필의 num_ctx 는 2048 인데, 사용자가 랩탑 실측으로 정한 6144 를 그대로
    쓰면 출력 예산이 컨텍스트 창보다 커진다. 검증이 두 값을 각각만 보고 서로 비교하지
    않아 조용히 그 상태로 돌았다.
    """

    def test_the_users_measured_config_is_not_touched(self):
        """ctx 8192 + 6144 는 실측으로 정한 값이다. 클램프가 이걸 건드리면 안 된다."""
        from jarvis.app import clamp_output_budget
        self.assertEqual(clamp_output_budget(8192, 6144), 6144)

    def test_unknown_profile_context_clamps_the_budget(self):
        from jarvis.app import clamp_output_budget
        self.assertEqual(clamp_output_budget(2048, 6144), 1024)

    def test_clamped_budget_always_leaves_room_for_a_prompt(self):
        from jarvis.app import clamp_output_budget
        for ctx in (2048, 4096, 8192, 16384, 32768):
            self.assertLess(clamp_output_budget(ctx, 8192), ctx,
                            f"ctx {ctx} 에서 출력 예산이 컨텍스트 창을 넘었다")

    def test_a_budget_that_already_fits_is_returned_unchanged(self):
        from jarvis.app import clamp_output_budget
        self.assertEqual(clamp_output_budget(8192, 1024), 1024)

    def test_unknown_profile_end_to_end(self):
        """전역값이 랩탑 실측이어도 UNKNOWN 장비에서는 줄어들어야 한다."""
        ctx, out, _ = effective_runtime_limits(laptop_tuned(num_ctx=0),
                                               PerformanceProfile.UNKNOWN)
        self.assertEqual(ctx, NUM_CTX_BY_PROFILE[PerformanceProfile.UNKNOWN])
        self.assertLess(out, ctx)


class ContradictoryConfigTests(unittest.TestCase):
    def test_budget_larger_than_explicit_context_is_rejected(self):
        with self.assertRaises(ConfigError):
            validate(Settings(num_ctx=4096, local_max_output_tokens=6144))

    def test_equal_values_are_rejected_too(self):
        with self.assertRaises(ConfigError):
            validate(Settings(num_ctx=4096, local_max_output_tokens=4096))

    def test_the_users_measured_config_passes(self):
        validate(laptop_tuned())

    def test_contradictory_desktop_pair_is_rejected(self):
        with self.assertRaises(ConfigError):
            validate(laptop_tuned(desktop_num_ctx=4096, desktop_max_output_tokens=8192))

    def test_desktop_budget_checked_against_inherited_context(self):
        """desktop_num_ctx 가 0이면 전역 num_ctx 와 비교해야 한다."""
        with self.assertRaises(ConfigError):
            validate(laptop_tuned(desktop_max_output_tokens=8192))
