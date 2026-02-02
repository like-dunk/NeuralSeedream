"""
命令行接口
"""

import argparse
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import threading
import warnings
from pathlib import Path
from typing import List, Optional, Union

# 屏蔽 Python 版本相关的 FutureWarning（Google 库会警告 Python 3.9 已过期）
warnings.filterwarnings("ignore", category=FutureWarning, module="google")
warnings.filterwarnings("ignore", message=".*Python version.*")
warnings.filterwarnings("ignore", message=".*end of life.*")

from .api_client import APIClient
from .config import ConfigManager
from .engine import GenerationEngine
from .exceptions import GeneratorError
from .gcs_uploader import GCSUploader
from .image_selector import ImageSelector
from .midjourney_client import MidjourneyClient
from .moss_uploader import MOSSUploader
from .openrouter_image_client import OpenRouterImageClient
from .output_manager import OutputManager
from .seedream_client import SeedreamClient
from .state_manager import StateManager
from .template_engine import TemplateEngine
from .text_generator import TextGenerator


def check_gcs_dependencies() -> bool:
    """
    检查 GCS 相关依赖是否已安装
    
    Returns:
        True 如果所有依赖都已安装
    """
    # 检查 google-cloud-storage Python 包
    try:
        import google.cloud.storage
        return True
    except ImportError:
        return False


def check_gcloud_auth() -> bool:
    """
    检查是否已通过 gcloud 登录
    
    Returns:
        True 如果已登录
    """
    # 检查应用默认凭证文件是否存在
    home = Path.home()
    adc_path = home / ".config" / "gcloud" / "application_default_credentials.json"
    
    if adc_path.exists():
        return True
    
    # Windows 路径
    adc_path_win = home / "AppData" / "Roaming" / "gcloud" / "application_default_credentials.json"
    if adc_path_win.exists():
        return True
    
    return False


