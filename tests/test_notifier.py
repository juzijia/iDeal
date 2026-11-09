from __future__ import annotations

import io
import socket
import sys
import threading
import unittest
from contextlib import redirect_stdout
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from ideal_core.models import DealEvent, OfficialApp
from ideal_core.notifier import _call_sender, load_qinglong_sender, send_events


def _make_dummy_event(app_id: str = "123", title: str = "Test App", score: int = 10) -> DealEvent:
    app = OfficialApp(
        app_id=app_id,
        region="cn",
        title=title,
        price=Decimal("0"),
        currency="CNY",
        formatted_price="免费",
        url=f"https://apps.apple.com/cn/app/id{app_id}",
        rating=4.5,
        rating_count=100,
        primary_genre_name="Utilities",
    )
    return DealEvent(
        event_key=f"event_{app_id}",
        app=app,
        old_price=Decimal("12"),
        new_price=Decimal("0"),
        alert_type="drop",
        confidence="A",
        score=score,
    )


class NotifierTestCase(unittest.TestCase):
    def setUp(self) -> None:
        config_patch = patch.dict(globals(), {"push_config": {"CONSOLE": False}})
        config_patch.start()
        self.addCleanup(config_patch.stop)
        # Guard against accidental outbound network calls during tests
        self._orig_socket_connect = socket.socket.connect

        def _forbidden_connect(*_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("Outbound network connections are strictly forbidden in unit tests")

        socket.socket.connect = _forbidden_connect

    def tearDown(self) -> None:
        socket.socket.connect = self._orig_socket_connect

    def _create_ql_sender(self, func: Any, push_config: dict[str, Any] | None = None) -> Any:
        func._is_qinglong_sender = True
        if push_config is not None:
            func.__globals__["push_config"] = push_config
        else:
            func.__globals__["push_config"] = {"CONSOLE": False}
        return func

    def test_qinglong_both_success(self) -> None:
        def fake_send(title: str, content: str) -> None:
            print("--- 开始推送 ---")
            print("bark 推送成功！")
            print("tg 推送成功！")

        sender = self._create_ql_sender(fake_send)
        buf = io.StringIO()
        with redirect_stdout(buf):
            res = _call_sender(sender, "Title", "Body")
        self.assertTrue(res)
        out = buf.getvalue()
        self.assertIn("bark 推送成功！", out)
        self.assertIn("tg 推送成功！", out)
        self.assertNotIn("部分渠道/API已接受", out)

    def test_qinglong_partial_success_bark(self) -> None:
        def fake_send(title: str, content: str) -> None:
            print("bark 推送成功！")
            print("tg 推送失败！")

        sender = self._create_ql_sender(fake_send)
        buf = io.StringIO()
        with redirect_stdout(buf):
            res = _call_sender(sender, "Title", "Body")
        self.assertTrue(res)
        out = buf.getvalue()
        self.assertIn("部分渠道/API已接受", out)

    def test_qinglong_partial_success_tg_with_thread_exception(self) -> None:
        def fake_send(title: str, content: str) -> None:
            print("bark 推送失败！")
            sys.stderr.write("Exception in thread Telegram_bot: URLError(timed out)\n")
            print("tg 推送成功！")

        sender = self._create_ql_sender(fake_send)
        buf = io.StringIO()
        with redirect_stdout(buf):
            res = _call_sender(sender, "Title", "Body")
        self.assertTrue(res)
        out = buf.getvalue()
        self.assertIn("部分渠道/API已接受", out)
        self.assertIn("tg 推送成功！", out)

    def test_qinglong_both_failed(self) -> None:
        def fake_send(title: str, content: str) -> None:
            print("bark 推送失败！")
            print("tg 推送失败！")

        sender = self._create_ql_sender(fake_send)
        buf = io.StringIO()
        with redirect_stdout(buf):
            res = _call_sender(sender, "Title", "Body")
        self.assertFalse(res)
        out = buf.getvalue()
        self.assertIn("未确认任何推送渠道成功", out)

    def test_qinglong_both_exception(self) -> None:
        def fake_send(title: str, content: str) -> None:
            sys.stderr.write("Exception in thread Bark: ConnectionError\n")
            sys.stderr.write("Exception in thread Telegram: TimeoutError\n")

        sender = self._create_ql_sender(fake_send)
        buf = io.StringIO()
        with redirect_stdout(buf):
            res = _call_sender(sender, "Title", "Body")
        self.assertFalse(res)
        out = buf.getvalue()
        self.assertIn("未确认任何推送渠道成功", out)

    def test_qinglong_no_channel(self) -> None:
        def fake_send(title: str, content: str) -> None:
            print("无推送渠道，请检查通知变量是否正确")

        sender = self._create_ql_sender(fake_send)
        buf = io.StringIO()
        with redirect_stdout(buf):
            res = _call_sender(sender, "Title", "Body")
        self.assertFalse(res)
        out = buf.getvalue()
        self.assertIn("未确认任何推送渠道成功", out)

    def test_qinglong_silent_none(self) -> None:
        def fake_send(title: str, content: str) -> None:
            print("通知组件准备就绪")

        sender = self._create_ql_sender(fake_send)
        buf = io.StringIO()
        with redirect_stdout(buf):
            res = _call_sender(sender, "Title", "Body")
        self.assertFalse(res)
        out = buf.getvalue()
        self.assertIn("未确认任何推送渠道成功", out)

    def test_qinglong_explicit_failure_precedence(self) -> None:
        # Even if stdout includes success string, explicit False or dict failure takes precedence
        def fake_send_false(title: str, content: str) -> bool:
            print("bark 推送成功！")
            return False

        sender = self._create_ql_sender(fake_send_false)
        buf = io.StringIO()
        with redirect_stdout(buf):
            res = _call_sender(sender, "Title", "Body")
        self.assertFalse(res)
        self.assertIn("notify.py 报告发送失败", buf.getvalue())

        def fake_send_dict(title: str, content: str) -> dict[str, Any]:
            print("bark 推送成功！")
            return {"success": False, "error": "partial"}

        sender_dict = self._create_ql_sender(fake_send_dict)
        buf = io.StringIO()
        with redirect_stdout(buf):
            res_dict = _call_sender(sender_dict, "Title", "Body")
        self.assertFalse(res_dict)
        self.assertIn("notify.py 报告发送失败", buf.getvalue())

    def test_qinglong_raise_exception(self) -> None:
        def fake_send(title: str, content: str) -> None:
            raise ConnectionResetError("network unreachable")

        sender = self._create_ql_sender(fake_send)
        buf = io.StringIO()
        with redirect_stdout(buf):
            res = _call_sender(sender, "Title", "Body")
        self.assertFalse(res)
        out = buf.getvalue()
        self.assertIn("青龙通知发送失败：ConnectionResetError", out)

    def test_qinglong_console_forgery_protection(self) -> None:
        # When CONSOLE is True, echo output cannot be trusted as channel receipt
        def fake_send(title: str, content: str) -> None:
            # Echoes content
            print(f"Echo:\n{content}")

        sender = self._create_ql_sender(fake_send, push_config={"CONSOLE": True})
        buf = io.StringIO()
        with redirect_stdout(buf):
            res = _call_sender(sender, "Test Title", "Here is bark 推送成功！ line")
        self.assertFalse(res)
        out = buf.getvalue()
        self.assertIn("启用了 CONSOLE 输出，无法确认真实渠道送达", out)

        # Also test with string "true"
        sender_str = self._create_ql_sender(fake_send, push_config={"CONSOLE": "true"})
        buf = io.StringIO()
        with redirect_stdout(buf):
            res_str = _call_sender(sender_str, "Test Title", "Here is tg 推送成功！ line")
        self.assertFalse(res_str)
        self.assertIn("启用了 CONSOLE 输出，无法确认真实渠道送达", buf.getvalue())

    def test_qinglong_body_title_forgery_filtered(self) -> None:
        # CONSOLE is False, but body or title contains the exact line
        def fake_send(title: str, content: str) -> None:
            # Sender inadvertently prints content
            print(content)

        sender = self._create_ql_sender(fake_send, push_config={"CONSOLE": False})
        buf = io.StringIO()
        with redirect_stdout(buf):
            res = _call_sender(sender, "Safe Title", "bark 推送成功！")
        self.assertFalse(res)
        out = buf.getvalue()
        self.assertIn("未确认任何推送渠道成功", out)

        buf = io.StringIO()
        with redirect_stdout(buf):
            res_title = _call_sender(sender, "tg 推送成功！", "Normal body")
        self.assertFalse(res_title)

    def test_qinglong_substring_not_success(self) -> None:
        # Substrings do not count as exact line match
        def fake_send_prefix(title: str, content: str) -> None:
            print("prefix bark 推送成功！ suffix")

        sender = self._create_ql_sender(fake_send_prefix)
        buf = io.StringIO()
        with redirect_stdout(buf):
            res = _call_sender(sender, "Title", "Body")
        self.assertFalse(res)

        def fake_send_no_exclamation(title: str, content: str) -> None:
            print("bark 推送成功")

        sender_no_ex = self._create_ql_sender(fake_send_no_exclamation)
        buf = io.StringIO()
        with redirect_stdout(buf):
            res2 = _call_sender(sender_no_ex, "Title", "Body")
        self.assertFalse(res2)

    def test_generic_sender_compatibility(self) -> None:
        # Generic senders without _is_qinglong_sender keep backward compatible behavior
        def generic_send_none(title: str, content: str) -> None:
            print("Logged notification")

        buf = io.StringIO()
        with redirect_stdout(buf):
            res = _call_sender(generic_send_none, "Title", "Body")
        self.assertTrue(res)

        def generic_send_true(title: str, content: str) -> bool:
            return True

        buf = io.StringIO()
        with redirect_stdout(buf):
            res2 = _call_sender(generic_send_true, "Title", "Body")
        self.assertTrue(res2)

        def generic_send_fail(title: str, content: str) -> None:
            print("network error occurred")

        buf = io.StringIO()
        with redirect_stdout(buf):
            res3 = _call_sender(generic_send_fail, "Title", "Body")
        self.assertFalse(res3)

    def test_send_events_multiple_chunks_second_chunk_fails(self) -> None:
        # Create events that generate a message body > 3500 chars (multiple chunks)
        events = [_make_dummy_event(app_id=f"app_{i}", title=f"Application {i} " + "X" * 120) for i in range(35)]

        call_count = 0

        def chunk_sender(title: str, content: str) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                print("bark 推送成功！")
            else:
                print("bark 推送失败！")

        sender = self._create_ql_sender(chunk_sender)
        buf = io.StringIO()
        with redirect_stdout(buf), patch("ideal_core.notifier.load_qinglong_sender", return_value=sender):
            delivered = send_events(events, "digest")
        self.assertFalse(delivered)
        self.assertGreaterEqual(call_count, 2)

    def test_loader_marks_qinglong_sender(self) -> None:
        def fake_send(title: str, content: str) -> None:
            print("tg 推送成功！")

        # Exercise the real loader without importing Qinglong config or changing sys.path.
        # Resolve all patch targets before mocking importlib's global import function.
        with patch("ideal_core.notifier.sys.path", list(sys.path)), patch(
            "ideal_core.notifier.Path.exists", return_value=True
        ), patch(
            "ideal_core.notifier.importlib.import_module", return_value=SimpleNamespace(send=fake_send)
        ):
            self.assertIs(load_qinglong_sender(), fake_send)
        self.assertTrue(getattr(fake_send, "_is_qinglong_sender", False))

    def test_actual_threads_partial_success(self) -> None:
        for successful_channel in ("bark", "tg"):
            with self.subTest(successful_channel=successful_channel):
                def accept(channel: str = successful_channel) -> None:
                    print(f"{channel} 推送成功！")

                def fail() -> None:
                    raise ConnectionError("simulated channel failure")

                def fake_send(title: str, content: str) -> None:
                    threads = [threading.Thread(target=accept), threading.Thread(target=fail)]
                    for thread in threads:
                        thread.start()
                    for thread in threads:
                        thread.join()

                sender = self._create_ql_sender(fake_send)
                with redirect_stdout(io.StringIO()):
                    self.assertTrue(_call_sender(sender, "Title", "Body"))

    def test_console_matches_qinglong_truthiness(self) -> None:
        def fake_send(title: str, content: str) -> None:
            print("tg 推送成功！")

        for value in (True, "true", "false", "0", "off", 1, ["enabled"]):
            with self.subTest(value=value):
                sender = self._create_ql_sender(fake_send, {"CONSOLE": value})
                with redirect_stdout(io.StringIO()):
                    self.assertFalse(_call_sender(sender, "Title", "Body"))
        for value in (False, "", 0, None):
            with self.subTest(value=value):
                sender = self._create_ql_sender(fake_send, {"CONSOLE": value})
                with redirect_stdout(io.StringIO()):
                    self.assertTrue(_call_sender(sender, "Title", "Body"))

    def test_structured_results_and_conflicting_failure(self) -> None:
        for result, expected in (
            (True, True), ({"ok": True}, True), ({"success": True}, True),
            ({"ok": False}, False), ({"success": True, "ok": False}, False),
        ):
            with self.subTest(result=result):
                def fake_send(title: str, content: str, send_result: object = result) -> object:
                    return send_result

                sender = self._create_ql_sender(fake_send)
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(_call_sender(sender, "Title", "Body"), expected)

    def test_silent_sender_and_misleading_prose(self) -> None:
        for text in ("", "失败是成功之母", "操作未成功", "无推送渠道，请检查通知变量是否正确"):
            with self.subTest(text=text):
                def fake_send(title: str, content: str, message: str = text) -> None:
                    if message:
                        print(message)

                sender = self._create_ql_sender(fake_send)
                with redirect_stdout(io.StringIO()):
                    self.assertFalse(_call_sender(sender, "Title", "Body"))

    def test_generic_no_channel_is_failure(self) -> None:
        def fake_send(title: str, content: str) -> None:
            print("无推送渠道，请检查通知变量是否正确")

        with redirect_stdout(io.StringIO()):
            self.assertFalse(_call_sender(fake_send, "Title", "Body"))

    def test_send_events_all_chunks_accepted(self) -> None:
        events = [_make_dummy_event(str(i), "Application " + "X" * 120) for i in range(35)]

        def fake_send(title: str, content: str) -> None:
            print("tg 推送成功！")
            print("bark 推送失败！")

        sender = self._create_ql_sender(fake_send)
        with redirect_stdout(io.StringIO()), patch(
            "ideal_core.notifier.load_qinglong_sender", return_value=sender
        ) as loader:
            self.assertTrue(send_events(events, "digest"))
        loader.assert_called_once_with()

    def test_send_events_no_events(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            res = send_events([], "digest")
        self.assertTrue(res)
        self.assertIn("本次没有满足条件的新提醒", buf.getvalue())

    def test_send_events_dry_run(self) -> None:
        event = _make_dummy_event("999", "DryRunApp")
        buf = io.StringIO()
        with redirect_stdout(buf):
            res = send_events([event], "digest", dry_run=True)
        self.assertTrue(res)
        self.assertIn("DryRunApp", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
