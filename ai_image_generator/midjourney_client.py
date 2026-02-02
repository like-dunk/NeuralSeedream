"""
KieAI Midjourney API 客户端 - 负责与 KieAI Midjourney 模型交互
支持 image-to-image (mj_img2img) 模式

API 文档: https://kie.ai/model-preview/features/mj-api
- 创建任务: POST /api/v1/mj/generate
- 查询结果: GET /api/v1/mj/record-info?taskId={taskId}
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

# Midjourney 支持的宽高比
MJ_ASPECT_RATIOS = {
    "1:1", "4:3", "3:4", "16:9", "9:16", "2:3", "3:2", "4:5", "5:4", "21:9", "9:21"
}

# Midjourney 支持的版本
MJ_VERSIONS = {"7", "6.1", "6", "5.2", "5.1", "niji6", "niji7"}

# 速度选项映射（用户友好名称 -> API 值）
MJ_SPEED_MAP = {
    "relax": "relaxed",
    "relaxed": "relaxed",
    "fast": "fast",
    "turbo": "turbo",
}

# 默认值
DEFAULT_MJ_VERSION = "7"
DEFAULT_MJ_SPEED = "fast"


class MidjourneyClient:
    """KieAI Midjourney API 客户端"""
    
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.kie.ai/api/v1",
        version: str = "7",
        speed: str = "fast",
        poll_interval: float = 5.0,
        max_wait: float = 600.0,
    ):
        """
        初始化 Midjourney API 客户端
        
        Args:
            api_key: API密钥
            base_url: API基础URL
            version: Midjourney 版本 (7, 6.1, 6, 5.2, 5.1, niji6, niji7)
            speed: 生成速度 (relaxed, fast, turbo)
            poll_interval: 轮询间隔（秒）
            max_wait: 最大等待时间（秒）
        """
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.version = version if version in MJ_VERSIONS else DEFAULT_MJ_VERSION
        # 标准化速度参数
        self.speed = MJ_SPEED_MAP.get(speed.lower(), DEFAULT_MJ_SPEED)
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
        stylization: int = 100,
        weirdness: int = 0,
        variety: int = 0,
        **kwargs,
    ) -> str:
        """
        创建 Midjourney image-to-image 生成任务
        
        Args:
            prompt: 生成提示词
            image_urls: 输入图片URL列表
            aspect_ratio: 宽高比
            stylization: 风格化程度 (0-1000)，默认100
            weirdness: 怪异程度 (0-3000)，默认0
            variety: 多样性 (0-100)，默认0
            
        Returns:
            task_id: 任务ID
        """
        url = f"{self.base_url}/mj/generate"
        
        # 验证宽高比
        if aspect_ratio not in MJ_ASPECT_RATIOS:
            logger.warning(f"不支持的宽高比 {aspect_ratio}，使用默认值 1:1")
            aspect_ratio = "1:1"
        
        payload = {
            "taskType": "mj_img2img",
            "prompt": prompt,
            "fileUrls": image_urls,  # 数组格式
            "aspectRatio": aspect_ratio,
            "version": self.version,
            "speed": self.speed,
            "stylization": max(0, min(1000, stylization)),
            "weirdness": max(0, min(3000, weirdness)),
            "variety": max(0, min(100, variety)),
            "waterMark": "",  # 水印，留空
        }
        
        logger.debug(f"创建 Midjourney 任务: prompt长度={len(prompt)}, 图片数={len(image_urls)}, aspect_ratio={aspect_ratio}")
        logger.debug(f"Midjourney 请求 payload: {json.dumps(payload, ensure_ascii=False)}")
        
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
            
            logger.debug(f"Midjourney 任务创建成功: {task_id}")
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
        url = f"{self.base_url}/mj/record-info"
        
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
            
            # Midjourney API 使用 successFlag 字段判断状态
            # successFlag: 1=成功, 0=处理中, -1=失败
            success_flag = status_data.get("successFlag")
            
            if success_flag == 1:
                # 任务成功，解析结果URL
                result_info = status_data.get("resultInfoJson", {})
                if isinstance(result_info, str):
                    try:
                        result_info = json.loads(result_info)
                    except json.JSONDecodeError:
                        result_info = {}
                
                result_urls = self._parse_result_urls(result_info)
                
                if not result_urls:
                    raise APIError(
                        f"任务成功但未找到结果URL: {result_info}",
                        task_id=task_id,
                    )
                
                logger.debug(f"{log_prefix} Midjourney 生成耗时 {int(elapsed)} 秒")
                return TaskResult(
                    task_id=task_id,
                    status="success",
                    result_urls=result_urls,
                )
            
            elif success_flag == -1 or success_flag == 0 and status_data.get("errorCode"):
                # 任务失败
                error_code = status_data.get("errorCode", "")
                error_message = status_data.get("errorMessage", "未知原因")
                raise APIError(
                    f"任务失败: {error_message} (code={error_code})",
                    task_id=task_id,
                )
            
            else:
                # 任务处理中
                current_time = int(elapsed)
                if current_time >= last_log_time + 10:
                    logger.info(f"{log_prefix} ⏳ Midjourney 生成中... {current_time}秒")
                    last_log_time = current_time
                time.sleep(self.poll_interval)
    
    def _parse_result_urls(self, result_info: Any) -> List[str]:
        """解析结果URL"""
        urls = []
        
        if isinstance(result_info, dict):
            # 标准格式: {"resultUrls": [{"resultUrl": "..."}, ...]}
            result_urls = result_info.get("resultUrls", [])
            if isinstance(result_urls, list):
                for item in result_urls:
                    if isinstance(item, dict):
                        url = item.get("resultUrl")
                        if url:
                            urls.append(url)
                    elif isinstance(item, str):
                        urls.append(item)
            
            # 备用字段
            for key in ["urls", "images", "result_urls"]:
                if key in result_info and not urls:
                    val = result_info[key]
                    if isinstance(val, list):
                        urls.extend([str(u) for u in val if u])
                    elif isinstance(val, str):
                        urls.append(val)
        
        elif isinstance(result_info, list):
            urls.extend([str(u) for u in result_info if u])
        
        elif isinstance(result_info, str):
            try:
                parsed = json.loads(result_info)
                return self._parse_result_urls(parsed)
            except json.JSONDecodeError:
                if result_info.startswith("http"):
                    urls.append(result_info)
        
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
        stylization: int = 100,
        weirdness: int = 0,
        variety: int = 0,
        log_prefix: str = "",
        resolution: str = None,  # 忽略，Midjourney 不使用此参数
        output_format: str = None,  # 忽略，Midjourney 不使用此参数
        **kwargs,
    ) -> TaskResult:
        """
        完整的图片生成流程：创建任务 -> 等待结果 -> 下载图片
        
        Midjourney 会生成4张图片，默认下载第一张
        
        Args:
            prompt: 生成提示词
            image_urls: 输入图片URL列表
            output_path: 输出路径
            aspect_ratio: 宽高比
            stylization: 风格化程度 (0-1000)
            weirdness: 怪异程度 (0-3000)
            variety: 多样性 (0-100)
            log_prefix: 日志前缀
            resolution: 分辨率（忽略，Midjourney 不使用）
            output_format: 输出格式（忽略，Midjourney 不使用）
            
        Returns:
            TaskResult: 任务结果
        """
        # 创建任务
        task_id = self.create_task(
            prompt=prompt,
            image_urls=image_urls,
            aspect_ratio=aspect_ratio,
            stylization=stylization,
            weirdness=weirdness,
            variety=variety,
        )
        
        # 等待结果
        result = self.wait_for_result(task_id, log_prefix=log_prefix)
        
        # 下载第一张图片
        if result.result_urls:
            self.download_result(result.result_urls[0], output_path)
        
        return result
