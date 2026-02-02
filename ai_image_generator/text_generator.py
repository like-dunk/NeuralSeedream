"""
文案生成器 - 基于 OpenRouter/OpenAI API 生成标题和文案
"""

import asyncio
import json
import logging
import random
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
        reference_min_samples: int = 3,
        reference_max_samples: int = 5,
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
            reference_min_samples: 参考文案最少抽取数量（默认3条）
            reference_max_samples: 参考文案最多抽取数量（默认5条）
        """
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.site_url = site_url
        self.site_name = site_name
        self.temperature = temperature
        self.max_retries = max_retries
        self.proxy = proxy
        self.reference_min_samples = reference_min_samples
        self.reference_max_samples = reference_max_samples

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
        # 先创建直连客户端，如果连接失败再尝试代理
        self.client: Optional[AsyncOpenAI] = None
        self.client_with_proxy: Optional[AsyncOpenAI] = None
        
        if api_key:
            # 直连客户端（无代理）
            self.client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
            )
            
            # 代理客户端（备用）
            if proxy:
                import httpx
                http_client = httpx.AsyncClient(proxy=proxy)
                self.client_with_proxy = AsyncOpenAI(
                    api_key=api_key,
                    base_url=base_url,
                    http_client=http_client,
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
                extensions=["jinja2.ext.loopcontrols"],
                autoescape=False
            )
        return self._jinja_env
    
    def _load_reference_examples(self, category: str = "美妆") -> List[Dict[str, str]]:
        """
        从 JSON 文件加载参考文案，随机抽取指定数量

        Args:
            category: 产品类别，用于选择对应的参考文案文件

        Returns:
            随机抽取的参考文案列表
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
                    # 修复常见问题：正文中包含未转义的双引号
                    raw_fixed = re.sub(
                        r'(?<=[\u4e00-\u9fff])"([^"\n\r]{1,50})"(?=[\u4e00-\u9fff])',
                        r'"\1"',
                        raw,
                    )
                    data = json.loads(raw_fixed)

            if isinstance(data, list):
                total_count = len(data)
                # 随机决定抽取数量（使用配置的min/max值）
                sample_count = random.randint(
                    self.reference_min_samples, 
                    min(self.reference_max_samples, total_count)
                )
                # 随机抽取
                sampled_data = random.sample(data, sample_count) if total_count >= sample_count else data
                logger.info(f"从 {total_count} 条参考文案中随机抽取了 {len(sampled_data)} 条（类别：{category}）")
                return sampled_data
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
    
    async def _generate_with_client(
        self,
        client: AsyncOpenAI,
        full_prompt: str,
        product_info: Dict[str, Any],
        use_proxy: bool = False,
        max_retries: Optional[int] = None,
    ) -> TextResult:
        """使用指定客户端生成文案（带重试）"""
        mode_str = "代理" if use_proxy else "直连"
        retries = max_retries if max_retries is not None else self.max_retries
        
        for attempt in range(retries):
            try:
                logger.debug(f"文案生成尝试 {attempt + 1}/{self.max_retries} ({mode_str})")

                extra_headers = {}
                if self.site_url:
                    extra_headers["HTTP-Referer"] = self.site_url
                if self.site_name:
                    extra_headers["X-Title"] = self.site_name

                completion = await client.chat.completions.create(
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
                            if attempt < retries - 1:
                                await asyncio.sleep(2 * (attempt + 1))
                                continue

                        logger.info(f"文案生成成功: {text_result.title[:30]}...")
                        return text_result
                    else:
                        logger.warning(f"JSON 解析失败或缺少字段: {content[:200]}")

            except Exception as e:
                import traceback
                error_type = type(e).__name__
                logger.error(f"文案生成错误: {error_type}: {e} ({mode_str})")
                logger.debug(f"详细堆栈: {traceback.format_exc()}")

            if attempt < retries - 1:
                await asyncio.sleep(2 * (attempt + 1))

        # 返回 None 表示该客户端失败
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

        # 加载参考文案（随机抽取3-5条）
        reference_examples = self._load_reference_examples(category)

        # 渲染提示词模板
        full_prompt = self._render_prompt_template(product_info, reference_examples, context)

        # 先尝试直连
        result = await self._generate_with_client(self.client, full_prompt, product_info, use_proxy=False)
        if result is not None:
            return result
        
        # 直连失败，尝试代理（代理重试次数较少）
        if self.client_with_proxy:
            logger.info("直连失败，切换到代理模式重试...")
            result = await self._generate_with_client(
                self.client_with_proxy, full_prompt, product_info, 
                use_proxy=True, max_retries=2
            )
            if result is not None:
                return result

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
