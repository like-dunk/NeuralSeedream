"""
Jinja2模板引擎 - 负责模板加载和渲染
"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from jinja2 import Environment, FileSystemLoader, Template, TemplateError

from .exceptions import TemplateRenderError
from .models import TemplateContext

logger = logging.getLogger(__name__)


class TemplateEngine:
    """Jinja2模板引擎"""
    
    def __init__(self, template_dir: Optional[Path] = None):
        """
        初始化模板引擎
        
        Args:
            template_dir: 模板文件目录
        """
        self.template_dir = template_dir
        
        # 配置Jinja2环境
        if template_dir and template_dir.exists():
            self.env = Environment(
                loader=FileSystemLoader(str(template_dir)),
                autoescape=False,  # prompt不需要HTML转义
            )
        else:
            self.env = Environment(autoescape=False)
    
    def load_template(self, path: Path) -> str:
        """
        从文件加载模板
        
        Args:
            path: 模板文件路径
            
        Returns:
            模板内容字符串
        """
        if not path.exists():
            raise TemplateRenderError(f"模板文件不存在: {path}", template=str(path))
        
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            raise TemplateRenderError(f"读取模板文件失败: {e}", template=str(path))
    
    def render(self, template_str: str, context: TemplateContext) -> str:
        """
        渲染模板
        
        Args:
            template_str: 模板字符串
            context: 模板上下文变量
            
        Returns:
            渲染后的字符串
        """
        try:
            template = self.env.from_string(template_str)
            return template.render(**context.to_dict())
        except TemplateError as e:
            logger.warning(f"模板渲染失败: {e}, 返回原始模板")
            return template_str
        except Exception as e:
            logger.warning(f"模板渲染异常: {e}, 返回原始模板")
            return template_str
    
    def render_dict(self, template_str: str, context_dict: Dict[str, Any]) -> str:
        """
        使用字典渲染模板
        
        Args:
            template_str: 模板字符串
            context_dict: 上下文字典
            
        Returns:
            渲染后的字符串
        """
        try:
            template = self.env.from_string(template_str)
            return template.render(**context_dict)
        except TemplateError as e:
            logger.warning(f"模板渲染失败: {e}, 返回原始模板")
            return template_str
        except Exception as e:
            logger.warning(f"模板渲染异常: {e}, 返回原始模板")
            return template_str
    
    def build_context(
        self,
        group_index: int,
        image_index: int,
        product_count: int,
        reference_count: int,
        total_groups: int,
        mode: str,
        custom_vars: Optional[Dict[str, Any]] = None,
    ) -> TemplateContext:
        """
        构建模板上下文
        
        Args:
            group_index: 组索引（从0开始）
            image_index: 图片索引（从0开始）
            product_count: 产品图数量
            reference_count: 参考图数量
            total_groups: 总组数
            mode: 生成模式
            custom_vars: 自定义变量
            
        Returns:
            TemplateContext实例
        """
        return TemplateContext(
            group_index=group_index,
            group_num=group_index + 1,
            image_index=image_index,
            image_num=image_index + 1,
            product_count=product_count,
            reference_count=reference_count,
            total_groups=total_groups,
            mode=mode,
            custom_vars=custom_vars or {},
        )
