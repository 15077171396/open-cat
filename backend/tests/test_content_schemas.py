"""公开内容响应模型的守卫（第五轮审查 EXT-06）。

补 schema 的收益是「前端能从 OpenAPI 拿类型」，风险则全在 `extra` 策略上：

- pydantic 默认 `extra="ignore"`，FastAPI 用 `response_model` 序列化时会**丢掉未声明的字段**。
  也就是说「加了一个 schema」可能变成「接口突然少字段」，而且 OpenAPI 看起来完全正确 ——
  比没有 schema 更糟，因为它把「少字段」伪装成了契约。
- 反过来，`ContentDetailBase` 与 `detail_shape.build_detail_base()` 是同一组键的
  两处声明，漂移了没人会发现（加公共字段只改一边）。

本文件钉住这两点。全部离线。
"""

import ast
import glob
import io
import os
import unittest

from app.schemas import content as schemas
from app.schemas import user_content as user_schemas
from app.services.detail_shape import COMMON_DETAIL_KEYS

# 所有内容响应模型（新增时必须加进来，否则 extra 策略不受检查）
_CONTENT_MODELS = (
    schemas.PageResult,
    schemas.ContentListItem,
    schemas.ContentDetailBase,
    schemas.TutorialDetail,
    schemas.TutorialIntro,
    schemas.NodeDetail,
    schemas.PluginDetail,
    schemas.ResourceDetail,
    schemas.ToolDetail,
    # 用户中心（第二批）
    user_schemas.FavoriteItem,
    user_schemas.DownloadHistoryItem,
    user_schemas.LearningRecordItem,
    user_schemas.ContinueLearningItem,
    user_schemas.MessageItem,
    user_schemas.PointTransactionItem,
    user_schemas.InviteInfo,
)


class ExtraAllowTests(unittest.TestCase):
    def test_all_models_pass_through_unknown_fields(self):
        for model in _CONTENT_MODELS:
            self.assertEqual(
                model.model_config.get("extra"), "allow",
                f"{model.__name__} 没有设 extra='allow' —— "
                "response_model 会把未声明的字段悄悄丢掉（接口少字段、前端静默空白，"
                "而 OpenAPI 看起来完全正确）",
            )

    def test_extra_field_survives_dump(self):
        """行为验证（不只是配置）：带一个未声明字段，dump 后它必须还在。"""
        payload = schemas.NodeDetail.model_validate(
            {"id": 1, "title": "标题", "some_future_field": "值"}
        )
        dumped = payload.model_dump()
        self.assertEqual(dumped["some_future_field"], "值")


class SchemaMatchesBuilderTests(unittest.TestCase):
    def test_common_keys_are_declared_in_schema(self):
        """`build_detail_base` 产出的公共键必须都在 `ContentDetailBase` 里声明。

        漏一个不会报错（靠 extra 透传），但 OpenAPI 里就少一个字段，
        前端按 schema 生成类型时会读不到它 —— 契约与实际不一致。
        """
        declared = set(schemas.ContentDetailBase.model_fields)
        missing = [k for k in COMMON_DETAIL_KEYS if k not in declared]
        self.assertEqual(
            missing, [],
            f"detail_shape.COMMON_DETAIL_KEYS 里有 {missing} 未在 ContentDetailBase 声明",
        )

    def test_schema_does_not_declare_nonexistent_common_keys(self):
        """反向：schema 里声明为「公共」的字段，builder 必须真的产出。"""
        extra_in_schema = set(schemas.ContentDetailBase.model_fields) - set(COMMON_DETAIL_KEYS)
        self.assertEqual(
            extra_in_schema, set(),
            f"ContentDetailBase 多声明了 {sorted(extra_in_schema)}，"
            "但 build_detail_base 不会产出它们（OpenAPI 会承诺一个恒为 null 的字段）",
        )


