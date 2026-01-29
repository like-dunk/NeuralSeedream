"""
Seedream 4.5 Edit API 客户端 - 负责与 KieAI Seedream 模型交互
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from .exceptions import APIError
from .models import TaskResult

logger = logging.getLogger(__name__)

# Seedream 支持的宽高比
SEEDREAM_ASPECT_RATIOS = {
    "1:1", "4:3", "3:4", "16:9", "9:16", "2:3", "3:2", "21:9"
}

# 宽高比映射（将不支持的比例映射到最接近的支持比例）
ASPECT_RATIO_MAPPING = {
    "4:5": "3:4",   # 4:5 (0.8) -> 3:4 (0.75) 最接近
    "5:4": "4:3",   # 5:4 (1.25) -> 4:3 (1.33) 最接近
}


class SeedreamClient:
    """KieAI Seedream 4.5 Edit API 客户端"""
    
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.kie.ai/api/v1",
        model: str = "seedream/4.5-edit",
        poll_interval: float = 2.0,
        max_wait: float = 1500.0,
    ):
        """
        初始化 Seedream API 客户端
        
        Args:
            api_key: API密钥
            base_url: API基础URL
            model: 模型名称
            poll_interval: 轮询间隔（秒）
            max_wait: 最大等待时间（秒）
        """
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.poll_interval = poll_interval
        self.max_wait = max_wait
        
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })
    
    def create_task(
        self,
        prompt: str,
        image_urls: List[str],
        aspect_ratio: str = "1:1",
        quality: str = "basic",
        **kwargs,
    ) -> str:
        """
        创建 Seedream 生成任务
        
        Args:
            prompt: 生成提示词
            image_urls: 输入图片URL列表
            aspect_ratio: 宽高比
            quality: 质量设置 (basic/high)
            
        Returns:
            task_id: 任务ID
        """
        url = f"{self.base_url}/jobs/createTask"
        
        # 验证并转换宽高比
        if aspect_ratio not in SEEDREAM_ASPECT_RATIOS:
            # 尝试使用映射
            mapped = ASPECT_RATIO_MAPPING.get(aspect_ratio)
            if mapped:
                logger.info(f"Seedream 不支持 {aspect_ratio}，自动映射为 {mapped}")
                aspect_ratio = mapped
            else:
                logger.warning(f"不支持的宽高比 {aspect_ratio}，使用默认值 1:1")
                aspect_ratio = "1:1"
        
        payload = {
            "model": self.model,
            "input": {
                "prompt": prompt,
                "image_urls": image_urls,
                "aspect_ratio": aspect_ratio,
                "quality": quality,
            },
        }
        
        logger.debug(f"创建 Seedream 任务: prompt长度={len(prompt)}, 图片数={len(image_urls)}, aspect_ratio={aspect_ratio}")
        logger.debug(f"Seedream 请求 payload: {json.dumps(payload, ensure_ascii=False)}")
        
        try:
            response = self.session.post(url, json=payload, timeout=30)
            
            logger.debug(f"API响应状态码: {response.status_code}")
            logger.debug(f"API响应内容: {response.text[:500]}")
            
            response.raise_for_status()
            
            data = response.json()
            
            if data.get("code") != 200:
                error_msg = data.get("message") or data.get("msg") or data.get("error") or f"错误码: {data.get('code')}"
                logger.error(f"API返回错误: {data}")
                raise APIError(
                    f"创建任务失败: {error_msg}",
                    status_code=data.get("code"),
                )
            
            task_id = data.get("data", {}).get("taskId")
            if not task_id:
                logger.error(f"API响应缺少taskId: {data}")
                raise APIError("创建任务成功但未返回taskId")
            
            logger.debug(f"Seedream 任务创建成功: {task_id}")
            return task_id
        
        except requests.RequestException as e:
            logger.error(f"API请求异常: {e}")
            raise APIError(f"API请求失败: {e}")
    
    def get_task_status(self, task_id: str) -> Dict[str, Any]:
        """
        获取任务状态
        
        Args:
            task_id: 任务ID
            
        Returns:
            任务状态信息
        """
        url = f"{self.base_url}/jobs/recordInfo"
        
        try:
            response = self.session.get(url, params={"taskId": task_id}, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            
            if data.get("code") != 200:
                raise APIError(
                    f"获取任务状态失败: {data.get('message') or data.get('msg', '未知错误')}",
                    task_id=task_id,
                    status_code=data.get("code"),
                )
            
            return data.get("data", {})
        
        except requests.RequestException as e:
            raise APIError(f"API请求失败: {e}", task_id=task_id)

    def wait_for_result(self, task_id: str, log_prefix: str = "") -> TaskResult:
        """
        等待任务完成
        
        Args:
            task_id: 任务ID
            log_prefix: 日志前缀
            
        Returns:
            TaskResult: 任务结果
        """
        start_time = time.time()
        last_log_time = 0
        
        while True:
            elapsed = time.time() - start_time
            if elapsed > self.max_wait:
                raise APIError(f"任务超时: {task_id}", task_id=task_id)
            
            status_data = self.get_task_status(task_id)
            status = status_data.get("state", "")
            
            if status == "success":
                result_json = status_data.get("resultJson", "{}")
                if isinstance(result_json, str):
                    try:
                        result_json = json.loads(result_json)
                    except json.JSONDecodeError:
                        result_json = {}
                
                result_urls = self._parse_result_urls(result_json)
                
                if not result_urls:
                    raise APIError(
                        f"任务成功但未找到结果URL: {result_json}",
                        task_id=task_id,
                    )
                
                logger.debug(f"{log_prefix} Seedream 生成耗时 {int(elapsed)} 秒")
                return TaskResult(
                    task_id=task_id,
                    status="success",
                    result_urls=result_urls,
                )
            
            elif status == "failed" or status == "fail":
                fail_code = status_data.get("failCode", "")
                fail_reason = status_data.get("failMsg") or status_data.get("failReason", "未知原因")
                raise APIError(
                    f"任务失败: {fail_reason} (code={fail_code})",
                    task_id=task_id,
                )
            
            elif status in ("pending", "processing", "running", "waiting"):
                current_time = int(elapsed)
                if current_time >= last_log_time + 10:
                    logger.info(f"{log_prefix} ⏳ Seedream 生成中... {current_time}秒")
                    last_log_time = current_time
                time.sleep(self.poll_interval)
            
            else:
                logger.warning(f"{log_prefix} ⚠️ 未知状态: {status}")
                time.sleep(self.poll_interval)
    
    def _parse_result_urls(self, result_json: Any) -> List[str]:
        """解析结果URL"""
        urls = []
        
        if isinstance(result_json, dict):
            for key in ["resultUrls", "result_urls", "urls", "images"]:
                if key in result_json:
                    val = result_json[key]
                    if isinstance(val, list):
                        urls.extend([str(u) for u in val if u])
                    elif isinstance(val, str):
                        urls.append(val)
            
            if not urls and "output" in result_json:
                output = result_json["output"]
                if isinstance(output, dict):
                    for key in ["images", "urls"]:
                        if key in output:
                            val = output[key]
                            if isinstance(val, list):
                                urls.extend([str(u) for u in val if u])
        
        elif isinstance(result_json, list):
            urls.extend([str(u) for u in result_json if u])
        
        elif isinstance(result_json, str):
            try:
                parsed = json.loads(result_json)
                return self._parse_result_urls(parsed)
            except json.JSONDecodeError:
                if result_json.startswith("http"):
                    urls.append(result_json)
        
        return urls
    
    def download_result(self, url: str, output_path: Path) -> Path:
        """下载结果图片"""
        try:
            response = requests.get(url, timeout=60, stream=True)
            response.raise_for_status()
            
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            logger.debug(f"下载完成: {output_path}")
            return output_path
        
        except requests.RequestException as e:
            raise APIError(f"下载失败: {url}, {e}")
    
    def generate_image(
        self,
        prompt: str,
        image_urls: List[str],
        output_path: Path,
        aspect_ratio: str = "1:1",
        quality: str = "basic",
        log_prefix: str = "",
        resolution: str = None,  # 忽略，Seedream 不使用此参数
        output_format: str = None,  # 忽略，Seedream 不使用此参数
        **kwargs,
    ) -> TaskResult:
        """
        完整的图片生成流程：创建任务 -> 等待结果 -> 下载图片
        
        Args:
            prompt: 生成提示词
            image_urls: 输入图片URL列表
            output_path: 输出路径
            aspect_ratio: 宽高比
            quality: 质量设置
            log_prefix: 日志前缀
            resolution: 分辨率（忽略，Seedream 不使用）
            output_format: 输出格式（忽略，Seedream 不使用）
            
        Returns:
            TaskResult: 任务结果
        """
        # 创建任务
        task_id = self.create_task(
            prompt=prompt,
            image_urls=image_urls,
            aspect_ratio=aspect_ratio,
            quality=quality,
        )
        
        # 等待结果
        result = self.wait_for_result(task_id, log_prefix=log_prefix)
        
        # 下载第一张图片
        if result.result_urls:
            self.download_result(result.result_urls[0], output_path)
        
        return result
