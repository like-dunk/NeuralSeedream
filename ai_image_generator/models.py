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


@dataclass
class GlobalConfig:
    """全局配置"""
    api_key: str
    api_base_url: str = "https://api.kie.ai/api/v1"
    model: str = "nano-banana-pro"
    moss_base_url: str = ""
    moss_access_key_id: str = ""
    moss_access_key_secret: str = ""
    moss_bucket_name: str = ""
    poll_interval: float = 2.0
    max_wait: float = 1500.0
    moss_expire_seconds: int = 86400


@dataclass
class ImageSelectionConfig:
    """图片选择配置"""
    source_dir: str
    count_per_group: Union[int, List[int]] = 1  # 固定值或 [min, max]
    selection_mode: str = "random"  # random, sequential, specified
    must_include: Optional[str] = None
    specified_images: List[str] = field(default_factory=list)
    specified_coverage: int = 100  # 指定图片覆盖的组百分比，默认100%


@dataclass
class PromptConfig:
    """Prompt配置"""
    source_dir: Optional[str] = None
    selection_mode: str = "random"
    unique_per_group: bool = True
    specified_prompts: List[str] = field(default_factory=list)
    custom_template: Optional[str] = None


@dataclass
class OutputConfig:
    """输出配置"""
    base_dir: str = "./outputs"
    aspect_ratio: str = "4:5"
    resolution: str = "2K"
    format: str = "png"
    max_concurrent_groups: int = 3  # 最大并发组数


@dataclass
class TemplateConfig:
    """模板配置"""
    name: str
    description: str
    mode: str  # scene_generation 或 subject_transfer
    group_count: int
    images_per_group: Union[int, List[int]]  # 固定值或 [min, max]
    product_images: ImageSelectionConfig
    prompts: PromptConfig
    output: OutputConfig
    reference_images: Optional[ImageSelectionConfig] = None
    template_variables: Dict[str, Any] = field(default_factory=dict)
    paths: Dict[str, str] = field(default_factory=dict)


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
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化的字典"""
        return {
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
