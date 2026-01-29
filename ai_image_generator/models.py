"""
数据模型定义
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


class GenerationMode(Enum):
    """生成模式"""
    SCENE_GENERATION = "scene_generation"
    SUBJECT_TRANSFER = "subject_transfer"


class SelectionMode(Enum):
    """选择模式"""
    RANDOM = "random"
    SEQUENTIAL = "sequential"
    SPECIFIED = "specified"


class ImageServiceProvider(Enum):
    """图片生成服务提供商"""
    KIEAI = "kieai"
    OPENROUTER = "openrouter"


@dataclass
class GlobalConfig:
    """全局配置"""
    # 图片生成服务选择
    image_service: str = "kieai"  # kieai 或 openrouter
    
    # KieAI 配置
    api_key: str = ""
    api_base_url: str = "https://api.kie.ai/api/v1"
    model: str = "nano-banana-pro"
    poll_interval: float = 2.0
    max_wait: float = 1500.0
    
    # MOSS 配置
    moss_base_url: str = ""
    moss_access_key_id: str = ""
    moss_access_key_secret: str = ""
    moss_bucket_name: str = ""
    moss_expire_seconds: int = 86400
    
    # OpenRouter 图片生成配置
    openrouter_image_api_key: str = ""
    openrouter_image_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_image_model: str = "google/gemini-3-pro-image-preview"
    openrouter_image_site_url: str = ""
    openrouter_image_site_name: str = ""
    openrouter_image_proxy: str = ""  # 代理地址
    
    # OpenRouter 文案生成配置（保持原有）
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = ""
    openrouter_site_url: str = ""
    openrouter_site_name: str = ""
    openrouter_proxy: str = ""  # 代理地址
    
    # 文案生成参考文案配置
    reference_min_samples: int = 3  # 参考文案最少抽取数量
    reference_max_samples: int = 5  # 参考文案最多抽取数量


@dataclass
class ImageSelectionConfig:
    """图片选择配置"""
    source_dir: str
    count_per_group: Union[int, List[int]] = 1  # 固定值或 [min, max]
    selection_mode: str = "random"  # random, sequential, specified
    must_include: Optional[str] = None
    specified_images: Union[str, List[str]] = field(default_factory=list)  # 产品图用数组，参考图用字符串
    specified_coverage: int = 100  # 指定图片覆盖的组百分比，默认100%


@dataclass
class PromptItem:
    """单个 Prompt 项"""
    id: str
    name: str
    description: str
    enabled: bool
    tags: List[str]
    template: str


@dataclass
class ScenePromptConfig:
    """
    场景生成 Prompt 配置

    特点：
    - 每组使用不同的 prompt（不重复随机）
    - 指定的 prompts 只占用对应数量的组，剩余组继续随机
    - prompt 用完后才会复用
    """
    source_dir: str = "prompts/scene_generation.json"
    specified_prompts: List[str] = field(default_factory=list)  # 指定的 prompt ID 列表
    custom_template: Optional[str] = None  # 自定义模板内容（优先级最高）


@dataclass
class TransferPromptConfig:
    """
    主体迁移 Prompt 配置

    特点：
    - 所有组共用同一个 prompt
    - 默认随机选择一个，也可以指定
    - 指定后所有组都使用该 prompt
    """
    source_dir: str = "prompts/subject_transfer.json"
    specified_prompt: Optional[str] = None  # 指定的单个 prompt ID
    custom_template: Optional[str] = None  # 自定义模板内容（优先级最高）


@dataclass
class OutputConfig:
    """输出配置"""
    base_dir: str = "./outputs"
    aspect_ratio: str = "4:5"
    resolution: str = "2K"
    format: str = "png"
    max_concurrent_groups: int = 10  # 最大并发组数
    generate_text: bool = True  # 是否生成文案


@dataclass
class TextGenerationConfig:
    """文案生成配置"""
    enabled: bool = True
    title_prompts_dir: Optional[str] = None  # 标题样本目录
    content_prompts_dir: Optional[str] = None  # 文案样本目录
    max_few_shot_examples: int = 5  # 最大 Few-shot 样本数
    tags: List[str] = field(default_factory=list)  # 用户自定义标签列表


class GenerationTarget(Enum):
    """生成目标"""
    IMAGE_ONLY = "image_only"  # 仅生成图片
    TEXT_ONLY = "text_only"    # 仅生成文案
    BOTH = "both"              # 同时生成图片和文案


class ImageModel(Enum):
    """图片生成模型"""
    NANO_BANANA_PRO = "nano-banana-pro"
    SEEDREAM_EDIT = "seedream/4.5-edit"


@dataclass
class TemplateConfig:
    """模板配置"""
    name: str
    description: str
    mode: str  # scene_generation 或 subject_transfer
    group_count: int
    images_per_group: Union[int, List[int]]  # 固定值或 [min, max]
    product_images: ImageSelectionConfig
    output: OutputConfig
    generation_target: str = "both"  # image_only, text_only, both
    image_model: str = "nano-banana-pro"  # nano-banana-pro 或 seedream/4.5-edit
    reference_images: Optional[ImageSelectionConfig] = None
    template_variables: Dict[str, Any] = field(default_factory=dict)
    paths: Dict[str, str] = field(default_factory=dict)
    text_generation: Optional[TextGenerationConfig] = None  # 文案生成配置
    scene_prompts: Optional[ScenePromptConfig] = None  # 场景生成模式
    transfer_prompts: Optional[TransferPromptConfig] = None  # 主体迁移模式


@dataclass
class TemplateContext:
    """模板渲染上下文"""
    group_index: int
    group_num: int  # group_index + 1
    image_index: int
    image_num: int  # image_index + 1
    product_count: int
    reference_count: int
    total_groups: int
    mode: str
    custom_vars: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典用于Jinja2渲染"""
        result = {
            "group_index": self.group_index,
            "group_num": self.group_num,
            "image_index": self.image_index,
            "image_num": self.image_num,
            "product_count": self.product_count,
            "reference_count": self.reference_count,
            "total_groups": self.total_groups,
            "mode": self.mode,
        }
        result.update(self.custom_vars)
        return result


