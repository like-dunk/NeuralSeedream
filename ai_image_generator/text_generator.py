"""
文案生成器 - 基于 OpenRouter/OpenAI API 生成标题和文案
"""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from jinja2 import Environment, FileSystemLoader, TemplateNotFound
from openai import AsyncOpenAI

from .exceptions import GeneratorError

logger = logging.getLogger(__name__)


class TextGenerator:
    """文案生成器 - 基于 OpenRouter/OpenAI API"""
    
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        model: str = "google/gemini-3-flash-preview",
        site_url: Optional[str] = None,
        site_name: Optional[str] = None,
        temperature: float = 0.8,
        max_retries: int = 3,
        prompt_template_path: Optional[str] = None,
        reference_json_path: Optional[str] = None,
    ):
        """
        初始化文案生成器
        
        Args:
            api_key: OpenRouter API 密钥
            base_url: API 基础 URL
            model: 模型名称
            site_url: 站点 URL（用于 OpenRouter）
            site_name: 站点名称（用于 OpenRouter）
            temperature: 生成温度
            max_retries: 最大重试次数
            prompt_template_path: Jinja2 提示词模板路径
            reference_json_path: 参考文案 JSON 路径
        """
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.site_url = site_url
        self.site_name = site_name
        self.temperature = temperature
        self.max_retries = max_retries
        
        # 模板和参考文案路径
        self.prompt_template_path = Path(prompt_template_path) if prompt_template_path else Path("Prompt/文案生成/tittle_text.j2")
        self.reference_json_path = Path(reference_json_path) if reference_json_path else Path("文案库/美妆产品参考.json")
        
        # 初始化 OpenAI 异步客户端
        self.client: Optional[AsyncOpenAI] = None
        if api_key:
            self.client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url
            )
        
        # 初始化 Jinja2 环境
        self._jinja_env: Optional[Environment] = None
    
    def is_enabled(self) -> bool:
        """检查服务是否启用"""
        return bool(self.api_key) and self.client is not None
    
    def _get_jinja_env(self) -> Environment:
        """获取或创建 Jinja2 环境"""
        if self._jinja_env is None:
            template_dir = self.prompt_template_path.parent
            self._jinja_env = Environment(
                loader=FileSystemLoader(str(template_dir)),
                autoescape=False
            )
        return self._jinja_env
    
    def _load_reference_examples(self) -> List[Dict[str, str]]:
        """从 JSON 文件加载参考文案"""
        if not self.reference_json_path.exists():
            logger.warning(f"参考文案文件不存在: {self.reference_json_path}")
            return []
        
        try:
            with open(self.reference_json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            if isinstance(data, list):
                logger.info(f"加载了 {len(data)} 条参考文案")
                return data
            else:
                logger.warning("参考文案 JSON 格式不正确，应为数组")
                return []
        except Exception as e:
            logger.error(f"加载参考文案失败: {e}")
            return []
    
    def _render_prompt_template(
        self,
        product_info: Dict[str, Any],
        reference_examples: List[Dict[str, str]],
        context: Optional[str] = None
    ) -> str:
        """使用 Jinja2 渲染提示词模板"""
        try:
            env = self._get_jinja_env()
            template = env.get_template(self.prompt_template_path.name)
            
            # 构建模板变量
            template_vars = {
                "product_name": product_info.get("product_name", "产品"),
                "brand": product_info.get("brand", ""),
                "style": product_info.get("style", "种草分享"),
                "features": product_info.get("features", ""),
                "target_audience": product_info.get("target_audience", "年轻女性"),
                "reference_examples": reference_examples,
                "context": context,
            }
            
            return template.render(**template_vars)
        except TemplateNotFound:
            logger.error(f"模板文件不存在: {self.prompt_template_path}")
            raise GeneratorError(f"提示词模板不存在: {self.prompt_template_path}")
        except Exception as e:
            logger.error(f"渲染模板失败: {e}")
            raise GeneratorError(f"渲染提示词模板失败: {e}")
    
    def _extract_json(self, text: str) -> Optional[Dict]:
        """从文本中提取 JSON"""
        # 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        
        # 尝试提取代码块中的 JSON
        json_pattern = r'```(?:json)?\s*(\{.*?\})\s*```'
        match = re.search(json_pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        
        # 尝试提取大括号内容
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end+1])
            except json.JSONDecodeError:
                pass
        
        return None
    
    async def generate(
        self,
        product_info: Dict[str, Any],
        context: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        生成标题和文案
        
        Args:
            product_info: 产品信息字典，包含 product_name, brand, style 等
            context: 额外上下文信息
            
        Returns:
            包含 title 和 content 的字典
        """
        if not self.client:
            raise GeneratorError("文案生成器未初始化，请检查 API 配置")
        
        # 加载参考文案
        reference_examples = self._load_reference_examples()
        
        # 渲染提示词模板
        full_prompt = self._render_prompt_template(product_info, reference_examples, context)
        
        for attempt in range(self.max_retries):
            try:
                logger.debug(f"文案生成尝试 {attempt + 1}/{self.max_retries}")
                
                extra_headers = {}
                if self.site_url:
                    extra_headers["HTTP-Referer"] = self.site_url
                if self.site_name:
                    extra_headers["X-Title"] = self.site_name
                
                completion = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "user", "content": full_prompt}
                    ],
                    extra_headers=extra_headers if extra_headers else None,
                    temperature=self.temperature,
                )
                
                if completion.choices and completion.choices[0].message.content:
                    content = completion.choices[0].message.content
                    result = self._extract_json(content)
                    
                    if result and "title" in result and "content" in result:
                        logger.info(f"文案生成成功: {result['title'][:30]}...")
                        return {
                            "title": str(result["title"]),
                            "content": str(result["content"])
                        }
                    else:
                        logger.warning(f"JSON 解析失败或缺少字段: {content[:200]}")
                
            except Exception as e:
                logger.error(f"文案生成错误: {e}")
            
            if attempt < self.max_retries - 1:
                await asyncio.sleep(2 * (attempt + 1))
        
        # 返回默认值
        logger.error("文案生成失败，返回默认值")
        return {
            "title": f"{product_info.get('brand', '')} {product_info.get('product_name', '产品')} 推荐",
            "content": f"这款 {product_info.get('product_name', '产品')} 真的很不错，推荐给大家！"
        }
    
    def generate_sync(
        self,
        product_info: Dict[str, Any],
        context: Optional[str] = None,
    ) -> Dict[str, str]:
        """同步版本的生成方法"""
        return asyncio.run(self.generate(product_info, context))
