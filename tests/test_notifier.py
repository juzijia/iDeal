import io
import threading
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from ideal_core.notifier import _call_sender, send_events


class NotifierTests(unittest.TestCase):
    def setUp(self) -> None:
        guard = patch("socket.socket.connect", side_effect=AssertionError("Unexpected network access"))
        guard.start()
        self.addCleanup(guard.stop)

    def test_result_policy(self) -> None:
        bark, tg = "bark 推送成功！", "tg 推送成功！"
        cases = (
            ("both accepted", None, f"{bark}\n{tg}", "Body", True),
            ("bark accepted", None, f"{bark}\ntg 推送失败！", "Body", True),
            ("tg accepted", None, f"ConnectionError: bark failed\n{tg}", "Body", True),
            ("both failed", None, "bark 推送失败！\ntg 推送失败！", "Body", False),
            ("no channel", None, "无推送渠道，请检查通知变量是否正确", "Body", False),
            ("explicit false", False, tg, "Body", False),
            ("success false", {"success": False}, tg, "Body", False),
            ("ok false", {"ok": False}, tg, "Body", False),
            ("legacy silent None", None, "", "Body", True),
            ("legacy True", True, "", "Body", True),
            ("other channel unchanged", None, "其他渠道推送成功", "Body", True),
            ("legacy True with failure", True, "network error", "Body", False),
            ("not an exact receipt", None, f"prefix {tg} suffix\nfailed", "Body", False),
            ("echo is not a receipt", None, f"{bark}\ntg 推送失败！", bark, False),
            ("success word is insufficient", None, "失败是成功之母", "Body", False),
        )
        for label, result, output, body, expected in cases:
            with self.subTest(label=label):
                def sender(title: str, content: str, value: object = result, text: str = output) -> object:
                    print(text)
                    return value

                with redirect_stdout(io.StringIO()):
                    self.assertEqual(_call_sender(sender, "Title", body), expected)

    def test_raised_exception_fails(self) -> None:
        with redirect_stdout(io.StringIO()):
            self.assertFalse(_call_sender(Mock(side_effect=OSError("offline")), "Title", "Body"))

    def test_real_threads_partial_success(self) -> None:
        for receipt in ("bark 推送成功！", "tg 推送成功！"):
            with self.subTest(receipt=receipt):
                def accept(line: str = receipt) -> None:
                    print(line)

                def fail() -> None:
                    raise OSError("simulated channel failure")

                def sender(title: str, content: str) -> None:
                    threads = [threading.Thread(target=accept), threading.Thread(target=fail)]
                    for thread in threads:
                        thread.start()
                    for thread in threads:
                        thread.join()

                with redirect_stdout(io.StringIO()):
                    self.assertTrue(_call_sender(sender, "Title", "Body"))

    def test_all_chunks_required(self) -> None:
        for results in ((True, True), (True, False), (False, True)):
            with self.subTest(results=results), redirect_stdout(io.StringIO()), patch(
                "ideal_core.notifier.render_events", return_value=("Title", "Body")
            ), patch(
                "ideal_core.notifier._split_message", return_value=["one", "two"]
            ), patch(
                "ideal_core.notifier.load_qinglong_sender", return_value=Mock()
            ), patch("ideal_core.notifier._call_sender", side_effect=results) as send:
                self.assertEqual(send_events([Mock()], "digest"), all(results))
                self.assertEqual(send.call_count, 2 if results[0] else 1)


if __name__ == "__main__":
    unittest.main()
