"""
AI Image Generator - 批量图片生成系统

支持两种生成模式：
- 场景生成 (Scene Generation): 产品图 + 场景prompt
- 主体迁移 (Subject Transfer): 产品图 + 参考背景图 + 迁移prompt
"""

__version__ = "1.0.0"

from .models import (
    GenerationMode,
    SelectionMode,
    ImageServiceProvider,
    GlobalConfig,
    TemplateConfig,
    ImageSelectionConfig,
    ScenePromptConfig,
    TransferPromptConfig,
    OutputConfig,
    TextGenerationConfig,
    TemplateContext,
    UploadResult,
    TaskResult,
    ImageResult,
    TextResult,
    GroupResult,
    RunState,
    RunResult,
    GenerationLog,
)
from .exceptions import (
    GeneratorError,
    ConfigurationError,
    TemplateRenderError,
    PathNotFoundError,
    APIError,
    MOSSError,
)
from .config import ConfigManager
from .template_engine import TemplateEngine
from .image_selector import ImageSelector
from .moss_uploader import MOSSUploader
from .api_client import APIClient
from .openrouter_image_client import OpenRouterImageClient
from .output_manager import OutputManager
from .state_manager import StateManager
from .text_generator import TextGenerator
from .engine import GenerationEngine

__all__ = [
    # Enums
    "GenerationMode",
    "SelectionMode",
    "ImageServiceProvider",
    # Data Models
    "GlobalConfig",
    "TemplateConfig",
    "ImageSelectionConfig",
    "ScenePromptConfig",
    "TransferPromptConfig",
    "OutputConfig",
    "TextGenerationConfig",
    "TemplateContext",
    "UploadResult",
    "TaskResult",
    "ImageResult",
    "TextResult",
    "GroupResult",
    "RunState",
    "RunResult",
    "GenerationLog",
    # Exceptions
    "GeneratorError",
    "ConfigurationError",
    "TemplateRenderError",
    "PathNotFoundError",
    "APIError",
    "MOSSError",
    # Components
    "ConfigManager",
    "TemplateEngine",
    "ImageSelector",
    "MOSSUploader",
    "APIClient",
    "OpenRouterImageClient",
    "OutputManager",
    "StateManager",
    "TextGenerator",
    "GenerationEngine",
]
