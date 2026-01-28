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
from .models import ProductInfo, TextResult

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
        reference_base_dir: Optional[str] = None,
        proxy: Optional[str] = None,
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
            reference_json_path: 参考文案 JSON 路径（已废弃，使用 reference_base_dir）
            reference_base_dir: 参考文案目录，按类别组织
            proxy: 代理地址（如 http://user:pass@host:port）
        """
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.site_url = site_url
        self.site_name = site_name
        self.temperature = temperature
        self.max_retries = max_retries
        self.proxy = proxy

        # 模板和参考文案路径
        self.prompt_template_path = Path(prompt_template_path) if prompt_template_path else Path("prompts/text_template.j2")

        # 支持新旧两种方式
        if reference_base_dir:
            self.reference_base_dir = Path(reference_base_dir)
        else:
            # 向后兼容：如果指定了旧的 reference_json_path，使用其父目录
            if reference_json_path:
                self.reference_base_dir = Path(reference_json_path).parent
            else:
                self.reference_base_dir = Path("文案库")

        # 初始化 OpenAI 异步客户端
        self.client: Optional[AsyncOpenAI] = None
        if api_key:
            # 配置 httpx 客户端以支持代理
            import httpx
            http_client = None
            if proxy:
                http_client = httpx.AsyncClient(proxy=proxy)
            
            self.client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                http_client=http_client,
            )

        # 初始化 Jinja2 环境
        self._jinja_env: Optional[Environment] = None

        # Few-shot 样本（可选，由 CLI 预加载）
        self._few_shot_title_examples: List[str] = []
        self._few_shot_content_examples: List[str] = []
    
    def is_enabled(self) -> bool:
        """检查服务是否启用"""
        return bool(self.api_key) and self.client is not None

    def load_few_shot_examples(
        self,
        title_dir: Path,
        content_dir: Path,
        max_examples: int = 5,
    ) -> None:
        self._few_shot_title_examples = self._load_few_shot_dir(title_dir, max_examples)
        self._few_shot_content_examples = self._load_few_shot_dir(content_dir, max_examples)

    def _load_few_shot_dir(self, directory: Path, max_examples: int) -> List[str]:
        if not directory or not directory.exists() or not directory.is_dir():
            logger.warning(f"Few-shot 目录不存在或不可用: {directory}")
            return []

        files = sorted([p for p in directory.iterdir() if p.is_file()])
        examples: List[str] = []
        for p in files:
            if len(examples) >= max_examples:
                break
            try:
                examples.append(p.read_text(encoding="utf-8").strip())
            except Exception as e:
                logger.warning(f"读取 Few-shot 样本失败: {p}, {e}")
        return [e for e in examples if e]
    
    def _get_jinja_env(self) -> Environment:
        """获取或创建 Jinja2 环境"""
        if self._jinja_env is None:
            template_dir = self.prompt_template_path.parent
            self._jinja_env = Environment(
                loader=FileSystemLoader(str(template_dir)),
                extensions=["jinja2.ext.loopcontrols"],
                autoescape=False
            )
        return self._jinja_env
    
    def _load_reference_examples(self, category: str = "美妆") -> List[Dict[str, str]]:
        """
        从 JSON 文件加载参考文案

        Args:
            category: 产品类别，用于选择对应的参考文案文件

        Returns:
            参考文案列表
        """
        # 尝试加载分类文件
        category_file = self.reference_base_dir / f"{category}产品参考.json"

        # 如果分类文件不存在，尝试加载默认文件
        if not category_file.exists():
            logger.warning(f"分类参考文案文件不存在: {category_file}，尝试使用默认文件")
            category_file = self.reference_base_dir / "美妆产品参考.json"

        if not category_file.exists():
            logger.warning(f"参考文案文件不存在: {category_file}")
            return []

        try:
            with open(category_file, "r", encoding="utf-8") as f:
                try:
                    data = json.load(f)
                except json.JSONDecodeError:
                    f.seek(0)
                    raw = f.read()
                    # 修复常见问题：正文中包含未转义的双引号（例如 把"洗干净"写成 把"洗干净"）
                    # 这里将夹在中文字符之间的双引号替换为中文引号，避免破坏 JSON 结构。
                    raw_fixed = re.sub(
                        r'(?<=[\u4e00-\u9fff])"([^"\n\r]{1,50})"(?=[\u4e00-\u9fff])',
                        r'“\1”',
                        raw,
                    )
                    data = json.loads(raw_fixed)

            if isinstance(data, list):
                logger.info(f"加载了 {len(data)} 条参考文案（类别：{category}）")
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
                "category": product_info.get("category", "美妆"),
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
    ) -> TextResult:
        """
        生成标题和文案

        Args:
            product_info: 产品信息字典，包含 product_name, brand, category, style 等
            context: 额外上下文信息

        Returns:
            TextResult 对象，包含 title, content, success, error
        """
        if not self.client:
            return TextResult(
                title="",
                content="",
                success=False,
                error="文案生成器未初始化，请检查 API 配置"
            )

        # 获取产品类别
        category = product_info.get("category", "美妆")

        # 加载参考文案
        reference_examples = self._load_reference_examples(category)

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
                        text_result = TextResult(
                            title=str(result["title"]),
                            content=str(result["content"]),
                            success=True,
                            error=None
                        )

                        # 验证文案质量
                        is_valid, error_msg = text_result.validate()
                        if not is_valid:
                            logger.warning(f"文案质量验证失败: {error_msg}")
                            # 继续重试
                            if attempt < self.max_retries - 1:
                                await asyncio.sleep(2 * (attempt + 1))
                                continue

                        logger.info(f"文案生成成功: {text_result.title[:30]}...")
                        return text_result
                    else:
                        logger.warning(f"JSON 解析失败或缺少字段: {content[:200]}")

            except Exception as e:
                logger.error(f"文案生成错误: {e}")

            if attempt < self.max_retries - 1:
                await asyncio.sleep(2 * (attempt + 1))

        # 返回默认值
        logger.error("文案生成失败，返回默认值")
        return TextResult(
            title=f"{product_info.get('brand', '')} {product_info.get('product_name', '产品')} 推荐",
            content=f"这款 {product_info.get('product_name', '产品')} 真的很不错，推荐给大家！",
            success=False,
            error="达到最大重试次数，使用默认文案"
        )
    
    def generate_sync(
        self,
        product_info: Dict[str, Any],
        context: Optional[str] = None,
    ) -> TextResult:
        """同步版本的生成方法"""
        return asyncio.run(self.generate(product_info, context))
