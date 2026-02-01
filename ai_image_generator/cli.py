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
import warnings
from pathlib import Path
from typing import Optional, Union

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
    
    # 根据 storage_service 和 image_service 选择上传器
    storage_service = global_config.storage_service
    image_service = global_config.image_service
    
    # KieAI 必须使用 MOSS（KieAI API 需要直接访问 URL）
    # OpenRouter 可以选择 MOSS 或 GCS
    if image_service == "kieai":
        # KieAI 强制使用 MOSS
        if storage_service == "gcs":
            logging.warning("⚠️ KieAI 服务不支持 GCS，自动切换到 MOSS")
        uploader = MOSSUploader(
            base_url=global_config.moss_base_url,
            access_key_id=global_config.moss_access_key_id,
            access_key_secret=global_config.moss_access_key_secret,
            bucket_name=global_config.moss_bucket_name,
            expire_seconds=global_config.moss_expire_seconds,
        )
    elif storage_service == "gcs" and global_config.gcs_bucket_name:
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
        # OpenRouter + MOSS（默认）
        uploader = MOSSUploader(
            base_url=global_config.moss_base_url,
            access_key_id=global_config.moss_access_key_id,
            access_key_secret=global_config.moss_access_key_secret,
            bucket_name=global_config.moss_bucket_name,
            expire_seconds=global_config.moss_expire_seconds,
        )
    
    # 根据配置选择图片生成服务
    image_model = template_config.image_model
    api_client: Union[APIClient, OpenRouterImageClient, SeedreamClient]
    
    if image_service == "openrouter":
        logging.info(f"📡 使用 OpenRouter 图片生成服务, model={global_config.openrouter_image_model}")
        if global_config.openrouter_image_proxy:
            logging.info(f"📡 使用代理: {global_config.openrouter_image_proxy.split('@')[-1]}")
        api_client = OpenRouterImageClient(
            api_key=global_config.openrouter_image_api_key,
            base_url=global_config.openrouter_image_base_url,
            model=global_config.openrouter_image_model,
            site_url=global_config.openrouter_image_site_url,
            site_name=global_config.openrouter_image_site_name,
            proxy=global_config.openrouter_image_proxy or None,
        )
    elif image_model == "seedream/4.5-edit":
        # 使用 Seedream 4.5 Edit 模型
        logging.info(f"📡 使用 KieAI Seedream 4.5 Edit 图片生成服务")
        api_client = SeedreamClient(
            api_key=global_config.api_key,
            base_url=global_config.api_base_url,
            model="seedream/4.5-edit",
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


def main():
    """主入口"""
    parser = argparse.ArgumentParser(
        description="AI图片生成器 - 批量生成产品场景图",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 场景生成
  python -m ai_image_generator -t templates/scene_generation_template.json
  
  # 主体迁移
  python -m ai_image_generator -t templates/subject_transfer_template.json
  
  # 验证配置
  python -m ai_image_generator -t templates/xxx.json --dry-run
  
  # 断点续传
  python -m ai_image_generator -t templates/xxx.json --resume outputs/xxx_20260126_143000
        """,
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
        "--resume",
        help="断点续传，指定之前的运行目录",
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
        template_path = Path(args.template)
        
        # 创建引擎
        engine = create_engine(
            config_path=config_path,
            template_path=template_path,
            api_key=args.api_key,
        )
        
        # 执行
        if args.resume:
            result = engine.resume(Path(args.resume), auto_confirm=args.yes)
        else:
            result = engine.run(dry_run=args.dry_run, auto_confirm=args.yes)
        
        # 输出结果
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        
        return 0
    
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