class OpenApiContractTests(unittest.TestCase):
    """这 23 条接口（11 条公开内容 + 7 条用户中心 + 5 条后台列表）的 OpenAPI 里必须出现具体 schema，
    而不是一个裸 object。"""

    TARGETS = (
        ("/api/v1/nodes", "get"),
        ("/api/v1/nodes/{node_id}", "get"),
        ("/api/v1/plugins", "get"),
        ("/api/v1/plugins/{plugin_id}", "get"),
        ("/api/v1/resources", "get"),
        ("/api/v1/resources/{resource_id}", "get"),
        ("/api/v1/tutorials", "get"),
        ("/api/v1/tutorials/{tutorial_id}", "get"),
        ("/api/v1/tutorials/{tutorial_id}/intro", "get"),
        ("/api/v1/tools", "get"),
        ("/api/v1/tools/{tool_id}", "get"),
        # 用户中心（第二批）
        ("/api/v1/users/favorites", "get"),
        ("/api/v1/users/learning", "get"),
        ("/api/v1/users/learning/continue", "get"),
        ("/api/v1/users/downloads", "get"),
        ("/api/v1/users/messages", "get"),
        ("/api/v1/users/points/transactions", "get"),
        ("/api/v1/users/invite/info", "get"),
        # 后台列表（与公开列表同一套分页形状）
        ("/api/v1/admin/nodes", "get"),
        ("/api/v1/admin/plugins", "get"),
        ("/api/v1/admin/resources", "get"),
        ("/api/v1/admin/tutorials", "get"),
        ("/api/v1/admin/tools", "get"),
    )

    @classmethod
    def setUpClass(cls):
        from app.main import app

        cls.spec = app.openapi()

    def test_targets_have_typed_data(self):
        problems = []
        for path, method in self.TARGETS:
            op = self.spec["paths"].get(path, {}).get(method)
            if op is None:
                problems.append(f"{method.upper()} {path} 不在 OpenAPI 里")
                continue
            # 200 响应 → ApiResponse_xxx_ → data 字段必须是 $ref 或 anyOf 里有 $ref
            schema = (
                op.get("responses", {})
                .get("200", {})
                .get("content", {})
                .get("application/json", {})
                .get("schema", {})
            )
            text = str(schema)
            if "$ref" not in text:
                problems.append(f"{method.upper()} {path} 的响应仍是裸对象")
        self.assertEqual(problems, [], "\n".join(problems))


class NoUntypedRoutesTests(unittest.TestCase):
    """棘轮：路由不得再退回 `ApiResponse[dict]`（EXT-06 收口）。

    这条守卫的价值在于「防止回退」：一次性把 148 条补完之后，如果没有棘轮，
    下次有人加接口时顺手写 `ApiResponse[dict]` 也不会有任何提示，
    几个月后又回到原点。这里直接扫源码，出现一处就红。
    """

    def test_no_api_response_dict_left(self):
        api_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "api")
        hits = []
        for path in glob.glob(os.path.join(api_dir, "*.py")):
            text = io.open(path, encoding="utf-8").read()
            for idx, line in enumerate(text.splitlines(), 1):
                if "ApiResponse[dict]" in line:
                    hits.append(f"{os.path.basename(path)}:{idx}")
        self.assertEqual(
            hits,
            [],
            "以下路由仍返回裸 dict —— 新增接口请用 schemas/ 下的模型"
            "（写/改类可用 ActionAck，列表用 PageResult[...]）：\n  " + "\n  ".join(hits),
        )


class JsonColumnTypesArePermissiveTests(unittest.TestCase):
    """详情响应模型里，**对应 JSON 列**的字段不得声明成标量类型。

    回归背景（2026-10-20）：第五轮给公开接口补响应模型时，把资源的
    `content_list` / `usage_notes` 写成了 `Optional[str]`、把百科的 `examples`
    写成了 `Optional[list]` —— 但它们在库里都是 **JSON 列**，实际形态分别是数组和对象。
    于是 `ResponseValidationError` 被全局处理器转成 500：本地 8/16 条资源、11/16 个百科节点
    详情页直接打不开，而列表页、健康探针、深度探活**全部正常**，问题只在你点进详情时才暴露。

    为什么只能静态查：单测没有真实数据，构造 payload 时按 schema 造必然「合规」，
    测不出「库里数据形态与声明不符」。所以这里反过来查**声明本身**：
    凡是与 JSON 列同名的 schema 字段，类型必须放行容器（list / dict / Any）。
    """

    #: 内容类型 → 它的公开详情响应模型
    DETAIL_SCHEMAS = {
        "tutorial": schemas.TutorialDetail,
        "node": schemas.NodeDetail,
        "plugin": schemas.PluginDetail,
        "resource": schemas.ResourceDetail,
        "tool": schemas.ToolDetail,
    }

    @staticmethod
    def _allows_container(annotation) -> bool:
        """注解里是否出现了 list / dict / Any（含 Optional[...] 与 list[dict] 这类）。"""
        import typing

        args = typing.get_args(annotation) or (annotation,)
        for arg in args:
            if arg in (list, dict, typing.Any):
                return True
            if typing.get_origin(arg) in (list, dict):
                return True
        return False

    def test_json_columns_accept_list_or_dict(self):
        from sqlalchemy import JSON

        from app.core.content_types import CONTENT_TYPES

        problems = []
        checked = 0
        for module, schema in self.DETAIL_SCHEMAS.items():
            spec = CONTENT_TYPES[module]
            json_cols = {
                col.name
                for col in spec.model.__table__.columns
                if isinstance(col.type, JSON)
            }
            declared = set(schema.model_fields)
            for name in sorted(json_cols & declared):
                checked += 1
                annotation = schema.model_fields[name].annotation
                if not self._allows_container(annotation):
                    problems.append(
                        f"{schema.__name__}.{name} 声明为 {annotation}，"
                        f"但它对应 {spec.model.__name__} 的 JSON 列 —— "
                        "库里可能是数组也可能是对象，标量类型会让详情接口 500"
                    )
        self.assertGreaterEqual(
            checked, 1, "没有找到任何「schema 字段 ↔ JSON 列」同名对，守卫可能已失效"
        )
        self.assertEqual(problems, [], "\n".join(problems))


