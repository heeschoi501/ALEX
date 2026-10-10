"""같은 시각에 만들어진 세션의 순서 회귀 테스트.

Windows 시계 해상도는 약 15.6ms 다. 그 안에 만들어진 두 세션은 updated_at 이 같고,
`ORDER BY updated_at DESC` 만으로는 순서가 임의가 된다. 2026-09-21 전체 테스트에서
test_list_sessions 가 간헐적으로 실패해 드러났다(재실행 8회는 전부 통과했다).

같은 뿌리의 버그가 이 저장소에서 세 번째다 — storage 메모 정렬, workspace 백업 파일명,
그리고 여기. datetime.now() 를 정렬 키로 쓰는 자리를 볼 때마다 동점을 의심해야 한다.

`latest_session_id()` 는 앱이 시작할 때 대화를 이어가는 데 쓰인다. 동점이 깨지지 않으면
**엉뚱한 대화가 열린다.**
"""
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.storage import Storage  # noqa: E402

# 반드시 **과거** 여야 한다. 처음에 미래 시각으로 고정했더니 이어지는 add_message 가
# 그보다 이른 실제 시각을 써서, 정상 동작을 실패로 읽었다.
FROZEN = "2020-01-01T00:00:00+00:00"


class SameTimestampSessionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.storage = Storage(Path(self._tmp.name) / "t.db")

    def tearDown(self):
        self._tmp.cleanup()

    def _make_two_at_the_same_instant(self) -> tuple[str, str]:
        """시계를 고정해 '같은 tick 안에서 연달아 생성' 을 확정적으로 만든다."""
        with mock.patch.object(Storage, "_now", return_value=FROZEN):
            first = self.storage.create_session()
            second = self.storage.create_session()
        return first, second

    def test_latest_session_is_the_one_created_last(self):
        first, second = self._make_two_at_the_same_instant()
        self.assertEqual(self.storage.latest_session_id(), second,
                         "시각이 같으면 나중에 만든 세션이 최신이어야 한다")

    def test_list_sessions_puts_the_newest_first(self):
        first, second = self._make_two_at_the_same_instant()
        self.assertEqual([s.session_id for s in self.storage.list_sessions()][:2],
                         [second, first])

    def test_timestamps_really_are_equal_in_this_fixture(self):
        """동점을 실제로 만들지 못하면 위 두 테스트는 아무것도 지키지 못한다."""
        self._make_two_at_the_same_instant()
        stamps = [s.updated_at for s in self.storage.list_sessions()[:2]]
        self.assertEqual(stamps[0], stamps[1], stamps)

    def test_a_later_update_still_wins_over_creation_order(self):
        """동점 해소가 정상적인 시간 순서를 덮어쓰면 안 된다."""
        first, second = self._make_two_at_the_same_instant()
        self.storage.add_message(first, "user", "나중에 온 메시지")
        self.assertEqual(self.storage.latest_session_id(), first)


if __name__ == "__main__":
    unittest.main()
