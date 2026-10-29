"""补充种子数据 - 工具/节点/扩展/用户/认证申请"""

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from app.database import async_session, engine
from app.models.user import User
from app.models.tool import Tool
from app.models.node import Node, Plugin, PluginSource
from app.models.auth_request import AuthRequest
from app.models.download import Download
from app.models.resource import Resource, ResourceNetdiskLink
from app.core.security import hash_password


async def seed_more():
    async with async_session() as db:
        # ========== 1. 用户数据 ==========
        users_data = [
            {"account": "13800138001", "password": "123456", "role": "user", "free_used": 3600},
            {"account": "13800138002", "password": "123456", "role": "user", "free_used": 7200},
            {"account": "user@example.com", "password": "123456", "role": "user", "free_used": 0},
            {"account": "vip@example.com", "password": "123456", "role": "vip", "free_used": 0,
             "vip_expire": datetime.now(timezone.utc) + timedelta(days=180)},
            {"account": "vip2@example.com", "password": "123456", "role": "vip", "free_used": 0,
             "vip_expire": datetime.now(timezone.utc) + timedelta(days=90)},
        ]
        for u in users_data:
            result = await db.execute(select(User).where(User.account == u["account"]))
            if not result.scalar_one_or_none():
                user = User(
                    account=u["account"],
                    password_hash=hash_password(u["password"]),
                    role=u["role"],
                    free_used_seconds=u["free_used"],
                    vip_expire_time=u.get("vip_expire"),
                    # 必须显式写：该列无 server_default，漏写会落 NULL，
                    # 前台只能回退 created_at 算免费时长（时区语义不同源，见 timer_service）
                    free_started_at=datetime.now(timezone.utc),
                )
                db.add(user)
                print(f"创建用户: {u['account']} ({u['role']})")

        # ========== 2. 在线工具 ==========
        tools_data = [
            {
                "name": "AI 提示词生成器",
                "description": "输入主题与风格，自动生成结构化 AI 绘画提示词",
                "category": "Prompt",
                "tool_type": "ai",
                "ai_config": {
                    "model": "deepseek",
                    # 只生成 1 组：早期模板要 3 组，用户反馈「不需要那么多份」。
                    # 格式用 **正向提示词** / **反向提示词** 两个标记，前端
                    # utils/promptResult.ts 据此解析（标题可有可无，模型飘了也能拆）。
                    "prompt_template": (
                        "你是 AI 绘画提示词工程师。根据以下信息生成 1 组高质量英文 "
                        "Stable Diffusion 提示词。\n\n"
                        "主题：{topic}\n风格：{style}\n\n"
                        "严格按以下格式输出，只要这一组，不要输出多组，也不要任何寒暄、"
                        "解释、开场白或结尾总结：\n\n"
                        "**正向提示词**\n"
                        "英文 prompt，逗号分隔的 tags\n\n"
                        "**反向提示词**\n"
                        "english, negative, prompt, tags\n"
                    ),
                },
                "form_schema": {
                    "fields": [
                        {"name": "topic", "label": "主题描述", "type": "text", "placeholder": "如：一个站在樱花树下的少女"},
                        {"name": "style", "label": "艺术风格", "type": "text", "placeholder": "如：二次元/写实/水彩/油画"},
                    ]
                },
            },
            {
                "name": "图片压缩工具",
                "description": "在浏览器本地压缩 JPG/PNG/WebP 图片，支持质量调节、尺寸缩放与格式转换。图片不会上传服务器，下载或离开页面后自动清除",
                "category": "图像",
                "tool_type": "frontend",
                "ai_config": None,
                "form_schema": None,
            },
        ]
        for t in tools_data:
            result = await db.execute(select(Tool).where(Tool.name == t["name"]))
            if not result.scalar_one_or_none():
                db.add(Tool(**t, is_published=True))
                print(f"创建工具: {t['name']}")

        # ========== 3. 节点百科 ==========
        nodes_data = [
            {"name": "KSampler", "category": "采样器", "description": "ComfyUI 中最核心的采样节点，用于在潜空间中根据模型和条件逐步去噪，生成最终图像。",
             "inputs": [{"name": "model", "type": "MODEL", "desc": "要使用的扩散模型"}, {"name": "positive", "type": "CONDITIONING", "desc": "正向条件"}, {"name": "negative", "type": "CONDITIONING", "desc": "反向条件"}, {"name": "latent_image", "type": "LATENT", "desc": "输入的潜空间图像"}],
             "outputs": [{"name": "LATENT", "type": "LATENT", "desc": "采样后的潜空间图像"}],
             "params": [{"name": "seed", "desc": "随机种子"}, {"name": "steps", "desc": "采样步数，通常 20-30 步"}, {"name": "cfg", "desc": "提示词相关性"}, {"name": "sampler_name", "desc": "采样器算法"}, {"name": "scheduler", "desc": "调度器"}, {"name": "denoise", "desc": "去噪强度"}],
             "examples": {"text": "文生图工作流中，KSampler 连接 Checkpoint 模型、CLIP Text Encode 和 Empty Latent Image，输出连接 VAE Decode。"}},
            {"name": "Checkpoint 加载器", "category": "加载器", "description": "用于加载包含模型权重、CLIP 和 VAE 的完整 Checkpoint 文件（.safetensors 或 .ckpt）。",
             "inputs": [], "outputs": [{"name": "MODEL", "type": "MODEL", "desc": "扩散模型"}, {"name": "CLIP", "type": "CLIP", "desc": "文本编码器"}, {"name": "VAE", "type": "VAE", "desc": "变分自编码器"}],
             "params": [{"name": "ckpt_name", "desc": "选择模型文件"}], "examples": {"text": "将 MODEL 接到 KSampler，CLIP 接到提示词编码器，VAE 接到 VAE Decode。"}},
            {"name": "CLIP Text Encode", "category": "条件", "description": "将文本提示词通过 CLIP 编码为 CONDITIONING 数据，供 KSampler 使用。",
             "inputs": [{"name": "clip", "type": "CLIP", "desc": "来自 Checkpoint 加载器的 CLIP"}], "outputs": [{"name": "CONDITIONING", "type": "CONDITIONING", "desc": "编码后的条件"}],
             "params": [{"name": "text", "desc": "输入提示词"}], "examples": {"text": "正向输入 '1girl, best quality'，反向输入 'low quality, blurry'，分别连接 KSampler。"}},
            {"name": "Empty Latent Image", "category": "潜空间", "description": "创建一个指定尺寸的空白潜空间图像，作为文生图的起点。",
             "inputs": [], "outputs": [{"name": "LATENT", "type": "LATENT", "desc": "空白潜空间图像"}],
             "params": [{"name": "width", "desc": "图像宽度"}, {"name": "height", "desc": "图像高度"}, {"name": "batch_size", "desc": "生成数量"}], "examples": {"text": "设置 512x768，输出连接 KSampler 的 latent_image。"}},
            {"name": "VAE Decode", "category": "图像", "description": "将 KSampler 生成的潜空间图像解码为可视化的像素图像。",
             "inputs": [{"name": "samples", "type": "LATENT", "desc": "来自 KSampler"}, {"name": "vae", "type": "VAE", "desc": "来自 Checkpoint"}], "outputs": [{"name": "IMAGE", "type": "IMAGE", "desc": "解码后的像素图像"}],
             "params": [], "examples": {"text": "KSampler 输出连接 samples，Checkpoint VAE 连接 vae。"}},
            {"name": "Load Image", "category": "图像", "description": "加载本地图像文件，常用于图生图、ControlNet 等场景。",
             "inputs": [], "outputs": [{"name": "IMAGE", "type": "IMAGE", "desc": "加载的图像"}, {"name": "MASK", "type": "MASK", "desc": "遮罩信息"}],
             "params": [{"name": "image", "desc": "选择图片文件"}], "examples": {"text": "图生图中，输出连接 VAE Encode 转换为潜空间。"}},
            {"name": "VAE Encode", "category": "潜空间", "description": "将像素图像编码为潜空间表示，是图生图工作流的关键节点。",
             "inputs": [{"name": "pixels", "type": "IMAGE", "desc": "来自 Load Image"}, {"name": "vae", "type": "VAE", "desc": "来自 Checkpoint"}], "outputs": [{"name": "LATENT", "type": "LATENT", "desc": "编码后的潜空间"}],
             "params": [], "examples": {"text": "Load Image 连接 pixels，输出连接 KSampler。"}},
            {"name": "Save Image", "category": "图像", "description": "将生成的图像保存到 output 目录，可设置文件名前缀。",
             "inputs": [{"name": "images", "type": "IMAGE", "desc": "要保存的图像"}], "outputs": [],
             "params": [{"name": "filename_prefix", "desc": "文件名前缀"}], "examples": {"text": "VAE Decode 输出连接 images，设置前缀 'ComfyUI'。"}},
            {"name": "Preview Image", "category": "工具", "description": "在工作流界面中临时预览图像，不会保存到文件。",
             "inputs": [{"name": "images", "type": "IMAGE", "desc": "要预览的图像"}], "outputs": [],
             "params": [], "examples": {"text": "调试时使用 Preview Image 快速查看中间结果。"}},
            {"name": "ControlNet 应用", "category": "条件", "description": "将 ControlNet 模型应用到条件控制中，实现姿态、线稿、深度等精确控制。",
             "inputs": [{"name": "conditioning", "type": "CONDITIONING", "desc": "来自 CLIP"}, {"name": "control_net", "type": "CONTROL_NET", "desc": "ControlNet 模型"}, {"name": "image", "type": "IMAGE", "desc": "控制图像"}], "outputs": [{"name": "CONDITIONING", "type": "CONDITIONING", "desc": "应用后的条件"}],
             "params": [{"name": "strength", "desc": "控制强度 0-1"}], "examples": {"text": "OpenPose 预处理图像连接 image，输出接入 KSampler 的 positive。"}},
            {"name": "Latent Upscale", "category": "潜空间", "description": "在潜空间中对图像进行放大，常用于高清修复工作流。",
             "inputs": [{"name": "samples", "type": "LATENT", "desc": "输入潜空间"}], "outputs": [{"name": "LATENT", "type": "LATENT", "desc": "放大后的潜空间"}],
             "params": [{"name": "upscale_method", "desc": "放大方法"}, {"name": "width", "desc": "目标宽度"}, {"name": "height", "desc": "目标高度"}, {"name": "crop", "desc": "是否裁剪"}], "examples": {"text": "KSampler 输出连接此节点放大，再接入第二个 KSampler 精修。"}},
            {"name": "Lora Loader", "category": "加载器", "description": "加载 LoRA 模型并对主模型进行微调，实现风格定制。",
             "inputs": [{"name": "model", "type": "MODEL", "desc": "主模型"}, {"name": "clip", "type": "CLIP", "desc": "CLIP 模型"}], "outputs": [{"name": "MODEL", "type": "MODEL", "desc": "微调后的模型"}, {"name": "CLIP", "type": "CLIP", "desc": "微调后的 CLIP"}],
             "params": [{"name": "lora_name", "desc": "LoRA 文件名"}, {"name": "strength_model", "desc": "模型强度"}, {"name": "strength_clip", "desc": "CLIP 强度"}], "examples": {"text": "Checkpoint MODEL 连接此节点，输出连接 KSampler。"}},
        ]
        for n in nodes_data:
            result = await db.execute(select(Node).where(Node.name == n["name"]))
            if not result.scalar_one_or_none():
                db.add(Node(**n))
                print(f"创建节点: {n['name']}")

        # ========== 4. 扩展数据 ==========
        plugins_data = [
            {
                "name": "ComfyUI Manager", "author_name": "ltdrdata", "category": "ComfyUI 节点",
                "description": "ComfyUI 最强大的管理扩展，一键安装/更新/删除自定义节点和模型。",
                "features": ["一键安装自定义节点", "模型管理", "工作流管理", "自动依赖检测", "节点搜索"],
                "install_steps": ["进入 ComfyUI/custom_nodes 目录", "git clone 仓库", "重启 ComfyUI", "侧边栏出现 Manager 按钮"],
                "sources": [{"source_name": "github", "url": "https://github.com/ltdrdata/ComfyUI-Manager"}, {"source_name": "official", "url": "https://github.com/ltdrdata/ComfyUI-Manager"}],
            },
            {
                "name": "ControlNet Aux", "author_name": "Fannovel16", "category": "ComfyUI 节点",
                "description": "ControlNet 预处理器节点集合，包含 OpenPose、Canny、深度图等多种预处理器。",
                "features": ["OpenPose 姿态检测", "Canny 边缘检测", "深度图生成", "线稿提取", "分割图"],
                "install_steps": ["进入 custom_nodes 目录", "git clone 仓库", "pip install -r requirements.txt", "重启 ComfyUI"],
                "sources": [{"source_name": "github", "url": "https://github.com/Fannovel16/comfyui_controlnet_aux"}],
            },
            {
                "name": "必应搜索", "author_name": "Coze 官方", "category": "Coze 扩展",
                "description": "Coze 平台的必应搜索扩展，让智能体具备实时网络搜索能力。",
                "features": ["实时搜索", "多语言支持", "结果摘要", "安全过滤"],
                "install_steps": ["在 Coze 平台创建智能体", "扩展市场搜索'必应搜索'", "点击安装", "在对话中调用"],
                "sources": [{"source_name": "official", "url": "https://www.coze.cn"}],
            },
            {
                "name": "图像生成", "author_name": "Coze 官方", "category": "Coze 扩展",
                "description": "Coze 图像生成扩展，支持文生图、图生图等多种模式。",
                "features": ["文生图", "图生图", "风格迁移", "批量生成"],
                "install_steps": ["在 Coze 平台创建智能体", "扩展市场搜索'图像生成'", "点击安装"],
                "sources": [{"source_name": "official", "url": "https://www.coze.cn"}],
            },
            {
                "name": "ComfyUI-Impact-Pack", "author_name": "ltdrdata", "category": "ComfyUI 节点",
                "description": "强大的 ComfyUI 综合工具包，包含检测器、分割器、管道等多种高级节点。",
                "features": ["人脸检测", "目标分割", "Detailer 管道", "区域提示词", "批量处理"],
                "install_steps": ["进入 custom_nodes 目录", "git clone 仓库", "安装依赖", "重启 ComfyUI"],
                "sources": [{"source_name": "github", "url": "https://github.com/ltdrdata/ComfyUI-Impact-Pack"}],
            },
            {
                "name": "ComfyUI-Easy-Use", "author_name": "yolain", "category": "ComfyUI 节点",
                "description": "简化 ComfyUI 工作流创建的便捷节点集，减少手动连线复杂度。",
                "features": ["一键全连接", "提示词模板", "快速预设", "工作流简化"],
                "install_steps": ["进入 custom_nodes 目录", "git clone 仓库", "重启 ComfyUI"],
                "sources": [{"source_name": "github", "url": "https://github.com/yolain/ComfyUI-Easy-Use"}, {"source_name": "quark", "url": "https://pan.quark.cn/s/easy-use", "extract_code": "easy"}],
            },
            {
                "name": "通义千问", "author_name": "Coze 官方", "category": "Coze 扩展",
                "description": "接入通义千问大模型，增强智能体的对话与推理能力。",
                "features": ["多轮对话", "代码生成", "文档理解", "多语言"],
                "install_steps": ["在 Coze 平台创建智能体", "扩展市场搜索'通义千问'", "配置 API Key", "点击安装"],
                "sources": [{"source_name": "official", "url": "https://www.coze.cn"}],
            },
            {
                "name": "WD14 Tagger", "author_name": "pythongosssss", "category": "ComfyUI 节点",
                "description": "自动给图像打标签的反向提示词工具，基于 WD14 模型自动识别图像内容。",
                "features": ["自动标签", "批量处理", "多模型支持", "阈值控制"],
                "install_steps": ["进入 custom_nodes 目录", "git clone 仓库", "下载模型到指定目录", "重启 ComfyUI"],
                "sources": [{"source_name": "github", "url": "https://github.com/pythongosssss/ComfyUI-WD14-Tagger"}],
            },
        ]
        for p in plugins_data:
            result = await db.execute(select(Plugin).where(Plugin.name == p["name"]))
            if not result.scalar_one_or_none():
                sources = p.pop("sources")
                plugin = Plugin(**p)
                db.add(plugin)
                await db.flush()
                for s in sources:
                    db.add(PluginSource(plugin_id=plugin.id, **s))
                print(f"创建扩展: {p['name']}")

        # ========== 5. 补充资源数据 ==========
        extra_resources_data = [
            {
                "name": "SDXL 基础模型包",
                "description": "包含 SDXL 1.0 Base + Refiner 的完整模型包，适合高分辨率图像生成。",
                "resource_type": "model",
                "file_size": "13.4 GB",
                "content_list": ["SDXL Base 1.0", "SDXL Refiner 1.0", "VAE ft_mse", "配置文件"],
                "usage_notes": ["下载后放入 models/checkpoints", "确保显存 ≥ 8GB", "推荐使用 ComfyUI 加载"],
                "links": [
                    {"platform": "baidu", "url": "https://pan.baidu.com/s/res6", "extract_code": "sdxl"},
                    {"platform": "quark", "url": "https://pan.quark.cn/s/res6", "extract_code": "sdx1"},
                ],
            },
            {
                "name": "Midjourney 提示词大全",
                "description": "整理 1000+ 高质量 Midjourney 提示词，覆盖风景、人物、艺术风格等大类。",
                "resource_type": "doc",
                "file_size": "8.2 MB",
                "content_list": ["风景提示词 300+", "人物提示词 200+", "艺术风格 250+", "负面词模板 100+"],
                "usage_notes": ["解压 PDF 文档", "按章节查找", "可直接复制使用"],
                "links": [
                    {"platform": "aliyun", "url": "https://www.aliyundrive.com/s/res7", "extract_code": "mjpw"},
                ],
            },
            {
                "name": "Whisper 语音识别工具",
                "description": "OpenAI Whisper 本地化部署工具包，支持多语言语音转文字。",
                "resource_type": "tool",
                "file_size": "2.1 GB",
                "content_list": ["Whisper 主程序", "Small/Base/Large 模型", "WebUI 界面", "FFmpeg 依赖"],
                "usage_notes": ["解压后运行 install.bat", "首次启动自动下载模型", "支持 GPU 加速"],
                "links": [
                    {"platform": "baidu", "url": "https://pan.baidu.com/s/res8", "extract_code": "whsp"},
                    {"platform": "mobile", "url": "https://yun.139.com/s/res8", "extract_code": "wh01"},
                ],
            },
            {
                "name": "ChatGLM3 本地部署源码",
                "description": "ChatGLM3 模型本地部署完整源码，含 WebUI、API 服务和量化方案。",
                "resource_type": "source",
                "file_size": "450 MB",
                "content_list": ["源码工程", "WebUI 模块", "API 服务", "量化脚本", "部署文档"],
                "usage_notes": ["克隆仓库", "安装依赖", "下载模型权重", "运行 python cli_demo.py"],
                "links": [
                    {"platform": "github", "url": "https://github.com/example/chatglm3", "extract_code": None},
                    {"platform": "baidu", "url": "https://pan.baidu.com/s/res9", "extract_code": "glm03"},
                ],
            },
            {
                "name": "COCO 数据集子集",
                "description": "COCO 数据集精选子集，含 5000 张标注图像，适合目标检测训练。",
                "resource_type": "dataset",
                "file_size": "3.8 GB",
                "content_list": ["图像 5000 张", "标注 JSON", "类别列表", "训练脚本示例"],
                "usage_notes": ["下载并解压", "将 annotations 放入 data 目录", "使用 train.py 启动训练"],
                "links": [
                    {"platform": "quark", "url": "https://pan.quark.cn/s/res10", "extract_code": "coco5"},
                ],
            },
            {
                "name": "Stable Diffusion WebUI 一键包",
                "description": "AUTOMATIC1111 SD WebUI 一键整合包，含常用扩展和中文界面。",
                "resource_type": "tool",
                "file_size": "15.6 GB",
                "content_list": ["SD WebUI 主程序", "SD 1.5 模型", "常用扩展包", "中文汉化", "启动脚本"],
                "usage_notes": ["解压到 D 盘根目录", "运行 webui-user.bat", "浏览器访问 127.0.0.1:7860"],
                "links": [
                    {"platform": "baidu", "url": "https://pan.baidu.com/s/res11", "extract_code": "sdwui"},
                    {"platform": "quark", "url": "https://pan.quark.cn/s/res11", "extract_code": "sdw1"},
                ],
            },
            {
                "name": "ControlNet 模型合集",
                "description": "完整 ControlNet 1.1 模型合集，支持 OpenPose、Canny、Depth 等多种控制。",
                "resource_type": "model",
                "file_size": "7.5 GB",
                "content_list": ["OpenPose 模型", "Canny 模型", "Depth 模型", "Lineart 模型", "Tile 模型"],
                "usage_notes": ["下载后放入 models/ControlNet", "在 WebUI 启用 ControlNet 扩展", "选择对应预处理器"],
                "links": [
                    {"platform": "quark", "url": "https://pan.quark.cn/s/res12", "extract_code": "cn111"},
                    {"platform": "aliyun", "url": "https://www.aliyundrive.com/s/res12", "extract_code": "cnmod"},
                ],
            },
            {
                "name": "Python AI 学习笔记合集",
                "description": "从入门到进阶的 Python + AI 学习笔记合集，含代码示例与练习题。",
                "resource_type": "doc",
                "file_size": "24.5 MB",
                "content_list": ["Python 基础笔记", "NumPy 教程", "PyTorch 入门", "实战项目案例", "练习题答案"],
                "usage_notes": ["下载 PDF 合集", "按章节学习", "配合示例代码练习"],
                "links": [
                    {"platform": "aliyun", "url": "https://www.aliyundrive.com/s/res13", "extract_code": "pyai0"},
                ],
            },
            {
                "name": "图像超分辨率工具包",
                "description": "基于 Real-ESRGAN 的图像超分辨率工具，支持 4x/8x 放大和视频修复。",
                "resource_type": "tool",
                "file_size": "850 MB",
                "content_list": ["REalesrgan 主程序", "预训练模型", "WebUI 界面", "批量处理脚本"],
                "usage_notes": ["解压后运行 install.bat", "将图片拖入界面", "选择放大倍数后点击处理"],
                "links": [
                    {"platform": "baidu", "url": "https://pan.baidu.com/s/res14", "extract_code": "esrgn"},
                    {"platform": "mobile", "url": "https://yun.139.com/s/res14", "extract_code": "es001"},
                ],
            },
            {
                "name": "LoRA 训练数据集模板",
                "description": "面向 LoRA 训练的数据集模板，包含 5 种主题的标注图像与训练配置。",
                "resource_type": "dataset",
                "file_size": "1.2 GB",
                "content_list": ["人物主题数据 200 张", "风格主题数据 150 张", "concept 主题 100 张", "标注文件", "训练配置"],
                "usage_notes": ["解压数据集", "修改 dataset.toml 路径", "运行 kohya_ss 训练"],
                "links": [
                    {"platform": "quark", "url": "https://pan.quark.cn/s/res15", "extract_code": "lora0"},
                ],
            },
            {
                "name": "AI 绘画扩展合集",
                "description": "SD WebUI 常用扩展合集，一键安装后即可使用热门功能。",
                "resource_type": "other",
                "file_size": "320 MB",
                "content_list": ["OpenPose Editor", "Prompt All-in-One", "CiviTAI Browser", "Image Browser", "Deforum"],
                "usage_notes": ["解压到 extensions 目录", "重启 WebUI", "在 Settings 中启用对应扩展"],
                "links": [
                    {"platform": "baidu", "url": "https://pan.baidu.com/s/res16", "extract_code": "ext01"},
                    {"platform": "quark", "url": "https://pan.quark.cn/s/res16", "extract_code": "ext02"},
                ],
            },
        ]
        for r_data in extra_resources_data:
            result = await db.execute(select(Resource).where(Resource.name == r_data["name"]))
            if not result.scalar_one_or_none():
                links = r_data.pop("links")
                resource = Resource(**r_data)
                db.add(resource)
                await db.flush()
                for idx, link in enumerate(links):
                    db.add(ResourceNetdiskLink(
                        resource_id=resource.id,
                        platform=link["platform"],
                        url=link["url"],
                        extract_code=link["extract_code"],
                        sort_order=idx,
                    ))
                print(f"创建资源: {r_data['name']}")

        # ========== 6. 认证申请 ==========
        # 获取一个普通用户
        result = await db.execute(select(User).where(User.account == "13800138001"))
        user1 = result.scalar_one_or_none()
        if user1:
            result = await db.execute(select(AuthRequest).where(AuthRequest.user_id == user1.id))
            if not result.scalar_one_or_none():
                auth_req = AuthRequest(
                    user_id=user1.id,
                    star_code="12345678",
                    status="pending",
                )
                db.add(auth_req)
                user1.pending_auth_code = "12345678"
                print(f"创建认证申请: {user1.account} -> 星球号 12345678")

        # ========== 6. 下载记录 ==========
        result = await db.execute(select(User).where(User.account == "user@example.com"))
        user2 = result.scalar_one_or_none()
        result = await db.execute(select(Resource).limit(3))
        resources = result.scalars().all()
        if user2 and resources:
            for res in resources:
                result = await db.execute(select(Download).where(Download.user_id == user2.id, Download.resource_id == res.id))
                if not result.scalars().first():
                    db.add(Download(user_id=user2.id, resource_id=res.id, file_size=res.file_size))
            print(f"为 {user2.account} 创建下载记录")

        await db.commit()
        print("\n补充数据创建完成!")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed_more())
