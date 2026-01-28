"""
OpenRouter 图片生成客户端 - 使用 OpenAI 兼容接口

参考文档: https://openrouter.ai/google/gemini-3-pro-image-preview
"""

import base64
import logging
import time
from pathlib import Path
from typing import List, Optional

from openai import OpenAI

from .exceptions import APIError
from .models import TaskResult

logger = logging.getLogger(__name__)


class OpenRouterImageClient:
    """OpenRouter 图片生成客户端"""
    
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        model: str = "google/gemini-2.5-flash-preview-05-20",
        site_url: Optional[str] = None,
        site_name: Optional[str] = None,
        timeout: float = 300.0,
    ):
        """
        初始化 OpenRouter 客户端
        
        Args:
            api_key: OpenRouter API 密钥
            base_url: API 基础 URL
            model: 模型名称（支持图片生成的模型）
            site_url: 站点 URL（用于 OpenRouter 统计）
            site_name: 站点名称
            timeout: 请求超时时间（秒）
        """
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.site_url = site_url
        self.site_name = site_name
        self.timeout = timeout
        
        # 构建默认 headers
        default_headers = {}
        if site_url:
            default_headers["HTTP-Referer"] = site_url
        if site_name:
            default_headers["X-Title"] = site_name
        
        self.client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            default_headers=default_headers if default_headers else None,
            timeout=self.timeout,
        )
    
    def generate_image(
        self,
        prompt: str,
        image_urls: List[str],
        output_path: Path,
        aspect_ratio: str = "4:5",
        resolution: str = "2K",
        output_format: str = "png",
        log_prefix: str = "",
    ) -> TaskResult:
        """
        生成图片
        
        Args:
            prompt: 生成提示词
            image_urls: 输入图片 URL 列表（会作为参考图片发送）
            output_path: 输出路径
            aspect_ratio: 宽高比（会添加到 prompt 中）
            resolution: 分辨率（会添加到 prompt 中）
            output_format: 输出格式
            log_prefix: 日志前缀
            
        Returns:
            TaskResult: 任务结果
        """
        start_time = time.time()
        task_id = f"openrouter_{int(start_time * 1000)}"
        
        try:
            # 构建消息内容
            content = self._build_content(prompt, image_urls, aspect_ratio, resolution)
            
            logger.debug(f"{log_prefix} 发送请求到 OpenRouter, model={self.model}")
            
            # 调用 API
            # 参考: https://openrouter.ai/google/gemini-3-pro-image-preview
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": content,
                    }
                ],
                extra_body={
                    "modalities": ["image", "text"],
                },
            )
            
            elapsed = time.time() - start_time
            logger.debug(f"{log_prefix} API 响应耗时 {elapsed:.1f}秒")
            
            # 解析响应 - 按照官方示例
            # response = response.choices[0].message
            # if response.images:
            #     for image in response.images:
            #         image_url = image['image_url']['url']  # Base64 data URL
            message = response.choices[0].message
            
            # 提取图片 URL
            image_url = self._extract_image_url(message)
            
            if image_url:
                # 保存图片
                self._save_base64_image(image_url, output_path)
                
                logger.info(f"{log_prefix} ✅ 生成成功，耗时 {elapsed:.1f}秒")
                
                return TaskResult(
                    task_id=task_id,
                    status="success",
                    result_urls=[str(output_path)],
                )
            
            # 没有图片，检查文本响应
            text_content = getattr(message, 'content', '') or ''
            logger.error(f"{log_prefix} API 未返回图片，响应: {text_content[:200]}")
            raise APIError(f"OpenRouter 未返回图片: {text_content[:100]}", task_id=task_id)
            
        except Exception as e:
            if isinstance(e, APIError):
                raise
            logger.error(f"{log_prefix} OpenRouter 请求失败: {e}")
            raise APIError(f"OpenRouter 请求失败: {e}", task_id=task_id)
    
    def _extract_image_url(self, message) -> Optional[str]:
        """
        从响应消息中提取图片 URL
        
        官方示例:
            if response.images:
                for image in response.images:
                    image_url = image['image_url']['url']
        
        Args:
            message: API 响应的 message 对象
            
        Returns:
            图片的 base64 data URL，未找到返回 None
        """
        # 方式1: message.images 属性（官方示例）
        images = getattr(message, 'images', None)
        if images:
            for image in images:
                # image 可能是字典或对象
                if isinstance(image, dict):
                    url = image.get('image_url', {}).get('url')
                else:
                    # 尝试作为对象访问
                    image_url_obj = getattr(image, 'image_url', None)
                    if image_url_obj:
                        url = image_url_obj.get('url') if isinstance(image_url_obj, dict) else getattr(image_url_obj, 'url', None)
                    else:
                        url = None
                
                if url:
                    return url
        
        # 方式2: 检查 content 是否包含图片（某些模型可能用这种格式）
        content = getattr(message, 'content', None)
        if content:
            # content 可能是字符串或列表
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        if item.get('type') == 'image_url':
                            url = item.get('image_url', {}).get('url')
                            if url:
                                return url
                        elif item.get('type') == 'image':
                            # 某些格式可能直接是 image 类型
                            url = item.get('url') or item.get('data')
                            if url:
                                return url
        
        return None
    
    def _build_content(
        self,
        prompt: str,
        image_urls: List[str],
        aspect_ratio: str,
        resolution: str,
    ) -> list:
        """
        构建消息内容（支持图片输入）
        
        Args:
            prompt: 提示词
            image_urls: 输入图片 URL 列表
            aspect_ratio: 宽高比
            resolution: 分辨率
            
        Returns:
            消息内容列表
        """
        content = []
        
        # 添加输入图片（作为参考）
        for url in image_urls:
            content.append({
                "type": "image_url",
                "image_url": {"url": url},
            })
        
        # 构建增强的提示词
        enhanced_prompt = prompt
        if aspect_ratio:
            enhanced_prompt += f"\n\nOutput aspect ratio: {aspect_ratio}"
        if resolution:
            enhanced_prompt += f"\nOutput resolution: {resolution}"
        
        content.append({
            "type": "text",
            "text": enhanced_prompt,
        })
        
        return content
    
    def _save_base64_image(self, data_url: str, output_path: Path):
        """
        保存 base64 编码的图片
        
        Args:
            data_url: base64 数据 URL (data:image/png;base64,...) 或纯 base64
            output_path: 输出路径
        """
        try:
            # 解析 data URL
            if data_url.startswith("data:"):
                # 格式: data:image/png;base64,xxxxx
                _, base64_data = data_url.split(",", 1)
            else:
                # 纯 base64 数据
                base64_data = data_url
            
            # 解码
            image_data = base64.b64decode(base64_data)
            
            # 确保目录存在
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            # 保存
            with open(output_path, "wb") as f:
                f.write(image_data)
            
            logger.debug(f"图片已保存: {output_path}")
            
        except Exception as e:
            raise APIError(f"保存图片失败: {e}")