def install_gcs_dependencies():
    """安装 GCS 相关依赖"""
    print("📦 正在安装 google-cloud-storage...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "google-cloud-storage"])
        print("✅ google-cloud-storage 安装成功")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ 安装失败: {e}")
        return False


def install_gcloud_cli():
    """安装 gcloud CLI"""
    system = platform.system()
    
    if system == "Darwin":  # macOS
        # 检查是否有 brew
        if shutil.which("brew"):
            print("📦 正在通过 Homebrew 安装 google-cloud-sdk...")
            try:
                subprocess.check_call(["brew", "install", "google-cloud-sdk"])
                
                # Homebrew 安装后需要添加 PATH
                gcloud_bin = "/opt/homebrew/share/google-cloud-sdk/bin"
                if os.path.exists(gcloud_bin):
                    # 添加到当前进程的 PATH
                    os.environ["PATH"] = f"{gcloud_bin}:{os.environ.get('PATH', '')}"
                    
                    # 添加到 shell 配置文件
                    shell_rc = Path.home() / ".zshrc"
                    if not shell_rc.exists():
                        shell_rc = Path.home() / ".bashrc"
                    
                    export_line = f'export PATH="{gcloud_bin}:$PATH"'
                    
                    # 检查是否已添加
                    if shell_rc.exists():
                        content = shell_rc.read_text()
                        if gcloud_bin not in content:
                            with open(shell_rc, "a") as f:
                                f.write(f"\n# Google Cloud SDK\n{export_line}\n")
                            print(f"✅ 已添加 gcloud 到 PATH ({shell_rc.name})")
                    
                print("✅ google-cloud-sdk 安装成功")
                return True
            except subprocess.CalledProcessError:
                pass
        
        print("❌ 请手动安装 gcloud CLI:")
        print("   brew install google-cloud-sdk")
        print("   或访问: https://cloud.google.com/sdk/docs/install")
        return False
    
    elif system == "Linux":
        print("❌ 请手动安装 gcloud CLI:")
        print("   curl https://sdk.cloud.google.com | bash")
        print("   或访问: https://cloud.google.com/sdk/docs/install")
        return False
    
    elif system == "Windows":
        print("❌ 请手动安装 gcloud CLI:")
        print("   访问: https://cloud.google.com/sdk/docs/install")
        return False
    
    return False


def setup_gcs_auth():
    """设置 GCS 认证"""
    print("\n🔐 需要登录 Google Cloud 账号来访问 GCS")
    print("   将打开浏览器进行登录...\n")
    
    try:
        subprocess.check_call(["gcloud", "auth", "application-default", "login"])
        print("\n✅ 登录成功！")
        return True
    except subprocess.CalledProcessError:
        print("\n❌ 登录失败，请手动运行: gcloud auth application-default login")
        return False
    except FileNotFoundError:
        print("\n❌ 未找到 gcloud 命令")
        return False


def ensure_gcs_ready(bucket_name: str) -> bool:
    """
    确保 GCS 环境已准备好
    
    Args:
        bucket_name: GCS bucket 名称
        
    Returns:
        True 如果环境已准备好
    """
    print(f"\n🔍 检查 GCS 环境 (bucket: {bucket_name})...")
    
    # 1. 检查 Python 包
    if not check_gcs_dependencies():
        print("⚠️  未安装 google-cloud-storage，正在自动安装...")
        if not install_gcs_dependencies():
            return False
    
    # 2. 检查 gcloud CLI
    # 先检查 Homebrew 安装路径
    gcloud_brew_path = "/opt/homebrew/share/google-cloud-sdk/bin/gcloud"
    if os.path.exists(gcloud_brew_path):
        # 添加到当前进程的 PATH
        gcloud_bin = "/opt/homebrew/share/google-cloud-sdk/bin"
        if gcloud_bin not in os.environ.get("PATH", ""):
            os.environ["PATH"] = f"{gcloud_bin}:{os.environ.get('PATH', '')}"
    
    if not shutil.which("gcloud"):
        print("⚠️  未安装 gcloud CLI，正在自动安装...")
        if not install_gcloud_cli():
            return False
    
    # 3. 检查是否已登录
    if not check_gcloud_auth():
        print("⚠️  未登录 Google Cloud，正在打开登录页面...")
        if not setup_gcs_auth():
            return False
    
    print("✅ GCS 环境检查通过\n")
    return True


def setup_logging(level: str = "INFO", log_file: Optional[Path] = None):
    """配置日志"""
    log_level = getattr(logging, level.upper(), logging.INFO)
    
    handlers = [logging.StreamHandler(sys.stdout)]
    
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    
    # 简化日志格式
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )
    
    # 降低第三方库的日志级别
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("MOSS_pro_utils").setLevel(logging.WARNING)
    logging.getLogger("ai_image_generator.moss_uploader").setLevel(logging.WARNING)