@dataclass
class UploadResult:
    """上传结果"""
    path: Path
    url: str
    moss_id: str


@dataclass
class TaskResult:
    """API任务结果"""
    task_id: str
    status: str
    result_urls: List[str]
    error: Optional[str] = None


@dataclass
class ImageResult:
    """单张图片生成结果"""
    index: int
    output_path: Path
    task_id: str
    prompt: str
    input_images: List[str]
    success: bool
    error: Optional[str] = None


@dataclass
class ProductInfo:
    """产品信息"""
    product_name: str
    brand: str = ""
    category: str = "美妆"
    style: str = "种草分享"
    features: str = ""
    target_audience: str = "年轻女性"

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "product_name": self.product_name,
            "brand": self.brand,
            "category": self.category,
            "style": self.style,
            "features": self.features,
            "target_audience": self.target_audience,
        }


@dataclass
class TextResult:
    """文案生成结果"""
    title: str
    content: str
    success: bool
    error: Optional[str] = None

    def validate(self) -> tuple[bool, Optional[str]]:
        """
        验证文案质量

        Returns:
            (is_valid, error_message)
        """
        # 检查标题长度
        title_len = len(self.title)
        if title_len < 10:
            return False, f"标题过短（{title_len}字），建议15-30字"
        if title_len > 50:
            return False, f"标题过长（{title_len}字），建议15-30字"

        # 检查正文长度
        content_len = len(self.content)
        if content_len < 100:
            return False, f"正文过短（{content_len}字），建议200-500字"
        if content_len > 1000:
            return False, f"正文过长（{content_len}字），建议200-500字"

        # 检查是否包含产品信息
        if not self.title.strip() or not self.content.strip():
            return False, "标题或正文为空"

        return True, None


@dataclass
class GroupResult:
    """组生成结果"""
    group_index: int
    group_dir: Path
    product_images: List[Path]
    reference_images: List[Path]
    prompt_template: str
    prompt_rendered: str
    images: List[ImageResult]
    completed_at: Optional[datetime] = None
    text_result: Optional[TextResult] = None  # 文案生成结果
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化的字典"""
        result = {
            "group_index": self.group_index,
            "group_dir": str(self.group_dir),
            "product_images": [str(p) for p in self.product_images],
            "reference_images": [str(p) for p in self.reference_images],
            "prompt_template": self.prompt_template,
            "prompt_rendered": self.prompt_rendered,
            "images": [
                {
                    "index": img.index,
                    "output_path": str(img.output_path),
                    "task_id": img.task_id,
                    "prompt": img.prompt,
                    "input_images": img.input_images,
                    "success": img.success,
                    "error": img.error,
                }
                for img in self.images
            ],
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
        
        if self.text_result:
            result["text"] = {
                "title": self.text_result.title,
                "content": self.text_result.content,
                "success": self.text_result.success,
                "error": self.text_result.error,
            }
        
        return result


@dataclass
class RunState:
    """运行状态（用于断点续传）"""
    template_config_path: str
    run_dir: Path
    started_at: datetime
    completed_groups: Dict[int, Dict[str, Any]]  # group_index -> GroupResult.to_dict()
    current_group: Optional[int] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化的字典"""
        return {
            "template_config_path": self.template_config_path,
            "run_dir": str(self.run_dir),
            "started_at": self.started_at.isoformat(),
            "completed_groups": self.completed_groups,
            "current_group": self.current_group,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RunState":
        """从字典创建实例"""
        return cls(
            template_config_path=data["template_config_path"],
            run_dir=Path(data["run_dir"]),
            started_at=datetime.fromisoformat(data["started_at"]),
            completed_groups=data.get("completed_groups", {}),
            current_group=data.get("current_group"),
        )


@dataclass
class RunResult:
    """运行结果"""
    run_dir: Path
    total_groups: int
    completed_groups: int
    total_images: int
    successful_images: int
    failed_images: int
    duration_seconds: float
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化的字典"""
        return {
            "run_dir": str(self.run_dir),
            "total_groups": self.total_groups,
            "completed_groups": self.completed_groups,
            "total_images": self.total_images,
            "successful_images": self.successful_images,
            "failed_images": self.failed_images,
            "duration_seconds": self.duration_seconds,
        }


@dataclass
class GenerationLog:
    """生成日志"""
    template_name: str
    mode: str
    started_at: datetime
    completed_at: Optional[datetime]
    groups: List[Dict[str, Any]]  # List of GroupResult.to_dict()
    summary: Optional[Dict[str, Any]] = None  # RunResult.to_dict()
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化的字典"""
        return {
            "template_name": self.template_name,
            "mode": self.mode,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "groups": self.groups,
            "summary": self.summary,
        }