class ResponseModelMatchesReturnShapeTests(unittest.TestCase):
    """路由声明的响应形状，必须与 `success_response(...)` 传进去的形状一致。

    回归背景（2026-10-20）：`/users/learning/continue` 声明
    `ApiResponse[list[ContinueLearningItem]]`，却返回 `success_response({"items": ...})`
    —— dict 对 list，FastAPI 序列化抛 `ResponseValidationError` → 500。
    个人中心「继续学习」整块打不开，顶部弹「部分数据加载失败，展示的可能不是最新数据」。

    这类错单测很难覆盖（要登录态 + 有学习记录才走到那段代码），但**静态就能看出来**：
    声明成列表的，返回的不能是 dict 字面量；声明成 PageResult/ItemsResult 的，
    返回的不能是 list 字面量。传变量名的无法判定，跳过（不假装能查）。
    """

    _PAGED = {"PageResult", "ItemsResult"}

    @staticmethod
    def _data_shape(node):
        """`ApiResponse[list[X]]` → `"list"`；`ApiResponse[PageResult[X]]` → `"PageResult"`。"""
        if not isinstance(node, ast.Subscript) or not isinstance(node.value, ast.Name):
            return None
        if node.value.id != "ApiResponse":
            return None
        inner = node.slice
        if isinstance(inner, ast.Name):
            return inner.id
        if isinstance(inner, ast.Subscript) and isinstance(inner.value, ast.Name):
            return inner.value.id
        return None

    def _routes(self):
        api_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "api"
        )
        for path in sorted(glob.glob(os.path.join(api_dir, "*.py"))):
            tree = ast.parse(io.open(path, encoding="utf-8").read())
            for node in ast.walk(tree):
                if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                    continue
                for dec in node.decorator_list:
                    if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                        continue
                    if dec.func.attr not in ("get", "post", "put", "patch", "delete"):
                        continue
                    kw = next((k for k in dec.keywords if k.arg == "response_model"), None)
                    if kw is None:
                        continue
                    declared = self._data_shape(kw.value)
                    if declared:
                        yield os.path.basename(path), node, declared

    def test_declared_shape_matches_returned_literal(self):
        problems = []
        checked = 0
        for fname, func, declared in self._routes():
            for node in ast.walk(func):
                if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Call):
                    continue
                call = node.value
                if not (isinstance(call.func, ast.Name) and call.func.id == "success_response"):
                    continue
                if not call.args:
                    continue
                arg = call.args[0]
                checked += 1
                if declared == "list" and isinstance(arg, ast.Dict):
                    problems.append(
                        f"{fname}::{func.name} 声明 `list` 响应，却返回 dict 字面量"
                        " —— 序列化会抛 ResponseValidationError（500）"
                    )
                elif declared in self._PAGED and isinstance(arg, (ast.List, ast.Tuple)):
                    problems.append(
                        f"{fname}::{func.name} 声明 `{declared}` 响应，却返回 list 字面量"
                    )
        self.assertGreaterEqual(checked, 1, "没有找到任何带字面量返回的路由，守卫可能已失效")
        self.assertEqual(problems, [], "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