def create_engine(
    config_path: Path,
    template_path: Path,
    api_key: Optional[str] = None,
) -> GenerationEngine:
    """创建生成引擎"""
    # 配置管理器
    config_manager = ConfigManager(
        config_path=config_path,
        template_path=template_path,
    )
    
    # 加载配置
    global_config = config_manager.load_global_config()
    template_config = config_manager.load_template_config()
    
    # 如果提供了API密钥，覆盖配置
    if api_key:
        global_config.api_key = api_key
    
    # 模板引擎
    prompts_dir = None
    # 根据模式获取 prompt 目录
    if template_config.mode == "scene_generation" and template_config.scene_prompts:
        prompts_dir = config_manager.get_resolved_path("scene_prompts", template_config.scene_prompts.source_dir)
    elif template_config.mode == "subject_transfer" and template_config.transfer_prompts:
        prompts_dir = config_manager.get_resolved_path("transfer_prompts", template_config.transfer_prompts.source_dir)
    template_engine = TemplateEngine(template_dir=prompts_dir)
    
    # 图片选择器
    image_selector = ImageSelector()
    
    # 根据 image_model 判断是否使用 OpenRouter
    image_model = template_config.image_model
    is_openrouter = image_model.startswith("openrouter/")
    
    # 警告：Midjourney 不适合主体迁移任务
    if image_model == "midjourney" and template_config.mode == "subject_transfer":
        logging.warning("⚠️ Midjourney 不适合主体迁移任务！")
        logging.warning("   Midjourney 的 image-to-image 是风格融合，无法精确保留产品主体。")
        logging.warning("   建议使用 nano-banana-pro 或 seedream/4.5-edit 进行主体迁移。")
    
    # 根据 storage_service 和 image_model 选择上传器
    # KieAI 模型必须使用 MOSS（KieAI API 需要直接访问 URL）
    # OpenRouter 模型可以选择 MOSS 或 GCS
    storage_service = global_config.storage_service
    
    if is_openrouter and storage_service == "gcs" and global_config.gcs_bucket_name:
        # OpenRouter + GCS
        if not ensure_gcs_ready(global_config.gcs_bucket_name):
            raise GeneratorError("GCS 环境未准备好，请按提示完成配置后重试")
        
        logging.info(f"📦 使用 Google Cloud Storage: {global_config.gcs_bucket_name}")
        uploader = GCSUploader(
            bucket_name=global_config.gcs_bucket_name,
            folder_path=global_config.gcs_folder_path,
            credentials_path=global_config.gcs_credentials_path or None,
            project_id=global_config.gcs_project_id or None,
            make_public=True,
        )
    else:
        # KieAI 或 OpenRouter + MOSS
        if not is_openrouter and storage_service == "gcs":
            logging.warning("⚠️ KieAI 模型不支持 GCS 存储，自动切换到 MOSS")
        uploader = MOSSUploader(
            base_url=global_config.moss_base_url,
            access_key_id=global_config.moss_access_key_id,
            access_key_secret=global_config.moss_access_key_secret,
            bucket_name=global_config.moss_bucket_name,
            expire_seconds=global_config.moss_expire_seconds,
        )
    
    # 根据 image_model 选择图片生成客户端
    # 所有生图模型统一在 templates/generation_template.json 的 image_model 字段配置
    api_client: Union[APIClient, OpenRouterImageClient, SeedreamClient, MidjourneyClient]
    
    if image_model == "openrouter/seedream-4.5":
        # OpenRouter Seedream 4.5
        logging.info(f"📡 使用 OpenRouter Seedream 4.5 图片生成服务")
        if global_config.openrouter_image_proxy:
            logging.info(f"📡 使用代理: {global_config.openrouter_image_proxy.split('@')[-1]}")
        api_client = OpenRouterImageClient(
            api_key=global_config.openrouter_image_api_key,
            base_url=global_config.openrouter_image_base_url,
            model="bytedance-seed/seedream-4.5",
            site_url=global_config.openrouter_image_site_url,
            site_name=global_config.openrouter_image_site_name,
            proxy=global_config.openrouter_image_proxy or None,
        )
    elif image_model == "openrouter/nano-banana-pro":
        # OpenRouter Nano Banana Pro (google/gemini-3-pro-image-preview)
        logging.info(f"📡 使用 OpenRouter Nano Banana Pro 图片生成服务")
        if global_config.openrouter_image_proxy:
            logging.info(f"📡 使用代理: {global_config.openrouter_image_proxy.split('@')[-1]}")
        api_client = OpenRouterImageClient(
            api_key=global_config.openrouter_image_api_key,
            base_url=global_config.openrouter_image_base_url,
            model="google/gemini-3-pro-image-preview",
            site_url=global_config.openrouter_image_site_url,
            site_name=global_config.openrouter_image_site_name,
            proxy=global_config.openrouter_image_proxy or None,
        )
    elif image_model == "seedream/4.5-edit":
        # KieAI Seedream 4.5 Edit
        logging.info(f"📡 使用 KieAI Seedream 4.5 Edit 图片生成服务")
        api_client = SeedreamClient(
            api_key=global_config.api_key,
            base_url=global_config.api_base_url,
            model="seedream/4.5-edit",
            poll_interval=global_config.poll_interval,
            max_wait=global_config.max_wait,
        )
    elif image_model == "midjourney":
        # KieAI Midjourney image-to-image
        logging.info(f"📡 使用 KieAI Midjourney 图片生成服务")
        api_client = MidjourneyClient(
            api_key=global_config.api_key,
            base_url=global_config.api_base_url,
            version=global_config.midjourney_version,
            speed=global_config.midjourney_speed,
            poll_interval=global_config.poll_interval,
            max_wait=global_config.max_wait,
        )
    else:
        # 默认使用 nano-banana-pro
        logging.info(f"📡 使用 KieAI 图片生成服务, model={image_model or global_config.model}")
        api_client = APIClient(
            api_key=global_config.api_key,
            base_url=global_config.api_base_url,
            model=image_model or global_config.model,
            poll_interval=global_config.poll_interval,
            max_wait=global_config.max_wait,
        )
    
    # 输出管理器
    output_base = config_manager.get_resolved_path("output_base", template_config.output.base_dir)
    output_manager = OutputManager(
        base_dir=output_base,
        run_name=template_config.name,
    )
    
    # 状态管理器（初始目录为输出目录）
    state_manager = StateManager(state_dir=output_base)
    
    # 文案生成器（如果配置了 OpenRouter）
    text_generator = None
    if global_config.openrouter_api_key:
        text_generator = TextGenerator(
            api_key=global_config.openrouter_api_key,
            base_url=global_config.openrouter_base_url,
            model=global_config.openrouter_model,
            site_url=global_config.openrouter_site_url,
            site_name=global_config.openrouter_site_name,
            proxy=global_config.openrouter_proxy or None,
            reference_min_samples=global_config.reference_min_samples,
            reference_max_samples=global_config.reference_max_samples,
        )
        
        # 加载 Few-shot 样本
        text_gen_cfg = template_config.text_generation
        if text_gen_cfg:
            pass
    
    # 创建引擎
    return GenerationEngine(
        config_manager=config_manager,
        template_engine=template_engine,
        image_selector=image_selector,
        moss_uploader=uploader,  # 可以是 MOSSUploader 或 GCSUploader
        api_client=api_client,
        output_manager=output_manager,
        state_manager=state_manager,
        text_generator=text_generator,
    )


