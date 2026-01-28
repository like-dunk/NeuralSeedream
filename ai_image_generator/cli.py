"""
命令行接口
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional, Union

from .api_client import APIClient
from .config import ConfigManager
from .engine import GenerationEngine
from .exceptions import GeneratorError
from .image_selector import ImageSelector
from .moss_uploader import MOSSUploader
from .openrouter_image_client import OpenRouterImageClient
from .output_manager import OutputManager
from .state_manager import StateManager
from .template_engine import TemplateEngine
from .text_generator import TextGenerator


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
    
    # MOSS上传器
    moss_uploader = MOSSUploader(
        base_url=global_config.moss_base_url,
        access_key_id=global_config.moss_access_key_id,
        access_key_secret=global_config.moss_access_key_secret,
        bucket_name=global_config.moss_bucket_name,
        expire_seconds=global_config.moss_expire_seconds,
    )
    
    # 根据配置选择图片生成服务
    image_service = global_config.image_service
    api_client: Union[APIClient, OpenRouterImageClient]
    
    if image_service == "openrouter":
        logging.info(f"📡 使用 OpenRouter 图片生成服务, model={global_config.openrouter_image_model}")
        api_client = OpenRouterImageClient(
            api_key=global_config.openrouter_image_api_key,
            base_url=global_config.openrouter_image_base_url,
            model=global_config.openrouter_image_model,
            site_url=global_config.openrouter_image_site_url,
            site_name=global_config.openrouter_image_site_name,
        )
    else:
        logging.info(f"📡 使用 KieAI 图片生成服务, model={global_config.model}")
        api_client = APIClient(
            api_key=global_config.api_key,
            base_url=global_config.api_base_url,
            model=global_config.model,
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
        )
        
        # 加载 Few-shot 样本
        text_gen_cfg = template_config.text_generation
        if text_gen_cfg:
            title_dir = config_manager.get_resolved_path("title_prompts", text_gen_cfg.title_prompts_dir or "Prompt/文案生成/标题")
            content_dir = config_manager.get_resolved_path("content_prompts", text_gen_cfg.content_prompts_dir or "Prompt/文案生成/文案")
            text_generator.load_few_shot_examples(
                title_dir=title_dir,
                content_dir=content_dir,
                max_examples=text_gen_cfg.max_few_shot_examples,
            )
    
    # 创建引擎
    return GenerationEngine(
        config_manager=config_manager,
        template_engine=template_engine,
        image_selector=image_selector,
        moss_uploader=moss_uploader,
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
