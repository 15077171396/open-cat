"""参数校验失败的**日志脱敏**回归（台账 P1-28 残留）。

## 这个测试防的是什么

`validation_exception_handler` 会把 Pydantic 的校验错误写进日志。而
`exc.errors()` 里含 **`input` —— 用户提交的原始值**：

    LoginReq(password='MySecret123')
    -> [{'type': 'string_too_short', 'loc': ['password'], ..., 'input': 'MySecret123'}]

参数校验失败是「用户输错就会触发」的高频路径，整条落盘（保留 14 天）等于把
密码 / 验证码 / 密钥明文沉淀到磁盘上。修复方式是记录前裁剪为 loc / msg / type 三项。

**测试必须断言「明文不在日志里」，而不只是「函数返回了三个键」** ——
后者在实现被改回 `exc.errors()` 时仍会通过（dict 里依然有那三个键）。
"""

import json

from app.core.exceptions import _safe_validation_errors


def _sample_errors(secret: str) -> list:
    """构造与 Pydantic 真实产出同构的错误列表（含 input / ctx / url）"""
    return [
        {
            "type": "string_too_short",
            "loc": ["body", "password"],
            "msg": "String should have at least 20 characters",
            "input": secret,
            "ctx": {"min_length": 20},
            "url": "https://errors.pydantic.dev/2.13/v/string_too_short",
        }
    ]


class TestSafeValidationErrors:
    def test_secret_not_present_in_scrubbed_output(self):
        secret = "MySecret123"
        out = _safe_validation_errors(_sample_errors(secret))
        assert secret not in json.dumps(out, ensure_ascii=False, default=str)

    def test_only_loc_msg_type_are_kept(self):
        out = _safe_validation_errors(_sample_errors("x" * 10))
        assert len(out) == 1
        assert set(out[0].keys()) == {"loc", "msg", "type"}

    def test_loc_and_msg_preserved_for_troubleshooting(self):
        out = _safe_validation_errors(_sample_errors("secret"))
        assert out[0]["loc"] == ["body", "password"]
        assert out[0]["msg"] == "String should have at least 20 characters"
        assert out[0]["type"] == "string_too_short"

    def test_non_serializable_ctx_is_dropped(self):
        """ctx 可能含 ValueError 等对象（原实现注释里提到过），裁掉即无此顾虑"""

        class Weird:
            def __repr__(self):
                return "<weird>"

        errors = _sample_errors("x")
        errors[0]["ctx"] = {"err": Weird()}
        out = _safe_validation_errors(errors)
        # 结果必须可 JSON 序列化（否则写日志时会抛错）
        assert json.dumps(out, ensure_ascii=False) is not None

    def test_empty_and_multi_field_cases(self):
        assert _safe_validation_errors([]) == []
        multi = _safe_validation_errors(
            [
                {"type": "missing", "loc": ["body", "title"], "msg": "Field required", "input": None},
                {
                    "type": "int_parsing",
                    "loc": ["body", "age"],
                    "msg": "Input should be a valid integer",
                    "input": "abc",
                },
            ]
        )
        assert len(multi) == 2
        assert multi[1]["loc"] == ["body", "age"]
        assert "abc" not in json.dumps(multi, ensure_ascii=False)

    def test_real_pydantic_error_does_not_leak_input(self):
        """用真实的 Pydantic ValidationError 校验，防止上游行为变化后测试失真"""
        from pydantic import BaseModel, Field, ValidationError

        class Req(BaseModel):
            password: str = Field(min_length=20)

        try:
            Req(password="MySecret123")
        except ValidationError as e:
            raw = e.errors()
            # 先确认「原材料确实含明文」—— 否则本测试就是假绿
            assert "MySecret123" in json.dumps(raw, default=str)
            safe = _safe_validation_errors(raw)
            assert "MySecret123" not in json.dumps(safe, default=str)
        else:  # pragma: no cover
            raise AssertionError("预期校验失败但没有抛错")


class TestValidationHandlerLogsScrubbed:
    """**集成层**测试：断言 handler 真的用了裁剪。

    上面那些是 `_safe_validation_errors` 的单元测试 —— 它们**测不出**
    「handler 忘了调用它」（变异测试已证实：把 handler 里的裁剪改回原始
    `exc.errors()`，单元测试依然全绿）。真正的修复点在**调用处**，就必须在调用处断言。
    """

    def test_endpoint_422_log_contains_no_plaintext_secret(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from loguru import logger as loguru_logger
        from pydantic import BaseModel, Field

        from app.core.exceptions import register_exception_handlers

        app = FastAPI()
        register_exception_handlers(app)

        class Req(BaseModel):
            password: str = Field(min_length=20)

        @app.post("/_t")
        async def _t(req: Req):  # pragma: no cover - 不会成功执行
            return {"ok": True}

        captured: list[str] = []
        sink_id = loguru_logger.add(lambda m: captured.append(str(m)), level="WARNING")
        try:
            client = TestClient(app)
            resp = client.post("/_t", json={"password": "MySecret123"})
        finally:
            loguru_logger.remove(sink_id)

        assert resp.status_code == 422
        log_text = "".join(captured)

        # 关键断言：明文密码不得进日志
        assert "MySecret123" not in log_text
        # 同时确认「裁剪没把排查价值一起裁掉」——字段名与错误类型仍要看得见
        assert "password" in log_text
        assert "string_too_short" in log_text