def validate_specified_images_coverage(source_dirs: List[str], specified_images: List[str]) -> List[str]:
    """
    验证所有指定的产品图都能匹配到至少一个 source_dir
    
    Args:
        source_dirs: 产品图文件夹列表
        specified_images: 用户指定的产品图路径列表
        
    Returns:
        无法匹配的图片路径列表（空列表表示全部匹配）
    """
    if not specified_images:
        return []
    
    unmatched = []
    for img_path in specified_images:
        if not img_path or not img_path.strip():
            continue
        
        # 检查是否匹配任意一个 source_dir
        matched = False
        for source_dir in source_dirs:
            source_dir_normalized = source_dir.rstrip("/")
            if img_path.startswith(source_dir_normalized + "/"):
                matched = True
                break
        
        if not matched:
            unmatched.append(img_path)
    
    return unmatched


def get_product_source_dirs(template_path: Path) -> List[str]:
    """
    从模板配置中获取产品图源目录列表
    
    Args:
        template_path: 模板配置文件路径
        
    Returns:
        产品图源目录列表（即使配置的是单个字符串也返回列表）
    """
    with open(template_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    prod_cfg = data.get("product_images", {})
    source_dir = prod_cfg.get("source_dir", "")
    
    if isinstance(source_dir, list):
        # 过滤空字符串
        return [d for d in source_dir if d and d.strip()]
    elif source_dir and source_dir.strip():
        return [source_dir]
    else:
        return []


def update_template_source_dir(template_path: Path, new_source_dir: str) -> Path:
    """
    创建临时模板配置，更新产品图源目录
    
    Args:
        template_path: 原始模板配置文件路径
        new_source_dir: 新的产品图源目录
        
    Returns:
        临时模板配置文件路径
    """
    import tempfile
    
    with open(template_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    # 更新产品图源目录为单个字符串
    data["product_images"]["source_dir"] = new_source_dir
    
    # 智能过滤 specified_images：只保留属于当前文件夹的图片
    original_specified = data["product_images"].get("specified_images", [])
    if original_specified:
        # 确保是列表
        if isinstance(original_specified, str):
            original_specified = [original_specified] if original_specified.strip() else []
        
        # 过滤：只保留路径以当前 source_dir 开头的图片
        # 标准化路径进行比较
        source_dir_normalized = new_source_dir.rstrip("/")
        filtered_specified = [
            img for img in original_specified
            if img and img.strip() and img.startswith(source_dir_normalized + "/")
        ]
        data["product_images"]["specified_images"] = filtered_specified
    
    # 根据新目录更新模板名称（使用文件夹名作为后缀）
    folder_name = Path(new_source_dir).name
    original_name = data.get("name", "生成任务")
    # 避免重复添加后缀（如果原名称已经包含文件夹名）
    if not original_name.endswith(f"_{folder_name}"):
        data["name"] = f"{original_name}_{folder_name}"
    
    # 创建临时文件
    fd, temp_path = tempfile.mkstemp(suffix=".json", prefix="template_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    return Path(temp_path)


def main():
    """主入口"""
    parser = argparse.ArgumentParser(
        description="AI图片生成器 - 批量生成产品场景图",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 新运行（使用默认模板）
  python -m ai_image_generator
  
  # 指定模板运行
  python -m ai_image_generator -t templates/scene_generation_template.json
  
  # 验证配置
  python -m ai_image_generator --dry-run
  
  # 断点续传（直接传入之前的运行目录）
  python -m ai_image_generator outputs/海洋至尊_20260126_143000
  
  # 多产品图文件夹批量生成（在模板中配置 source_dir 为数组）
  # "source_dir": ["产品图/海洋至尊", "产品图/化妆品2", "产品图/化妆品3"]
        """,
    )
    
    # 位置参数：断点续传目录（可选）
    parser.add_argument(
        "resume_dir",
        nargs="?",
        default=None,
        help="断点续传：指定之前的运行目录路径",
    )
    
    parser.add_argument(
        "-t", "--template",
        default="templates/generation_template.json",
        help="模板配置文件路径 (默认: templates/generation_template.json)",
    )
    
    parser.add_argument(
        "-c", "--config",
        default="config.json",
        help="全局配置文件路径 (默认: config.json)",
    )
    
    parser.add_argument(
        "--api-key",
        help="API密钥（覆盖配置文件）",
    )
    
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="试运行模式，只验证配置不执行生成",
    )
    
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="自动确认，跳过所有确认提示",
    )
    
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别 (默认: INFO)",
    )
    
    args = parser.parse_args()
    
    # 配置日志
    setup_logging(level=args.log_level)
    logger = logging.getLogger(__name__)
    
    try:
        config_path = Path(args.config)
        
        # 判断是否为断点续传模式
        if args.resume_dir:
            # 断点续传模式
            resume_path = Path(args.resume_dir)
            
            # 检查目录是否存在
            if not resume_path.exists():
                logger.error(f"目录不存在: {resume_path}")
                return 1
            
            if not resume_path.is_dir():
                logger.error(f"路径不是目录: {resume_path}")
                return 1
            
            # 检查是否有 results.json
            results_file = resume_path / "results.json"
            if not results_file.exists():
                logger.error(f"该目录不是有效的运行目录（缺少 results.json）: {resume_path}")
                return 1
            
            # 加载状态获取模板配置路径
            state_manager = StateManager(state_dir=resume_path)
            state = state_manager.load_state()
            
            if not state:
                logger.error("状态文件损坏，无法恢复")
                return 1
            
            # 从状态中获取模板配置路径
            template_path = Path(state.template_config_path)
            if not template_path.exists():
                logger.error(f"模板配置文件不存在: {template_path}")
                return 1
            
            logger.info(f"🔄 断点续传模式: {resume_path}")
            logger.info(f"   使用模板: {template_path}")
            
            # 创建引擎
            engine = create_engine(
                config_path=config_path,
                template_path=template_path,
                api_key=args.api_key,
            )
            
            # 设置输出目录为恢复目录
            engine.output_manager.set_run_dir(resume_path)
            engine.state_manager.state_dir = resume_path
            engine.state_manager._state = state
            
            # 执行（会自动跳过已完成的组）
            result = engine.run(dry_run=args.dry_run, auto_confirm=args.yes)
            
            # 输出结果
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
            return 0
        
        # 新运行模式
        template_path = Path(args.template)
        
        # 检查是否有多个产品图文件夹
        source_dirs = get_product_source_dirs(template_path)
        
        if len(source_dirs) <= 1:
            # 单个文件夹，正常执行
            engine = create_engine(
                config_path=config_path,
                template_path=template_path,
                api_key=args.api_key,
            )
            result = engine.run(dry_run=args.dry_run, auto_confirm=args.yes)
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
            return 0
        
        # 多个产品图文件夹，循环执行
        logger.info(f"📂 检测到 {len(source_dirs)} 个产品图文件夹，将依次执行:")
        for i, d in enumerate(source_dirs, 1):
            logger.info(f"   {i}. {d}")
        print()
        
        # 验证 specified_images 都能匹配到 source_dir
        with open(template_path, "r", encoding="utf-8") as f:
            template_data = json.load(f)
        specified_images = template_data.get("product_images", {}).get("specified_images", [])
        if isinstance(specified_images, str):
            specified_images = [specified_images] if specified_images.strip() else []
        
        unmatched_images = validate_specified_images_coverage(source_dirs, specified_images)
        if unmatched_images:
            logger.error("❌ 以下指定的产品图路径不属于任何 source_dir 文件夹:")
            for img in unmatched_images:
                logger.error(f"   - {img}")
            logger.error(f"   可用的 source_dir: {source_dirs}")
            raise GeneratorError(f"指定的产品图路径无效: {', '.join(unmatched_images)}")
        
        all_results = []
        temp_files = []  # 记录临时文件，最后清理
        results_lock = threading.Lock()  # 结果列表锁
        
        def execute_source_dir(idx: int, source_dir: str) -> dict:
            """执行单个产品图文件夹的生成任务"""
            folder_name = Path(source_dir).name
            logger.info(f"\n{'='*60}")
            logger.info(f"📦 [{idx}/{len(source_dirs)}] 开始处理: {folder_name}")
            logger.info(f"{'='*60}\n")
            
            # 创建临时模板配置
            temp_template = update_template_source_dir(template_path, source_dir)
            with results_lock:
                temp_files.append(temp_template)
            
            # 创建引擎
            engine = create_engine(
                config_path=config_path,
                template_path=temp_template,
                api_key=args.api_key,
            )
            
            # 执行
            result = engine.run(dry_run=args.dry_run, auto_confirm=args.yes)
            
            logger.info(f"\n✅ [{idx}/{len(source_dirs)}] {folder_name} 完成")
            
            return {
                "source_dir": source_dir,
                "folder_name": folder_name,
                "result": result.to_dict(),
            }
        
        try:
            # 并发执行所有文件夹
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(source_dirs)) as executor:
                futures = {
                    executor.submit(execute_source_dir, idx, source_dir): source_dir
                    for idx, source_dir in enumerate(source_dirs, 1)
                }
                
                for future in concurrent.futures.as_completed(futures):
                    source_dir = futures[future]
                    try:
                        result = future.result()
                        with results_lock:
                            all_results.append(result)
                    except Exception as e:
                        folder_name = Path(source_dir).name
                        logger.error(f"❌ {folder_name} 执行失败: {e}")
                        with results_lock:
                            all_results.append({
                                "source_dir": source_dir,
                                "folder_name": folder_name,
                                "result": {"error": str(e)},
                            })
            
            # 输出汇总结果
            logger.info(f"\n{'='*60}")
            logger.info(f"🎉 全部完成！共处理 {len(source_dirs)} 个产品图文件夹")
            logger.info(f"{'='*60}\n")
            
            # 汇总统计
            total_images = sum(r["result"]["total_images"] for r in all_results)
            successful_images = sum(r["result"]["successful_images"] for r in all_results)
            failed_images = sum(r["result"]["failed_images"] for r in all_results)
            total_duration = sum(r["result"]["duration_seconds"] for r in all_results)
            
            summary = {
                "total_source_dirs": len(source_dirs),
                "total_images": total_images,
                "successful_images": successful_images,
                "failed_images": failed_images,
                "total_duration_seconds": total_duration,
                "results": all_results,
            }
            
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0
            
        finally:
            # 清理临时文件
            for temp_file in temp_files:
                try:
                    if temp_file.exists():
                        temp_file.unlink()
                except Exception:
                    pass
    
    except GeneratorError as e:
        logger.error(f"生成错误: {e}")
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 1
    
    except Exception as e:
        logger.exception(f"未知错误: {e}")
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
