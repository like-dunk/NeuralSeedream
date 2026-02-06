"""
配置管理器 - 负责加载和验证配置
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .exceptions import ConfigurationError, PathNotFoundError
from .models import (
    GlobalConfig,
    ImageSelectionConfig,
    OpeningStyle,
    OutputConfig,
    ScenePromptConfig,
    TransferPromptConfig,
    TemplateConfig,
    TextGenerationConfig,
)


class ConfigManager:
    """配置管理器"""
    
    def __init__(
        self,
        config_path: Optional[Path] = None,
        template_path: Optional[Path] = None,
        project_root: Optional[Path] = None,
    ):
        """
        初始化配置管理器
        
        Args:
            config_path: 全局配置文件路径 (config.json)
            template_path: 模板配置文件路径
            project_root: 项目根目录，用于解析相对路径
        """
        self.project_root = project_root or Path.cwd()
        self.config_path = config_path
        self.template_path = template_path
        self._global_config: Optional[GlobalConfig] = None
        self._template_config: Optional[TemplateConfig] = None
    
    def _load_json(self, path: Path) -> Dict[str, Any]:
        """加载JSON文件"""
        if not path.exists():
            raise PathNotFoundError(str(path), f"配置文件不存在: {path}")
        
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ConfigurationError(f"配置文件根对象必须是字典: {path}")
            return data
        except json.JSONDecodeError as e:
            raise ConfigurationError(f"JSON解析错误: {path}, {e}")
    
    def _resolve_path(self, path_str: str) -> Path:
        """解析路径，相对路径相对于项目根目录"""
        p = Path(path_str)
        if p.is_absolute():
            return p
        return self.project_root / p
    
    def load_global_config(self) -> GlobalConfig:
        """加载全局配置（API密钥、MOSS配置等）"""
        if self._global_config:
            return self._global_config
        
        if not self.config_path:
            # 尝试默认路径
            default_path = self.project_root / "config.json"
            if default_path.exists():
                self.config_path = default_path
            else:
                raise ConfigurationError("未指定配置文件路径", field="config_path")
        
        data = self._load_json(self.config_path)
        
        # 从环境变量或配置文件获取值
        kieai_cfg = data.get("kieai", {})
        moss_cfg = data.get("moss", {})
        gcs_cfg = data.get("gcs", {})
        openrouter_cfg = data.get("openrouter", {})
        openrouter_image_cfg = data.get("openrouter_image", {})
        text_gen_cfg = data.get("text_generator", {})
        
        # 存储服务选择（默认 moss）
        storage_service = data.get("storage_service", "moss")
        
        # KieAI 配置
        kieai_api_key = os.getenv("KIEAI_API_KEY") or kieai_cfg.get("api_key", "")
        
        # OpenRouter 图片生成配置（可以复用 openrouter 配置或使用独立配置）
        openrouter_image_api_key = (
            os.getenv("OPENROUTER_IMAGE_API_KEY") or 
            openrouter_image_cfg.get("api_key") or 
            os.getenv("OPENROUTER_API_KEY") or 
            openrouter_cfg.get("api_key", "")
        )
        
        self._global_config = GlobalConfig(
            # 服务选择
            storage_service=storage_service,
            # KieAI 配置
            api_key=kieai_api_key,
            api_base_url=kieai_cfg.get("base_url", "https://api.kie.ai/api/v1"),
            model=kieai_cfg.get("model", "nano-banana-pro"),
            poll_interval=float(kieai_cfg.get("poll_interval", 2.0)),
            max_wait=float(kieai_cfg.get("max_wait_seconds", 1500.0)),
            # KieAI Midjourney 配置
            midjourney_version=kieai_cfg.get("midjourney_version", "7"),
            midjourney_speed=kieai_cfg.get("midjourney_speed", "fast"),
            # MOSS 配置
            moss_base_url=os.getenv("MOSS_BASE_URL") or moss_cfg.get("base_url", ""),
            moss_access_key_id=os.getenv("MOSS_ACCESS_KEY_ID") or moss_cfg.get("access_key_id", ""),
            moss_access_key_secret=os.getenv("MOSS_ACCESS_KEY_SECRET") or moss_cfg.get("access_key_secret", ""),
            moss_bucket_name=os.getenv("MOSS_BUCKET_NAME") or moss_cfg.get("bucket_name", ""),
            moss_expire_seconds=int(moss_cfg.get("expire_seconds", 86400)),
            # GCS 配置
            gcs_bucket_name=os.getenv("GCS_BUCKET_NAME") or gcs_cfg.get("bucket_name", ""),
            gcs_folder_path=gcs_cfg.get("folder_path", "ImageUpload"),
            gcs_credentials_path=os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or gcs_cfg.get("credentials_path", ""),
            gcs_project_id=os.getenv("GOOGLE_CLOUD_PROJECT") or gcs_cfg.get("project_id", ""),
            # OpenRouter 图片生成配置
            openrouter_image_api_key=openrouter_image_api_key,
            openrouter_image_base_url=(
                openrouter_image_cfg.get("base_url") or 
                openrouter_cfg.get("base_url", "https://openrouter.ai/api/v1")
            ),
            openrouter_image_site_url=(
                openrouter_image_cfg.get("site_url") or 
                os.getenv("OPENROUTER_SITE_URL") or 
                openrouter_cfg.get("site_url", "")
            ),
            openrouter_image_site_name=(
                openrouter_image_cfg.get("site_name") or 
                os.getenv("OPENROUTER_SITE_NAME") or 
                openrouter_cfg.get("site_name", "")
            ),
            openrouter_image_proxy=(
                openrouter_image_cfg.get("proxy") or 
                os.getenv("OPENROUTER_PROXY") or 
                openrouter_cfg.get("proxy", "")
            ),
            # OpenRouter 文案生成配置（保持原有）
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY") or openrouter_cfg.get("api_key", ""),
            openrouter_base_url=os.getenv("OPENROUTER_BASE_URL") or openrouter_cfg.get("base_url", "https://openrouter.ai/api/v1"),
            openrouter_model=openrouter_cfg.get("model", "google/gemini-3-flash-preview"),
            openrouter_site_url=os.getenv("OPENROUTER_SITE_URL") or openrouter_cfg.get("site_url", ""),
            openrouter_site_name=os.getenv("OPENROUTER_SITE_NAME") or openrouter_cfg.get("site_name", ""),
            openrouter_proxy=os.getenv("OPENROUTER_PROXY") or openrouter_cfg.get("proxy", ""),
            # 文案生成参考文案配置
            reference_min_samples=int(text_gen_cfg.get("reference_min_samples", 3)),
            reference_max_samples=int(text_gen_cfg.get("reference_max_samples", 5)),
        )
        
        return self._global_config

    def load_template_config(self) -> TemplateConfig:
        """加载模板配置"""
        if self._template_config:
            return self._template_config
        
        if not self.template_path:
            raise ConfigurationError("未指定模板配置文件路径", field="template_path")
        
        data = self._load_json(self.template_path)
        
        # 验证必需字段
        required_fields = ["name", "mode", "group_count", "product_images"]
        for field in required_fields:
            if field not in data:
                raise ConfigurationError(f"模板配置缺少必需字段: {field}", field=field)
        
        # 解析产品图配置
        prod_cfg = data["product_images"]
        product_images = ImageSelectionConfig(
            source_dir=prod_cfg.get("source_dir", ""),
            count_per_group=prod_cfg.get("count_per_group", 4),
            selection_mode=prod_cfg.get("selection_mode", "random"),
            must_include=prod_cfg.get("must_include"),
            specified_images=prod_cfg.get("specified_images", []),
            specified_coverage=prod_cfg.get("specified_coverage", 100),
        )
        
        # 解析参考图配置（可选）
        reference_images = None
        if "reference_images" in data and data["reference_images"]:
            ref_cfg = data["reference_images"]
            reference_images = ImageSelectionConfig(
                source_dir=ref_cfg.get("source_dir", ""),
                count_per_group=ref_cfg.get("count_per_group", 1),
                selection_mode=ref_cfg.get("selection_mode", "random"),
                must_include=ref_cfg.get("must_include"),
                specified_images=ref_cfg.get("specified_images", []),
                specified_coverage=ref_cfg.get("specified_coverage", 100),
            )
        
        # 解析Prompt配置（新版）
        mode = data["mode"]
        scene_prompts = None
        transfer_prompts = None
        
        if "scene_prompts" in data:
            sp_cfg = data["scene_prompts"]
            scene_prompts = ScenePromptConfig(
                source_dir=sp_cfg.get("source_dir", "prompts/scene_generation.json"),
                specified_prompts=sp_cfg.get("specified_prompts", []),
                custom_template=sp_cfg.get("custom_template"),
            )
        
        if "transfer_prompts" in data:
            tp_cfg = data["transfer_prompts"]
            transfer_prompts = TransferPromptConfig(
                source_dir=tp_cfg.get("source_dir", "prompts/subject_transfer.json"),
                specified_prompt=tp_cfg.get("specified_prompt"),
                custom_template=tp_cfg.get("custom_template"),
            )
        
        # 验证：根据模式检查必需的 prompt 配置
        if mode == "scene_generation" and not scene_prompts:
            raise ConfigurationError("场景生成模式需要配置 scene_prompts", field="scene_prompts")
        if mode == "subject_transfer" and not transfer_prompts:
            raise ConfigurationError("主体迁移模式需要配置 transfer_prompts", field="transfer_prompts")
        
        # 解析输出配置
        output_cfg = data.get("output", {})
        output = OutputConfig(
            base_dir=output_cfg.get("base_dir", "./outputs"),
            aspect_ratio=output_cfg.get("aspect_ratio", "4:5"),
            resolution=output_cfg.get("resolution", "2K"),
            format=output_cfg.get("format", "png"),
            max_concurrent_groups=output_cfg.get("max_concurrent_groups", 10),
            save_inputs=output_cfg.get("save_inputs", False),
        )
        
        # 解析文案生成配置
        text_gen_cfg = data.get("text_generation", {})
        
        # 解析开头风格列表
        opening_styles_data = text_gen_cfg.get("opening_styles", [])
        opening_styles = [
            OpeningStyle(
                name=style.get("name", ""),
                description=style.get("description", ""),
                example=style.get("example", "")
            )
            for style in opening_styles_data
        ]
        
        # 解析产品信息（优先从 text_generation.product_info 读取，向后兼容 template_variables）
        product_info = text_gen_cfg.get("product_info", data.get("template_variables", {}))
        
        # 解析参考文案抽取数量
        # 支持两种格式：[3, 5] 表示随机范围，4 表示固定数量
        reference_samples_raw = text_gen_cfg.get("reference_samples", [3, 5])
        if isinstance(reference_samples_raw, int):
            # 固定数量：转换为 [n, n]
            reference_samples = [reference_samples_raw, reference_samples_raw]
        elif isinstance(reference_samples_raw, list) and len(reference_samples_raw) == 2:
            reference_samples = reference_samples_raw
        else:
            reference_samples = [3, 5]
        
        text_generation = TextGenerationConfig(
            enabled=text_gen_cfg.get("enabled", True),
            tags=text_gen_cfg.get("tags", []),
            opening_styles=opening_styles,
            product_info=product_info,
            reference_samples=reference_samples,
        )
        
        self._template_config = TemplateConfig(
            name=data["name"],
            description=data.get("description", ""),
            mode=data["mode"],
            generation_target=data.get("generation_target", "both"),
            image_model=data.get("image_model", "nano-banana-pro"),
            group_count=int(data["group_count"]),
            images_per_group=data.get("images_per_group", 1),
            product_images=product_images,
            reference_images=reference_images,
            output=output,
            template_variables=data.get("template_variables", {}),
            paths=data.get("paths", {}),
            text_generation=text_generation,
            scene_prompts=scene_prompts,
            transfer_prompts=transfer_prompts,
        )
        
        return self._template_config
    
    def validate_config(self) -> List[str]:
        """验证配置完整性，返回错误列表"""
        errors = []
        
        try:
            global_cfg = self.load_global_config()
            if not global_cfg.api_key:
                errors.append("缺少API密钥")
        except Exception as e:
            errors.append(f"全局配置错误: {e}")
        
        try:
            template_cfg = self.load_template_config()
            
            # 验证模式
            if template_cfg.mode not in ["scene_generation", "subject_transfer"]:
                errors.append(f"无效的生成模式: {template_cfg.mode}")
            
            # 验证生成目标
            if template_cfg.generation_target not in ["text", "image", "both"]:
                errors.append(f"无效的生成目标: {template_cfg.generation_target}")
            
            # 验证组数
            if template_cfg.group_count < 1:
                errors.append("组数必须大于0")
            
            # 验证产品图目录
            prod_dir = self.get_resolved_path("product_images", template_cfg.product_images.source_dir)
            if not prod_dir.exists():
                errors.append(f"产品图目录不存在: {prod_dir}")
            
            # 主体迁移模式需要参考图
            if template_cfg.mode == "subject_transfer":
                if not template_cfg.reference_images:
                    errors.append("主体迁移模式需要配置参考图")
                else:
                    ref_dir = self.get_resolved_path("reference_images", template_cfg.reference_images.source_dir)
                    if not ref_dir.exists():
                        errors.append(f"参考图目录不存在: {ref_dir}")
            
            # 验证Prompt目录
            if template_cfg.mode == "scene_generation" and template_cfg.scene_prompts:
                prompt_dir = self.get_resolved_path("scene_prompts", template_cfg.scene_prompts.source_dir)
                if not prompt_dir.exists():
                    errors.append(f"场景生成Prompt目录不存在: {prompt_dir}")
            
            if template_cfg.mode == "subject_transfer" and template_cfg.transfer_prompts:
                prompt_dir = self.get_resolved_path("transfer_prompts", template_cfg.transfer_prompts.source_dir)
                if not prompt_dir.exists():
                    errors.append(f"主体迁移Prompt目录不存在: {prompt_dir}")
        
        except Exception as e:
            errors.append(f"模板配置错误: {e}")
        
        return errors
    
    def get_resolved_path(self, path_type: str, path_str: str) -> Path:
        """
        获取解析后的绝对路径
        
        Args:
            path_type: 路径类型（用于查找paths覆盖）
            path_str: 原始路径字符串
        """
        # 检查是否有路径覆盖
        if self._template_config and path_type in self._template_config.paths:
            path_str = self._template_config.paths[path_type]
        
        return self._resolve_path(path_str)
    
    def get_all_resolved_paths(self) -> Dict[str, Path]:
        """获取所有解析后的路径"""
        template_cfg = self.load_template_config()
        
        paths = {
            "output_base": self._resolve_path(template_cfg.output.base_dir),
            "product_images": self.get_resolved_path("product_images", template_cfg.product_images.source_dir),
        }
        
        if template_cfg.reference_images:
            paths["reference_images"] = self.get_resolved_path(
                "reference_images", template_cfg.reference_images.source_dir
            )
        
        # 根据模式获取 prompt 目录
        if template_cfg.mode == "scene_generation" and template_cfg.scene_prompts:
            paths["prompts"] = self.get_resolved_path("scene_prompts", template_cfg.scene_prompts.source_dir)
        elif template_cfg.mode == "subject_transfer" and template_cfg.transfer_prompts:
            paths["prompts"] = self.get_resolved_path("transfer_prompts", template_cfg.transfer_prompts.source_dir)
        
        return paths
