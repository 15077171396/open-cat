"""管理后台路由（聚合入口）：按域拆分为 9 个模块，本文件按原注册顺序 import 聚合
（import 即执行各模块的 @router 装饰器完成路由注册）。
FastAPI 按注册顺序匹配路由（batch-delete 必须先于 {tutorial_id}），域间顺序由本文件 import 顺序决定。
"""

from app.api._admin_shared import router  # noqa: F401  供 app/api/router.py 以 admin.router 挂载

# ⚠️ 导入顺序 = 路由注册顺序，请勿随意调整
from app.api import (  # noqa: F401
    admin_menus,
    # 菜单管理（展示层：名称/图标/排序/可见性；路由与权限码仍由代码定义）
    admin_menu_settings,
    # 数据字典（类型 + 字典项）
    admin_dictionaries,
    # 岗位目录（组织架构）
    admin_positions,
    # 监控管理（在线用户 / 服务器 / 缓存，只读）
    admin_monitor,
    # 顶栏待办聚合：只读，类别按当前管理员的权限裁剪
    admin_todos,
    # 版本与升级：当前版本 / 迁移状态 / 最新 release（只读运维）
    admin_version,
    # 备份管理：只读列表 / 下载（恢复仍在宿主机脚本执行）
    admin_backups,
    admin_ops,
    # 列表导出（CSV）：数据外带，权限与各自列表页一致，每次导出写审计
    admin_export,
    admin_tutorials,
    # 分类分组：分类页左栏（组）与右栏（分类）的组织结构
    admin_category_groups,
    admin_users,
    # 统一回收站：教程 / 评论 / 用户三类软删除记录的聚合、恢复与到期清理
    admin_recycle_bin,
    # 登录设备：查看/下线自己的会话 + 强制下线某个用户（改密、疑似盗号时用）
    admin_sessions,
    admin_redeem_codes,
    admin_announcements,
    admin_feedbacks,
    # 站内消息通知：给用户发系统通知（全站 / 指定用户），消息中心「系统通知」的数据来源
    admin_messages,
    admin_moderation,
    admin_settings,
    admin_ai_models,
    # 素材库：本地上传 / 图库导入 / AI 图的统一浏览与复用
    admin_materials,
    # 图片搜索：编辑器图库检索与转存（权限复用内容编辑，见 core/rbac.py）
    admin_image_search,
    # AI 写作：SSE 执行流 + 提示词配置（权限复用内容编辑 / 模型管理，见 core/rbac.py）
    admin_ai_writing,
    # 登录管理：从「系统设置 → 登录与短信」抽出的独立模块（读写范围与权限码都独立）
    admin_login_settings,
    # 法律文档：从「系统设置 → 法律文档」抽出的独立模块（对外合规文本，单独授权）
    admin_legal_documents,
    # 邮件通知：SMTP 通道（验证码发信）从「系统设置 → 邮件通知」抽出的独立模块
    admin_mail_settings,
    # 支付与渠道：从「系统设置 → 支付与渠道」抽出的独立模块（资金链路配置，单独授权）
    admin_payment_settings,
    admin_payments,
    admin_comments,
)

__all__ = ["router"]
