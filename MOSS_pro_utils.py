"""MOSS Pro SDK - 文件上传工具

通过文件夹路径（moss_path）查询该路径下所有文件的媒资ID列表，并支持文件上传功能。

认证方式：使用明文 AKSK (Access Key ID & Access Key Secret) 认证
- 前端直接传输明文 AccessKeyId 和 AccessKeySecret
- 后端自动处理加密、验证和安全检查
- 无需前端计算复杂的 HMAC-SHA256 签名

文件上传特性：
- 支持 OSS 直传上传
- 超过 100MB 的文件自动使用分片上传
- 小于 100MB 的文件使用单分片上传
- 支持上传进度显示
"""

import os
import asyncio
import hashlib
import mimetypes
from typing import Optional, Dict, Any, Callable
from pathlib import Path

import httpx

# 使用标准 logging
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
log = logging.getLogger(__name__)

# 分片大小阈值（100MB）
CHUNK_SIZE_THRESHOLD = 100 * 1024 * 1024  # 100MB
# 分片大小（10MB，适合大文件）
PART_SIZE = 10 * 1024 * 1024  # 10MB


class MossConfig:
    """Moss API 配置 - 使用明文 AKSK 认证"""
    
    def __init__(
        self,
        base_url: Optional[str] = None,
        access_key_id: Optional[str] = None,
        access_key_secret: Optional[str] = None,
        bucket_name: Optional[str] = None,
        timeout: int = 300,
        max_retries: int = 3
    ):
        self.base_url = base_url or os.getenv("MOSS_BASE_URL", "http://localhost:8000")
        self.access_key_id = access_key_id or os.getenv("MOSS_ACCESS_KEY_ID")
        self.access_key_secret = access_key_secret or os.getenv("MOSS_ACCESS_KEY_SECRET")
        self.bucket_name = bucket_name or os.getenv("MOSS_BUCKET_NAME")
        self.timeout = timeout
        self.max_retries = max_retries
        
        if not self.access_key_id or not self.access_key_secret:
            raise ValueError("Moss Access Key ID and Access Key Secret must be provided via config or environment variables")
        
        if not self.bucket_name:
            raise ValueError("Moss Bucket Name must be provided via config or environment variables")


class MossAPIClient:
    """Moss API HTTP 客户端 - 使用明文 AKSK 认证
    
    通过 X-Access-Key-Id 和 X-Access-Key-Secret 头部发送明文凭证。
    后端会自动验证凭证并处理所有安全检查。
    """
    
    def __init__(self, config: MossConfig):
        self.config = config
        self.client = httpx.AsyncClient(
            base_url=config.base_url,
            timeout=config.timeout,
            trust_env=False  # 禁用代理
        )
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()
    
    def _get_auth_headers(self) -> Dict[str, str]:
        """获取明文AKSK认证头部
        
        使用 X-Access-Key-Id 和 X-Access-Key-Secret 头部进行明文认证。
        后端会自动处理加密和验证，前端无需计算签名。
        """
        # 确保不会返回None值
        access_key_id = self.config.access_key_id or ""
        access_key_secret = self.config.access_key_secret or ""
        
        return {
            "X-Access-Key-Id": access_key_id,
            "X-Access-Key-Secret": access_key_secret
        }
    
    async def request(
        self, 
        method: str, 
        url: str, 
        **kwargs
    ) -> httpx.Response:
        """发送带明文AKSK认证的请求
        
        使用 X-Access-Key-Id 和 X-Access-Key-Secret 头部进行认证。
        后端会自动验证明文凭证，无需前端计算签名。
        """
        full_url = f"{self.config.base_url}{url}"
        
        for attempt in range(self.config.max_retries):
            try:
                # 准备头部
                headers = kwargs.get("headers", {})
                
                # 添加明文AKSK认证头部
                auth_headers = self._get_auth_headers()
                headers.update(auth_headers)
                
                # 设置内容类型（如果有JSON数据）
                if "json" in kwargs and "Content-Type" not in headers:
                    headers["Content-Type"] = "application/json"
                
                kwargs["headers"] = headers
                
                # 发送请求
                response = await self.client.request(method, full_url, **kwargs)
                
                log.debug(f"{method} {url} -> {response.status_code}")
                # 对于404状态码，静默处理不记录错误日志，避免干扰正常的文件夹创建流程
                if response.status_code >= 400 and response.status_code != 404:
                    log.error(f"请求失败: {response.text}")
                    try:
                        error_detail = response.json()
                        log.error(f"错误详情: {error_detail}")
                    except:
                        pass
                elif response.status_code == 404:
                    # 404状态码静默处理，用于文件夹不存在的正常检查流程
                    log.debug(f"资源未找到 (404): {response.text}")
                
                response.raise_for_status()
                return response
                
            except httpx.RequestError as e:
                if attempt == self.config.max_retries - 1:
                    raise
                log.warning(f"Request failed (attempt {attempt + 1}): {e}")
                await asyncio.sleep(2 ** attempt)  # 指数退避
        
        raise Exception("Max retries exceeded")


class MossProUtils:
    """Moss Pro 工具 - 支持文件上传和媒资查询
    
    通过文件夹路径查询该路径下所有文件的媒资ID列表。
    支持递归/非递归查询、状态过滤、分页等功能。
    同时支持获取文件夹的层级结构。
    新增：支持文件上传功能，使用 OSS 直传上传。
    """
    
    def __init__(self, config: MossConfig):
        self.config = config
        self.api_client = MossAPIClient(config)
    
    async def __aenter__(self):
        await self.api_client.__aenter__()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.api_client.__aexit__(exc_type, exc_val, exc_tb)
    
    def _build_full_path(self, relative_path: str) -> str:
        """将用户提供的相对路径与 bucket_name 拼接成完整路径
        
        Args:
            relative_path: 用户提供的相对路径（不包含 bucket_name）
                          例如: "/" 或 "/videos/" 或 "/2025-10/image/"
        
        Returns:
            str: 完整路径，格式为 /{bucket_name}{relative_path}
                例如: "/阿里/" 或 "/阿里/videos/" 或 "/阿里/2025-10/image/"
        """
        bucket_name = self.config.bucket_name
        
        # 确保 relative_path 以 / 开头
        if not relative_path.startswith("/"):
            relative_path = "/" + relative_path
        
        # 如果相对路径是根目录 "/"，返回 /bucket_name/
        if relative_path == "/":
            return f"/{bucket_name}/"
        
        # 否则拼接: /bucket_name/relative_path
        # 去掉 relative_path 开头的 /，避免双斜杠
        relative_path_trimmed = relative_path.lstrip("/")
        full_path = f"/{bucket_name}/{relative_path_trimmed}"
        
        return full_path
    
    @staticmethod
    def _calculate_file_hash(file_path: str) -> str:
        """计算文件的 SHA256 哈希值
        
        Args:
            file_path: 文件路径
            
        Returns:
            str: SHA256 哈希值（64字符）
        """
        sha256_hash = hashlib.sha256()
        
        with open(file_path, 'rb') as f:
            while chunk := f.read(8192):  # 8KB 块
                sha256_hash.update(chunk)
        
        return sha256_hash.hexdigest()
    
    @staticmethod
    def _get_content_type(file_path: str) -> str:
        """获取文件的 MIME 类型
        
        Args:
            file_path: 文件路径
            
        Returns:
            str: MIME 类型，默认为 application/octet-stream
        """
        content_type, _ = mimetypes.guess_type(file_path)
        return content_type or "application/octet-stream"
    
    async def _get_folder_id_by_path(self, folder_path: str) -> int:
        """通过文件夹路径获取 folder_id，如果不存在则自动创建
        
        Args:
            folder_path: 文件夹路径（不含 bucket_name），例如 "/" 或 "/videos/"
            
        Returns:
            int: folder_id
            
        Raises:
            Exception: 如果创建失败
        """
        try:
            # 兼容：若输入为完整路径（以 /{bucket_name}/ 开头），直接使用；否则按相对路径拼接
            bucket_name = self.config.bucket_name
            if folder_path.startswith(f"/{bucket_name}/") or folder_path == f"/{bucket_name}/":
                full_path = folder_path
            else:
                full_path = self._build_full_path(folder_path)
            
            # 调用结构查询 API，获取 folder_id
            response = await self.api_client.request(
                "GET",
                "/api/v1/folders/structure/by-path",
                params={
                    "moss_path": full_path,
                    "include_bucket": False
                }
            )
            
            data = response.json()
            folder_id = data.get("base_folder_id")
            
            if not folder_id:
                raise Exception(f"无法获取文件夹 ID，路径: {folder_path}")
            
            log.info(f"通过路径获取 folder_id 成功: {folder_path} -> {folder_id}")
            return folder_id
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                # 文件夹不存在，自动创建（静默处理，不记录错误日志）
                log.info(f"文件夹不存在，开始自动创建: {folder_path}")
                return await self._create_folder_path(folder_path)
            elif e.response.status_code == 401:
                # 认证失败
                error_msg = "认证失败：Access Key ID 或 Secret 不正确，请检查您的凭证信息"
                log.error(error_msg)
                raise Exception(error_msg)
            elif e.response.status_code == 403:
                # 权限不足，记录清晰的中文提示
                log.warning(f"权限不足：无法访问路径 {folder_path}，正在尝试直接创建文件夹")
                return await self._create_folder_path(folder_path)
            else:
                error_msg = f"API请求失败，状态码 {e.response.status_code}，请检查网络连接或联系管理员"
                log.error(error_msg)
                raise Exception(error_msg)
        except Exception as e:
            log.error(f"获取文件夹 ID 失败: folder_path={folder_path}, error={e}")
            raise
    
    async def _create_folder_path(self, folder_path: str) -> int:
        """递归创建文件夹路径（优化版：只创建不存在的文件夹）
        
        将完整路径拆分为多层，从根目录开始逐层检查，只创建不存在的文件夹。
        
        **路径转换逻辑：**
        - 用户输入: `/video/12/11/` (相对于bucket的路径)
        - bucket_name: `dev`
        - 实际完整路径: `/dev/video/12/11/`
        - SDK会自动通过 `_build_full_path()` 拼接 bucket_name
        
        **创建优化：**
        - 如果 `/video/` 和 `/video/12/` 已存在，只创建 `/video/12/11/`
        - 避免重复检查和创建已存在的文件夹
        
        Args:
            folder_path: 文件夹路径（不含bucket_name），例如 "/video/12/11/"
                        SDK会自动拼接为 "/{bucket_name}/video/12/11/"
            
        Returns:
            int: 最终创建的文件夹的 folder_id
            
        Raises:
            Exception: 如果创建失败
        """
        try:
            # 确保路径格式正确
            folder_path = folder_path.strip()
            if not folder_path.startswith("/"):
                folder_path = "/" + folder_path
            if not folder_path.endswith("/"):
                folder_path = folder_path + "/"
            
            # 如果是根目录，直接获取bucket的folder_id
            if folder_path == "/":
                full_path = self._build_full_path("/")
                response = await self.api_client.request(
                    "GET",
                    "/api/v1/folders/structure/by-path",
                    params={
                        "moss_path": full_path,
                        "include_bucket": False
                    }
                )
                data = response.json()
                return data.get("base_folder_id")
            
            # 拆分路径为各层级
            # 例如: "/videos/2024/movie/" -> ["/videos/", "/videos/2024/", "/videos/2024/movie/"]
            parts = folder_path.strip("/").split("/")
            path_parts = []
            current_path = "/"
            for part in parts:
                current_path = current_path + part + "/"
                path_parts.append(current_path)
            
            log.info(f"路径层级: {path_parts}")
            
            # 从后往前找到第一个不存在的层级
            # 例如: 如果 /videos/ 和 /videos/2024/ 存在，但 /videos/2024/movie/ 不存在
            # 则从 /videos/2024/movie/ 开始创建
            first_missing_index = None
            parent_id = None
            
            # 从前往后检查每一层
            for i in range(len(path_parts)):
                try:
                    # 直接调用API检查文件夹是否存在，避免递归调用
                    full_path = self._build_full_path(path_parts[i])
                    response = await self.api_client.request(
                        "GET",
                        "/api/v1/folders/structure/by-path",
                        params={
                            "moss_path": full_path,
                            "include_bucket": False
                        }
                    )
                    data = response.json()
                    folder_id = data.get("base_folder_id")
                    
                    if not folder_id:
                        # 找到第一个不存在的层级
                        first_missing_index = i
                        log.info(f"✗ 文件夹不存在: {path_parts[i]}，从此层开始创建")
                        break
                        
                    log.info(f"✓ 文件夹已存在: {path_parts[i]} (ID: {folder_id})")
                    parent_id = folder_id  # 记录最后一个存在的文件夹ID作为父ID
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 404:
                        # 找到第一个不存在的层级，静默处理
                        first_missing_index = i
                        log.info(f"✗ 文件夹不存在: {path_parts[i]}，从此层开始创建")
                        break
                    else:
                        # 其他错误直接抛出
                        raise
                except Exception as e:
                    log.error(f"检查文件夹存在性失败: path={path_parts[i]}, error={e}")
                    raise
            
            # 如果所有层级都存在，直接返回最后一层的ID
            if first_missing_index is None:
                if parent_id is not None:
                    log.info(f"所有文件夹都已存在，返回最终ID: {parent_id}")
                    return parent_id
                else:
                    # 这种情况理论上不应该发生，但为了类型安全添加处理
                    raise Exception("无法确定文件夹ID")
            
            # 如果第一层就不存在，parent_id 应该是 bucket 的 folder_id
            if parent_id is None:
                root_path = self._build_full_path("/")
                root_response = await self.api_client.request(
                    "GET",
                    "/api/v1/folders/structure/by-path",
                    params={
                        "moss_path": root_path,
                        "include_bucket": False
                    }
                )
                root_data = root_response.json()
                parent_id = root_data.get("base_folder_id")
                log.info(f"获取bucket根目录ID: {parent_id}")
            
            # 从第一个不存在的层级开始，逐层创建文件夹
            current_folder_id = parent_id
            for i in range(first_missing_index, len(path_parts)):
                folder_name = parts[i]
                
                log.info(f"创建文件夹: {folder_name} (父ID: {parent_id}, 路径: {path_parts[i]})")
                
                # 调用创建文件夹 API（带重试机制）
                max_retries = 3
                create_response = None
                for attempt in range(max_retries):
                    try:
                        create_response = await self.api_client.request(
                            "POST",
                            "/api/v1/folders/",
                            json={
                                "name": folder_name,
                                "parent_id": parent_id
                            }
                        )
                        break  # 成功则跳出重试循环
                    except httpx.HTTPStatusError as e:
                        if e.response.status_code == 403:
                            # 权限不足，记录清晰的中文提示但继续尝试
                            log.warning(f"创建文件夹权限不足: {folder_name} (尝试 {attempt + 1}/{max_retries})，请检查账号权限")
                            if attempt == max_retries - 1:  # 最后一次尝试仍然失败
                                raise Exception(f"创建文件夹权限不足: {folder_name}，请检查账号权限或联系管理员")
                            await asyncio.sleep(1)  # 等待1秒后重试
                        else:
                            raise
                    except Exception as e:
                        if attempt == max_retries - 1:  # 最后一次尝试仍然失败
                            raise Exception(f"创建文件夹失败: {folder_name}，错误信息: {str(e)}")
                        log.warning(f"创建文件夹失败: {folder_name} (尝试 {attempt + 1}/{max_retries}): {e}，1秒后重试")
                        await asyncio.sleep(1)  # 等待1秒后重试
                
                if create_response is None:
                    raise Exception(f"创建文件夹失败: {folder_name}")
                
                create_data = create_response.json()
                current_folder_id = create_data.get("id")
                parent_id = current_folder_id  # 下一层的父ID就是当前创建的ID
                
                log.info(f"✅ 文件夹创建成功: {path_parts[i]} (ID: {current_folder_id})")
            
            log.info(f"✅ 路径创建完成: {folder_path} (最终ID: {current_folder_id})")
            return current_folder_id
            
        except Exception as e:
            log.error(f"创建文件夹路径失败: folder_path={folder_path}, error={e}")
            raise
    
    async def upload_file(
        self,
        file_path: str,
        folder_path: str = "/",
        tags: Optional[list] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        enable_content_analysis: bool = False,
        frame_level: str = "medium"
    ) -> Dict[str, Any]:
        """上传文件到 MOSS（使用 OSS 直传）
        
        支持大文件分片上传（超过 100MB 自动分片）。
        如果目标文件夹不存在，会自动创建。
        
        Args:
            file_path: 本地文件路径
            folder_path: 目标文件夹路径，例如 "/" 或 "/videos/"，默认为根目录
                        如果文件夹不存在会自动创建
            tags: 文件标签列表，可选
            progress_callback: 进度回调函数，接收 (uploaded_bytes, total_bytes)
            enable_content_analysis: 是否启用AI内容分析（仅支持视频文件）
            frame_level: 抽帧等级: low/medium/high
            
        Returns:
            Dict: 包含上传结果，包括：
                - success: 是否成功
                - moss_id: MOSS ID
                - oss_path: OSS 路径
                - file_size: 文件大小
                - message: 提示信息
                
        Examples:
            上传到根目录：
            ```python
            result = await moss.upload_file(
                file_path="/path/to/video.mp4"
            )
            print(f"上传成功: {result['moss_id']}")
            ```
            
            上传到指定文件夹：
            ```python
            result = await moss.upload_file(
                file_path="/path/to/video.mp4",
                folder_path="/videos/"
            )
            print(f"上传成功: {result['moss_id']}")
            ```
            
            带进度显示：
            ```python
            def on_progress(uploaded, total):
                percent = (uploaded / total) * 100
                print(f"进度: {percent:.1f}%")
            
            result = await moss.upload_file(
                file_path="/path/to/large_video.mp4",
                folder_path="/videos/",
                progress_callback=on_progress
            )
            ```
        """
        # 通过路径获取 folder_id（自动创建不存在的文件夹）
        folder_id = await self._get_folder_id_by_path(folder_path)
        
        file_path_obj = Path(file_path)
        
        if not file_path_obj.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")
        
        file_name = file_path_obj.name
        file_size = file_path_obj.stat().st_size
        
        log.info(f"🚀 开始上传文件: {file_name}, 大小: {file_size} 字节 ({file_size / 1024 / 1024:.2f} MB)")
        
        # 计算文件哈希
        log.info("🔐 计算文件 SHA256 哈希...")
        file_hash = self._calculate_file_hash(file_path)
        log.info(f"✅ 文件哈希: {file_hash[:16]}...")
        
        # 获取 MIME 类型
        content_type = self._get_content_type(file_path)
        log.info(f"📄 文件类型: {content_type}")
        
        # 1. 初始化分片上传
        log.info("📤 初始化分片上传...")
        init_request = {
            "file_name": file_name,
            "file_size": file_size,
            "file_hash": file_hash,
            "folder_id": folder_id,
            "content_type": content_type,
            "tags": tags or [],
            "enable_content_analysis": enable_content_analysis,
            "frame_level": frame_level
        }
        
        init_response = await self.api_client.request(
            "POST",
            "/api/v1/oss-direct-upload/init-multipart",
            json=init_request
        )
        
        init_data = init_response.json()
        
        # 检查文件是否已存在（MOSS 云端已有相同文件）
        if init_data.get("file_exists"):
            if init_data.get("is_active"):
                existing_moss_id = init_data.get("existing_moss_id")
                log.debug(f"📦 MOSS 云端已存在相同文件，复用 MOSS ID: {existing_moss_id}")
                return {
                    "success": False,
                    "file_exists": True,
                    "existing_moss_id": existing_moss_id,
                    "message": init_data.get("message", "文件已存在")
                }
            else:
                log.debug("📦 MOSS 云端文件已重新激活")
                return {
                    "success": True,
                    "file_exists": True,
                    "existing_moss_id": init_data.get("existing_moss_id"),
                    "message": init_data.get("message", "文件已重新激活")
                }
        
        upload_token = init_data["upload_token"]
        upload_id = init_data["upload_id"]
        oss_key = init_data["oss_key"]
        
        log.info(f"✅ 初始化成功 - upload_id: {upload_id[:16]}...")
        
        # 2. 上传分片
        # 判断是否需要分片上传
        use_multipart = file_size > CHUNK_SIZE_THRESHOLD
        
        if use_multipart:
            log.info(f"📦 使用分片上传（文件大小超过 100MB）")
            # 计算分片数量
            total_parts = (file_size + PART_SIZE - 1) // PART_SIZE
            log.info(f"分片数量: {total_parts}, 每片大小: {PART_SIZE / 1024 / 1024:.2f} MB")
            actual_part_size = PART_SIZE
        else:
            log.info(f"📤 使用单分片上传（文件大小小于 100MB）")
            total_parts = 1
            actual_part_size = file_size  # 单分片时，分片大小就是整个文件大小
        
        uploaded_bytes = 0
        parts = []
        
        with open(file_path, 'rb') as f:
            for part_number in range(1, total_parts + 1):
                # 计算当前分片的起始和结束位置
                if use_multipart:
                    start_pos = (part_number - 1) * PART_SIZE
                    end_pos = min(start_pos + PART_SIZE, file_size)
                else:
                    # 单分片上传，读取整个文件
                    start_pos = 0
                    end_pos = file_size
                part_size = end_pos - start_pos
                
                log.info(f"📤 上传分片 {part_number}/{total_parts} ({part_size / 1024 / 1024:.2f} MB)...")
                
                # 读取分片数据
                f.seek(start_pos)
                part_data = f.read(part_size)
                
                # 获取预签名 URL
                url_response = await self.api_client.request(
                    "POST",
                    "/api/v1/oss-direct-upload/get-upload-url",
                    json={
                        "upload_token": upload_token,
                        "part_number": part_number
                    }
                )
                
                url_data = url_response.json()
                upload_url = url_data["upload_url"]
                
                # 上传分片到 OSS（增加超时时间和重试机制）
                max_upload_retries = 3
                upload_success = False
                last_error = None
                
                for upload_attempt in range(max_upload_retries):
                    try:
                        # 根据分片大小动态设置超时时间（每MB 30秒，最少120秒，最多600秒）
                        timeout_per_mb = 30
                        min_timeout = 120
                        max_timeout = 600
                        calculated_timeout = (part_size / 1024 / 1024) * timeout_per_mb
                        timeout_seconds = max(min_timeout, min(calculated_timeout, max_timeout))
                        
                        log.debug(f"分片 {part_number} 上传超时设置: {timeout_seconds}秒 (分片大小: {part_size / 1024 / 1024:.2f} MB)")
                        
                        # 创建HTTP客户端，禁用代理，使用更宽松的超时配置
                        timeout_config = httpx.Timeout(
                            connect=30.0,  # 连接超时30秒
                            read=timeout_seconds,  # 读取超时根据文件大小动态设置
                            write=timeout_seconds,  # 写入超时
                            pool=30.0  # 连接池超时
                        )
                        
                        async with httpx.AsyncClient(
                            timeout=timeout_config,
                            trust_env=False,  # 禁用代理
                            follow_redirects=True,
                            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
                        ) as upload_client:
                            # OSS分片上传不需要Content-Type头部，让OSS自动检测
                            upload_response = await upload_client.put(
                                upload_url,
                                content=part_data
                            )
                            upload_response.raise_for_status()
                            
                            # 获取 ETag（OSS返回的ETag可能带引号，需要去除）
                            etag = upload_response.headers.get("ETag", "").strip('"').strip("'")
                            if not etag:
                                # 如果响应头没有ETag，尝试从响应体获取
                                log.warning(f"分片 {part_number} 响应头中没有ETag，尝试其他方式获取")
                                # OSS分片上传PUT请求通常会在响应头中返回ETag
                                raise Exception("无法获取ETag，上传可能失败")
                            
                            parts.append({
                                "part_number": part_number,
                                "etag": etag
                            })
                            
                            upload_success = True
                            log.info(f"✅ 分片 {part_number} 上传成功，ETag: {etag[:16]}...")
                            break
                            
                    except (httpx.ReadError, httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as e:
                        last_error = e
                        error_msg = str(e)
                        if isinstance(e, httpx.HTTPStatusError):
                            error_msg = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
                        
                        if upload_attempt < max_upload_retries - 1:
                            wait_time = 2 ** upload_attempt  # 指数退避：1秒、2秒、4秒
                            log.warning(f"分片 {part_number} 上传失败（尝试 {upload_attempt + 1}/{max_upload_retries}）: {error_msg}，{wait_time}秒后重试...")
                            await asyncio.sleep(wait_time)
                        else:
                            log.error(f"分片 {part_number} 上传失败，已重试 {max_upload_retries} 次")
                            raise Exception(f"分片 {part_number} 上传失败，已重试 {max_upload_retries} 次: {error_msg}")
                    except Exception as e:
                        last_error = e
                        error_msg = str(e)
                        if upload_attempt < max_upload_retries - 1:
                            wait_time = 2 ** upload_attempt
                            log.warning(f"分片 {part_number} 上传失败（尝试 {upload_attempt + 1}/{max_upload_retries}）: {error_msg}，{wait_time}秒后重试...")
                            await asyncio.sleep(wait_time)
                        else:
                            raise Exception(f"分片 {part_number} 上传失败，已重试 {max_upload_retries} 次: {error_msg}")
                
                if not upload_success:
                    error_msg = str(last_error) if last_error else "未知错误"
                    raise Exception(f"分片 {part_number} 上传失败: {error_msg}")
                
                uploaded_bytes += part_size
                
                # 调用进度回调
                if progress_callback:
                    progress_callback(uploaded_bytes, file_size)
                
                log.info(f"✅ 分片 {part_number}/{total_parts} 上传完成")
        
        log.info(f"✅ 所有分片上传完成 ({uploaded_bytes / 1024 / 1024:.2f} MB)")
        
        # 3. 完成上传
        log.info("🔗 完成分片上传...")
        complete_response = await self.api_client.request(
            "POST",
            "/api/v1/oss-direct-upload/complete-multipart",
            json={
                "upload_token": upload_token,
                "parts": parts
            }
        )
        
        complete_data = complete_response.json()
        
        log.info(f"🎉 文件上传成功!")
        log.info(f"  • MOSS ID: {complete_data['moss_id']}")
        log.info(f"  • OSS 路径: {complete_data['oss_path']}")
        log.info(f"  • 文件大小: {complete_data['file_size'] / 1024 / 1024:.2f} MB")
        
        return {
            "success": True,
            "moss_id": complete_data["moss_id"],
            "oss_path": complete_data["oss_path"],
            "file_size": complete_data["file_size"],
            "message": complete_data.get("message", "文件上传成功")
        }
    
    async def get_file_metadata(self, moss_id: str) -> Dict[str, Any]:
        """通过 MOSS ID 获取文件元数据
        
        Args:
            moss_id: MOSS 文件 ID
            
        Returns:
            Dict: 文件元数据，包括：
                - moss_id: MOSS ID
                - oss_path: OSS 路径
                - moss_path: MOSS 路径
                - file_name: 文件名
                - file_size: 文件大小
                - file_format: 文件格式
                - video_metadata: 视频元数据（如果是视频）
                  - width: 宽度
                  - height: 高度
                  - duration: 时长（秒）
                  - frame_rate: 帧率
                  - bitrate: 码率
                  - video_codec: 视频编码
        """
        try:
            response = await self.api_client.request(
                "GET",
                f"/api/v1/files/{moss_id}"
            )
            return response.json()
        except Exception as e:
            log.error(f"获取文件元数据失败: moss_id={moss_id}, error={e}")
            raise
    
    async def wait_for_video_metadata(
        self,
        moss_id: str,
        max_wait_seconds: int = 120,
        poll_interval: int = 5
    ) -> Dict[str, Any]:
        """等待视频元数据就绪（ICE 媒资注册完成）
        
        上传视频后，ICE 媒资注册是异步的，需要等待一段时间才能获取到时长等元数据。
        此方法会轮询查询，直到获取到视频时长或超时。
        
        Args:
            moss_id: MOSS 文件 ID
            max_wait_seconds: 最大等待时间（秒），默认 120 秒
            poll_interval: 轮询间隔（秒），默认 5 秒
            
        Returns:
            Dict: 文件元数据（包含 video_metadata）
            
        Raises:
            TimeoutError: 等待超时
            Exception: 获取元数据失败
        """
        elapsed = 0
        
        while elapsed < max_wait_seconds:
            try:
                metadata = await self.get_file_metadata(moss_id)
                
                # 检查是否有视频元数据和时长
                video_metadata = metadata.get("video_metadata")
                if video_metadata and video_metadata.get("duration"):
                    log.info(f"视频元数据就绪: duration={video_metadata['duration']}s")
                    return metadata
                
                log.info(f"等待视频元数据就绪... ({elapsed}/{max_wait_seconds}s)")
                
            except Exception as e:
                log.warning(f"查询元数据失败，继续等待: {e}")
            
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        
        raise TimeoutError(f"等待视频元数据超时（{max_wait_seconds}秒）")
    
    async def get_video_snapshot_urls(
        self,
        oss_path: str,
        timestamps_ms: list[int],
        width: int = 720,
        expire_seconds: int = 3600
    ) -> Dict[str, Any]:
        """批量获取视频截帧签名 URL
        
        通过 OSS 视频截帧功能生成带签名的缩略图 URL 列表。
        如果时间戳超过 100 个，会自动分批请求。
        
        Args:
            oss_path: OSS 上的视频路径（如 Dev/2025-12/video/xxx.mp4）
            timestamps_ms: 需要截帧的时间点列表（毫秒）
            width: 缩略图宽度，默认 720
            expire_seconds: 签名 URL 有效期（秒），默认 3600
            
        Returns:
            Dict: 包含签名 URL 列表
                - success: 是否成功
                - urls: URL 列表，每项包含 timestamp_ms 和 url
                
        Examples:
            ```python
            result = await moss.get_video_snapshot_urls(
                oss_path="Dev/2025-12/video/xxx.mp4",
                timestamps_ms=[0, 500, 1000, 1500, 2000],
                width=720,
                expire_seconds=3600
            )
            for item in result["urls"]:
                print(f"{item['timestamp_ms']}ms: {item['url']}")
            ```
        """
        try:
            BATCH_SIZE = 100
            all_urls = []
            total_timestamps = len(timestamps_ms)
            
            log.info(f"获取视频截帧签名 URL: {oss_path}, 共 {total_timestamps} 帧")
            
            # 分批请求
            for i in range(0, total_timestamps, BATCH_SIZE):
                batch_timestamps = timestamps_ms[i:i + BATCH_SIZE]
                batch_num = i // BATCH_SIZE + 1
                total_batches = (total_timestamps + BATCH_SIZE - 1) // BATCH_SIZE
                
                log.info(f"获取截帧 URL 批次 {batch_num}/{total_batches}，共 {len(batch_timestamps)} 帧")
                
                response = await self.api_client.request(
                    "POST",
                    "/api/v1/oss/video-snapshot-urls",
                    json={
                        "oss_path": oss_path,
                        "timestamps_ms": batch_timestamps,
                        "width": width,
                        "expire_seconds": expire_seconds
                    }
                )
                
                data = response.json()
                batch_urls = data.get("urls", [])
                all_urls.extend(batch_urls)
            
            log.info(f"获取截帧 URL 成功，共 {len(all_urls)} 个")
            
            return {
                "success": True,
                "urls": all_urls
            }
            
        except Exception as e:
            log.error(f"获取视频截帧 URL 失败: {e}")
            return {
                "success": False,
                "urls": [],
                "error": str(e)
            }
    
    async def get_folder_media_ids(
        self,
        folder_path: str,
        recursive: bool = False,
        include_pending: bool = False,
        include_raw: bool = True,
        media_status: Optional[str] = None,
        page: Optional[int] = None,
        page_size: Optional[int] = None
    ) -> Dict[str, Any]:
        """获取文件夹下所有文件的媒资ID列表 - 基于路径查询
        
        通过文件夹的逻辑路径获取该路径下所有文件的媒资信息。
        路径不需要包含 bucket_name，SDK 会自动添加。
        
        Args:
            folder_path: 文件夹的逻辑路径（不含 bucket_name）
            recursive: 是否递归查询子文件夹，默认 False
            include_pending: 是否包含未完成注册的文件，默认 False
            include_raw: 是否包含完整的原始元数据，默认 True
            media_status: 按媒资状态过滤 (completed/pending/failed)
            page: 页码（从1开始），用于分页
            page_size: 每页大小，用于分页
            
        Returns:
            Dict: 包含文件及媒资信息的字典
        """
        try:
            # 将用户提供的相对路径与 bucket_name 拼接
            full_path = self._build_full_path(folder_path)
            
            # 构建查询参数
            params = {
                "folder_path": full_path,
                "recursive": recursive,
                "include_pending": include_pending,
                "include_raw": include_raw
            }
            
            if media_status:
                params["media_status"] = media_status
            if page:
                params["page"] = page
            if page_size:
                params["page_size"] = page_size
            
            # 调用API
            response = await self.api_client.request(
                "GET",
                "/api/v1/folders/media-ids/by-path",
                params=params
            )
            
            data = response.json()
            
            log.info(
                "获取文件夹 %s 的媒资列表成功: %s 个文件 (recursive=%s)",
                folder_path,
                data.get('stats', {}).get('total_files', 0),
                recursive
            )
            
            return data
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                log.error(
                    "文件夹不存在: folder_path=%s。"
                    "请检查：1) 文件夹路径是否正确 2) 文件夹是否已创建 3) 路径格式是否正确（应以/结尾）",
                    folder_path
                )
            raise
        except Exception as e:
            log.error("获取文件夹媒资列表失败: folder_path=%s, error=%s", folder_path, e)
            log.exception(e)
            raise
    
    async def get_folder_structure(
        self,
        moss_path: str,
        include_bucket: bool = False
    ) -> Dict[str, Any]:
        """获取文件夹的层级结构（包含子文件夹和文件列表）
        
        通过文件夹路径获取该文件夹及其所有子文件夹的树形结构。
        每个文件夹包含直接的文件列表（仅文件名）。
        
        Args:
            moss_path: 相对路径，不包含 bucket_name
            include_bucket: 是否在结构中包含 bucket_name 顶级目录，默认 False
            
        Returns:
            Dict: 包含文件夹结构的字典
        """
        try:
            # 将用户提供的相对路径与 bucket_name 拼接
            full_path = self._build_full_path(moss_path)
            
            # 构建查询参数
            params = {
                "moss_path": full_path,
                "include_bucket": include_bucket
            }
            
            # 调用API
            response = await self.api_client.request(
                "GET",
                "/api/v1/folders/structure/by-path",
                params=params
            )
            
            data = response.json()
            
            log.info(
                "获取文件夹结构成功: %s (总共 %s 个文件夹, %s 个文件)",
                moss_path,
                data.get('total_folders', 0),
                data.get('total_files', 0)
            )
            
            return data
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                error_msg = (
                    f"❌ 文件夹不存在: moss_path={moss_path}\n"
                    f"可能的原因：\n"
                    f"  1. 路径不正确 - 请检查文件夹名称是否拼写正确\n"
                    f"  2. 文件夹未创建 - 该文件夹可能还不存在于系统中\n"
                    f"  3. 路径格式错误 - 路径应该以 / 开头和结尾，如 '/updated_folder_176/'\n"
                )
                log.error(error_msg)
            raise
        except Exception as e:
            log.error("获取文件夹结构失败: moss_path=%s, error=%s", moss_path, e)
            log.exception(e)
            raise


    async def get_folder_contents(
        self,
        folder_id: int,
        page: int = 1,
        page_size: int = 100
    ) -> Dict[str, Any]:
        """获取文件夹内容详情（包含 AI 打标结果）
        
        通过 folder_id 获取该文件夹下所有素材的详细信息，
        包括 AI 内容分析结果（标签、场景、情感等）和时长信息。
        
        Args:
            folder_id: 文件夹 ID
            page: 页码，从 1 开始
            page_size: 每页数量，默认 100
            
        Returns:
            Dict: 包含素材详情的响应
                - items: 素材列表，每项包含：
                    - moss_id: 素材 ID
                    - file_name: 文件名
                    - start_time/end_time: 片段时长（秒）
                    - content_analysis_result: AI 打标结果
                        - main_subject: 主体
                        - action_or_event: 动作/事件
                        - scene_setting: 场景
                        - visual_style: 视觉风格
                        - keywords: 关键词列表
                        - atmosphere_tags: 氛围标签
                        - emotion_dominant: 主导情感
                    - metadata: 元数据（分辨率、帧率等）
                - total: 总数
                - page: 当前页
                - page_size: 每页数量
                
        Examples:
            ```python
            result = await moss.get_folder_contents(folder_id=123)
            for item in result.get("items", []):
                print(f"素材: {item['file_name']}")
                analysis = item.get("content_analysis_result", {})
                print(f"  关键词: {analysis.get('keywords', [])}")
            ```
        """
        try:
            log.info(f"获取文件夹内容详情: folder_id={folder_id}, page={page}")
            
            response = await self.api_client.request(
                "GET",
                f"/api/v1/folders/{folder_id}/contents",
                params={
                    "page": page,
                    "page_size": page_size
                }
            )
            
            data = response.json()
            items = data.get("items", [])
            
            log.info(f"获取文件夹内容成功: folder_id={folder_id}, 共 {len(items)} 个素材")
            
            return data
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                log.error(f"文件夹不存在: folder_id={folder_id}")
            raise
        except Exception as e:
            log.error(f"获取文件夹内容失败: folder_id={folder_id}, error={e}")
            raise

    async def batch_copy_from_oss(
        self,
        source_oss_folder_path: str,
        target_folder_path: str = "/"
    ) -> Dict[str, Any]:
        """批量从外部OSS复制文件到MOSS
        
        如果目标文件夹不存在，会自动创建。
        
        Args:
            source_oss_folder_path: 源OSS文件夹路径，格式如 oss://bucket-name/path/
            target_folder_path: 目标文件夹路径，默认为根目录 "/"
                               如果文件夹不存在会自动创建
            
        Returns:
            Dict: 包含任务ID和状态的响应
        """
        # 通过路径获取 folder_id（自动创建不存在的文件夹）
        target_folder_id = await self._get_folder_id_by_path(target_folder_path)
        
        log.info(f"批量复制任务 - 源: {source_oss_folder_path}, 目标: {target_folder_path} (ID: {target_folder_id})")
        
        response = await self.api_client.request(
            "POST",
            "/api/v1/oss-direct-upload/batch-copy-from-oss",
            json={
                "source_oss_path": source_oss_folder_path,  # 后端接口仍使用 source_oss_path
                "target_folder_id": target_folder_id,
            },
        )
        return response.json()

    async def upload_from_url(
        self,
        url: str,
        folder_path: str = "/",
        tags: Optional[list] = None,
        enable_content_analysis: bool = False,
        frame_level: str = "medium"
    ) -> Dict[str, Any]:
        """通过URL上传文件到MOSS
        
        支持从URL直接下载视频、图片、音频文件并上传到MOSS系统。
        使用异步流式下载，避免内存占用过高。
        如果目标文件夹不存在，会自动创建。
        
        Args:
            url: 要下载的文件URL
            folder_path: 目标文件夹路径，默认为根目录 "/"
                        如果文件夹不存在会自动创建
            tags: 文件标签列表，可选
            enable_content_analysis: 是否启用AI内容分析（仅支持视频文件）
            frame_level: 抽帧等级: low/medium/high
            
        Returns:
            Dict: 包含上传结果，包括：
                - success: 是否成功
                - moss_id: MOSS ID
                - task_id: 任务ID
                - message: 提示信息
                
        Examples:
            通过URL上传文件：
            ```python
            result = await moss.upload_from_url(
                url="https://example.com/video.mp4"
            )
            print(f"上传成功: {result['moss_id']}")
            ```
            
            上传到指定文件夹：
            ```python
            result = await moss.upload_from_url(
                url="https://example.com/image.jpg",
                folder_path="/images/"
            )
            print(f"上传成功: {result['moss_id']}")
            ```
        """
        # 通过路径获取 folder_id（自动创建不存在的文件夹）
        folder_id = await self._get_folder_id_by_path(folder_path)
        
        log.info(f"🚀 开始URL上传任务 - URL: {url}, 目标: {folder_path} (ID: {folder_id})")
        
        # 调用批量复制API的URL模式
        request_data = {
            "url": url,
            "target_folder_id": folder_id,
            "tags": tags or [],
            "enable_content_analysis": enable_content_analysis,
            "frame_level": frame_level
        }
        
        response = await self.api_client.request(
            "POST",
            "/api/v1/oss-direct-upload/batch-copy-from-oss",
            json=request_data
        )
        
        result = response.json()
        
        if result.get("success"):
            log.info(f"✅ URL上传任务已启动 - Task ID: {result['task_id']}")
            return {
                "success": True,
                "task_id": result["task_id"],
                "message": result.get("message", "URL上传任务已启动")
            }
        else:
            log.error(f"❌ URL上传失败: {result.get('message', '未知错误')}")
            return {
                "success": False,
                "message": result.get("message", "URL上传失败")
            }

    async def list_batch_copy_tasks(
        self,
        status_filter: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if status_filter:
            params["status_filter"] = status_filter
        response = await self.api_client.request(
            "GET",
            "/api/v1/oss-direct-upload/batch-copy-tasks",
            params=params,
        )
        return response.json()

    async def create_script_variation_task(
        self,
        script: str,
        title: str,
        variation_count: int = 3,
        level: str = "medium",
        special_requirements: Optional[str] = None
    ) -> Dict[str, Any]:
        """创建脚本裂变任务
        
        Args:
            script: 原始脚本内容
            title: 脚本标题
            variation_count: 裂变数量，默认3
            level: 裂变等级 (low/medium/high)，默认 medium
            special_requirements: 特殊要求（可选）
            
        Returns:
            Dict: 包含任务ID和状态的响应
        """
        payload = {
            "script": script,
            "title": title,
            "variation_count": variation_count,
            "level": level
        }
        if special_requirements:
            payload["special_requirements"] = special_requirements
        
        log.info(f"🎬 创建脚本裂变任务: title={title}, count={variation_count}, level={level}")
        
        response = await self.api_client.request(
            "POST",
            "/api/v1/script-variation/tasks",
            json=payload
        )
        return response.json()

    async def create_copy_variation_task(
        self,
        script: str,
        title: str,
        variation_count: int = 3,
        level: str = "medium",
        special_requirements: Optional[str] = None
    ) -> Dict[str, Any]:
        """创建文案裂变任务
        
        Args:
            script: 原始文案内容
            title: 文案标题
            variation_count: 裂变数量，默认3
            level: 裂变等级 (low/medium/high)，默认 medium
            special_requirements: 特殊要求（可选）
            
        Returns:
            Dict: 包含任务ID和状态的响应
        """
        payload = {
            "script": script,
            "title": title,
            "variation_count": variation_count,
            "level": level
        }
        if special_requirements:
            payload["special_requirements"] = special_requirements
        
        log.info(f"📝 创建文案裂变任务: title={title}, count={variation_count}, level={level}")
        
        response = await self.api_client.request(
            "POST",
            "/api/v1/copy-variation/tasks",
            json=payload
        )
        return response.json()

    async def query_variation_tasks(
        self,
        variation_type: str = "script",
        shot_matching_task_id: Optional[str] = None,
        variation_task_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 20
    ) -> Dict[str, Any]:
        """查询裂变任务
        
        Args:
            variation_type: 裂变类型 (script/copy)
            shot_matching_task_id: 镜头组任务ID（可选，提供时返回该镜头组的所有裂变文案）
            variation_task_id: 裂变任务ID（可选，提供时在客户端侧筛选特定任务）
            page: 页码，从1开始
            page_size: 每页数量，默认20，最大100
            
        Returns:
            Dict: 包含任务列表的响应
        """
        params: Dict[str, Any] = {
            "type": variation_type,
            "page": page,
            "page_size": min(page_size, 100)
        }
        if shot_matching_task_id:
            params["shot_matching_task_id"] = shot_matching_task_id
        
        log.info(f"🔍 查询裂变任务: type={variation_type}, page={page}")
        
        response = await self.api_client.request(
            "GET",
            "/api/v1/script-variation/tasks",
            params=params
        )
        result = response.json()
        
        # 客户端侧筛选：如果提供了 variation_task_id，筛选特定任务
        if variation_task_id and result.get("tasks"):
            filtered_tasks = [t for t in result["tasks"] if t.get("task_id") == variation_task_id]
            result["tasks"] = filtered_tasks
            result["total"] = len(filtered_tasks)
        
        return result

    async def get_direct_download_url(
        self,
        oss_path: str,
        bucket_name: Optional[str] = None,
        expire_seconds: int = 300
    ) -> Dict[str, Any]:
        """获取OSS文件的直接下载URL
        
        通过MOSS API获取OSS文件的预签名下载URL。
        
        Args:
            oss_path: OSS文件路径（不带开头斜杠），例如 "MUSE/Dev/20251218/xlsx/"
            bucket_name: OSS Bucket名称，默认使用配置中的 bucket_name
            expire_seconds: URL过期时间（秒），默认300，范围60-86400
            
        Returns:
            Dict: 包含下载URL的响应
                - success: 是否成功
                - url: 预签名下载URL
                - bucket_name: Bucket名称
                - oss_path: 文件路径
                - expires_at: URL过期时间
                - message: 提示信息
                
        Examples:
            获取Excel文件下载URL：
            ```python
            result = await moss.get_direct_download_url(
                oss_path="MUSE/Dev/20251218/xlsx/video-info.xlsx"
            )
            if result.get("success"):
                print(f"下载URL: {result['url']}")
            ```
        """
        try:
            # 使用传入的bucket_name或配置中的bucket_name
            target_bucket = bucket_name or self.config.bucket_name
            
            # 确保oss_path不以斜杠开头
            if oss_path.startswith("/"):
                oss_path = oss_path[1:]
            
            # 验证expire_seconds范围
            expire_seconds = max(60, min(86400, expire_seconds))
            
            log.info(f"📥 获取直接下载URL: bucket={target_bucket}, path={oss_path}")
            log.info(f"📥 请求URL: {self.api_client.config.base_url}/api/v1/oss/direct-url")
            
            request_body = {
                "bucket_name": target_bucket,
                "oss_path": oss_path,
                "expire_seconds": expire_seconds
            }
            log.info(f"📥 请求体: {request_body}")
            
            response = await self.api_client.request(
                "POST",
                "/api/v1/oss/direct-url",
                json=request_body
            )
            
            data = response.json()
            
            is_folder = data.get("is_folder", False)
            file_count = data.get("file_count", 1)
            
            log.info(f"✅ 获取下载URL成功: {oss_path}, is_folder={is_folder}, file_count={file_count}")
            
            return {
                "success": True,
                "url": data.get("url"),
                "bucket_name": data.get("bucket_name"),
                "oss_path": data.get("oss_path"),
                "is_folder": is_folder,
                "files": data.get("files"),
                "file_count": file_count,
                "expires_at": data.get("expires_at"),
                "message": data.get("message", "获取下载URL成功")
            }
            
        except httpx.HTTPStatusError as e:
            error_detail = "未知错误"
            try:
                error_data = e.response.json()
                error_detail = error_data.get("detail", str(e))
            except:
                error_detail = e.response.text or str(e)
            
            # 将常见的英文错误信息翻译为中文
            if "not found" in error_detail.lower() or "Not Found" in error_detail:
                error_detail = f"文件未找到: {oss_path}"
            elif "unauthorized" in error_detail.lower():
                error_detail = "认证失败，请检查 Access Key 配置"
            elif "forbidden" in error_detail.lower():
                error_detail = "权限不足，无法访问该文件"
            elif "File not found" in error_detail:
                error_detail = f"文件未找到: {oss_path}"
            
            log.error(f"❌ 获取下载URL失败: {error_detail}")
            return {
                "success": False,
                "message": error_detail
            }
        except Exception as e:
            log.error(f"❌ 获取下载URL失败: {e}")
            return {
                "success": False,
                "message": str(e)
            }

    async def get_download_url_by_moss_id(
        self,
        moss_id: str,
        expire_seconds: int = 3600
    ) -> Dict[str, Any]:
        """通过 MOSS ID 获取文件下载 URL
        
        使用 MOSS API 直接通过 moss_id 获取预签名下载 URL，无需先获取 oss_path。
        
        Args:
            moss_id: MOSS 文件 ID
            expire_seconds: URL 过期时间（秒），默认 3600，范围 60-86400
            
        Returns:
            Dict: 包含下载 URL 的响应
                - success: 是否成功
                - url: 预签名下载 URL
                - expires_at: URL 过期时间
                - message: 提示信息
                
        Examples:
            ```python
            result = await moss.get_download_url_by_moss_id(
                moss_id="15ba56b0-47f0-4376-af92-514acdc2d0c7"
            )
            if result.get("success"):
                print(f"下载URL: {result['url']}")
            ```
        """
        try:
            # 验证 expire_seconds 范围
            expire_seconds = max(60, min(86400, expire_seconds))
            
            log.info(f"📥 通过 MOSS ID 获取下载 URL: moss_id={moss_id}")
            
            response = await self.api_client.request(
                "GET",
                f"/api/v1/oss/url/{moss_id}",
                params={"expire_seconds": expire_seconds}
            )
            
            data = response.json()
            
            log.info(f"✅ 获取下载 URL 成功: moss_id={moss_id}")
            
            return {
                "success": True,
                "url": data.get("url"),
                "expires_at": data.get("expires_at"),
                "message": "获取下载 URL 成功"
            }
            
        except httpx.HTTPStatusError as e:
            error_detail = "未知错误"
            try:
                error_data = e.response.json()
                error_detail = error_data.get("message") or error_data.get("detail", str(e))
            except:
                error_detail = e.response.text or str(e)
            
            # 翻译常见错误信息
            if "not found" in error_detail.lower():
                error_detail = f"文件未找到: {moss_id}"
            elif "unauthorized" in error_detail.lower():
                error_detail = "认证失败，请检查 Access Key 配置"
            elif "forbidden" in error_detail.lower() or "access denied" in error_detail.lower():
                error_detail = "权限不足，无法访问该文件"
            
            log.error(f"❌ 获取下载 URL 失败: {error_detail}")
            return {
                "success": False,
                "message": error_detail
            }
        except Exception as e:
            log.error(f"❌ 获取下载 URL 失败: {e}")
            return {
                "success": False,
                "message": str(e)
            }



# ===== 同步接口包装器 =====

class MossProUtilsSync:
    """Moss Pro 工具的同步接口 - 使用明文 AKSK 认证"""
    
    def __init__(self, config: MossConfig):
        self.config = config
    
    def upload_file(
        self,
        file_path: str,
        folder_path: str = "/",
        tags: Optional[list] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        enable_content_analysis: bool = False,
        frame_level: str = "medium"
    ) -> Dict[str, Any]:
        """同步上传文件到 MOSS
        
        如果目标文件夹不存在，会自动创建。
        
        Args:
            file_path: 本地文件路径
            folder_path: 目标文件夹路径，默认为根目录
                        如果文件夹不存在会自动创建
            tags: 文件标签列表，可选
            progress_callback: 进度回调函数
            enable_content_analysis: 是否启用AI内容分析
            frame_level: 抽帧等级
            
        Returns:
            Dict: 包含上传结果
        """
        async def _upload():
            moss_pro = MossProUtils(self.config)
            async with moss_pro as client:
                return await client.upload_file(
                    file_path, folder_path, tags, progress_callback,
                    enable_content_analysis, frame_level
                )
        
        return asyncio.run(_upload())
    
    def get_folder_media_ids(
        self,
        folder_path: str,
        recursive: bool = False,
        include_pending: bool = False,
        include_raw: bool = True,
        media_status: Optional[str] = None,
        page: Optional[int] = None,
        page_size: Optional[int] = None
    ) -> Dict[str, Any]:
        """同步获取文件夹媒资ID列表"""
        async def _get_media_ids():
            moss_pro = MossProUtils(self.config)
            async with moss_pro as client:
                return await client.get_folder_media_ids(
                    folder_path, recursive, include_pending, include_raw,
                    media_status, page, page_size
                )
        
        return asyncio.run(_get_media_ids())
    
    def upload_from_url(
        self,
        url: str,
        folder_path: str = "/",
        tags: Optional[list] = None,
        enable_content_analysis: bool = False,
        frame_level: str = "medium"
    ) -> Dict[str, Any]:
        """同步通过URL上传文件到MOSS
        
        Args:
            url: 要下载的文件URL
            folder_path: 目标文件夹路径，默认为根目录 "/"
                        如果文件夹不存在会自动创建
            tags: 文件标签列表，可选
            enable_content_analysis: 是否启用AI内容分析
            frame_level: 抽帧等级
            
        Returns:
            Dict: 包含上传结果
        """
        async def _upload():
            moss_pro = MossProUtils(self.config)
            async with moss_pro as client:
                return await client.upload_from_url(
                    url, folder_path, tags,
                    enable_content_analysis, frame_level
                )
        
        return asyncio.run(_upload())

    def get_folder_structure(
        self,
        moss_path: str,
        include_bucket: bool = False
    ) -> Dict[str, Any]:
        """同步获取文件夹层级结构"""
        async def _get_structure():
            moss_pro = MossProUtils(self.config)
            async with moss_pro as client:
                return await client.get_folder_structure(moss_path, include_bucket)
        
        return asyncio.run(_get_structure())

    def get_folder_contents(
        self,
        folder_id: int,
        page: int = 1,
        page_size: int = 100
    ) -> Dict[str, Any]:
        """同步获取文件夹内容详情（包含 AI 打标结果）
        
        Args:
            folder_id: 文件夹 ID
            page: 页码，从 1 开始
            page_size: 每页数量
            
        Returns:
            Dict: 包含素材详情的响应
        """
        async def _get_contents():
            moss_pro = MossProUtils(self.config)
            async with moss_pro as client:
                return await client.get_folder_contents(folder_id, page, page_size)
        
        return asyncio.run(_get_contents())

    def batch_copy_from_oss(
        self,
        source_oss_folder_path: str,
        target_folder_path: str = "/"
    ) -> Dict[str, Any]:
        """同步批量从外部OSS复制文件
        
        Args:
            source_oss_folder_path: 源OSS文件夹路径
            target_folder_path: 目标文件夹路径，默认为根目录 "/"
                               如果文件夹不存在会自动创建
        """
        async def _start():
            moss_pro = MossProUtils(self.config)
            async with moss_pro as client:
                return await client.batch_copy_from_oss(
                    source_oss_folder_path=source_oss_folder_path,
                    target_folder_path=target_folder_path
                )
        return asyncio.run(_start())

    def list_batch_copy_tasks(
        self,
        status_filter: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        async def _list():
            moss_pro = MossProUtils(self.config)
            async with moss_pro as client:
                return await client.list_batch_copy_tasks(
                    status_filter=status_filter,
                    limit=limit,
                    offset=offset,
                )
        return asyncio.run(_list())

    def create_script_variation_task(
        self,
        script: str,
        title: str,
        variation_count: int = 3,
        level: str = "medium",
        special_requirements: Optional[str] = None
    ) -> Dict[str, Any]:
        """同步创建脚本裂变任务
        
        Args:
            script: 原始脚本内容
            title: 脚本标题
            variation_count: 裂变数量
            level: 裂变等级 (low/medium/high)
            special_requirements: 特殊要求（可选）
        """
        async def _create():
            moss_pro = MossProUtils(self.config)
            async with moss_pro as client:
                return await client.create_script_variation_task(
                    script=script,
                    title=title,
                    variation_count=variation_count,
                    level=level,
                    special_requirements=special_requirements
                )
        return asyncio.run(_create())

    def create_copy_variation_task(
        self,
        script: str,
        title: str,
        variation_count: int = 3,
        level: str = "medium",
        special_requirements: Optional[str] = None
    ) -> Dict[str, Any]:
        """同步创建文案裂变任务
        
        Args:
            script: 原始文案内容
            title: 文案标题
            variation_count: 裂变数量
            level: 裂变等级 (low/medium/high)
            special_requirements: 特殊要求（可选）
        """
        async def _create():
            moss_pro = MossProUtils(self.config)
            async with moss_pro as client:
                return await client.create_copy_variation_task(
                    script=script,
                    title=title,
                    variation_count=variation_count,
                    level=level,
                    special_requirements=special_requirements
                )
        return asyncio.run(_create())

    def query_variation_tasks(
        self,
        variation_type: str = "script",
        shot_matching_task_id: Optional[str] = None,
        variation_task_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 20
    ) -> Dict[str, Any]:
        """同步查询裂变任务
        
        Args:
            variation_type: 裂变类型 (script/copy)
            shot_matching_task_id: 镜头组任务ID（可选，用于查询与镜头组关联的裂变任务）
            variation_task_id: 裂变任务ID（可选，用于在客户端侧筛选特定任务）
            page: 页码
            page_size: 每页数量
        """
        async def _query():
            moss_pro = MossProUtils(self.config)
            async with moss_pro as client:
                return await client.query_variation_tasks(
                    variation_type=variation_type,
                    shot_matching_task_id=shot_matching_task_id,
                    variation_task_id=variation_task_id,
                    page=page,
                    page_size=page_size
                )
        return asyncio.run(_query())

    def get_direct_download_url(
        self,
        oss_path: str,
        bucket_name: Optional[str] = None,
        expire_seconds: int = 3600
    ) -> Dict[str, Any]:
        """同步获取OSS文件的直接下载URL
        
        Args:
            oss_path: OSS文件路径（不带开头斜杠）
            bucket_name: OSS Bucket名称，默认使用配置中的bucket_name
            expire_seconds: URL过期时间（秒），默认3600
            
        Returns:
            Dict: 包含下载URL的响应
        """
        async def _get_url():
            moss_pro = MossProUtils(self.config)
            async with moss_pro as client:
                return await client.get_direct_download_url(
                    oss_path=oss_path,
                    bucket_name=bucket_name,
                    expire_seconds=expire_seconds
                )
        return asyncio.run(_get_url())


# ===== 便捷的工厂函数 =====

def create_moss_pro_utils(
    base_url: Optional[str] = None,
    access_key_id: Optional[str] = None,
    access_key_secret: Optional[str] = None,
    bucket_name: Optional[str] = None,
    **kwargs
) -> MossProUtilsSync:
    """创建 Moss Pro 工具实例 - 使用明文 AKSK 认证
    
    Args:
        base_url: Moss API 服务地址，默认从环境变量 MOSS_BASE_URL 读取
        access_key_id: 访问密钥 ID，默认从环境变量 MOSS_ACCESS_KEY_ID 读取
        access_key_secret: 访问密钥 Secret（明文），默认从环境变量 MOSS_ACCESS_KEY_SECRET 读取
        bucket_name: Bucket 名称（企业标识），默认从环境变量 MOSS_BUCKET_NAME 读取
        **kwargs: 其他配置参数
        
    Returns:
        MossProUtilsSync: Moss Pro 工具同步接口实例
        
    Examples:
        基本使用：
        ```python
        moss = create_moss_pro_utils(
            base_url="http://localhost:8000",
            access_key_id="YOUR_ACCESS_KEY_ID",
            access_key_secret="YOUR_ACCESS_KEY_SECRET",
            bucket_name="Dev"
        )
        
        # 上传文件
        result = moss.upload_file(
            file_path="/path/to/video.mp4",
            folder_path="/"
        )
        print(f"上传成功: {result['moss_id']}")
        
        # 查询文件夹媒资
        result = moss.get_folder_media_ids(
            folder_path="/",
            recursive=False
        )
        ```
    """
    config = MossConfig(
        base_url=base_url,
        access_key_id=access_key_id,
        access_key_secret=access_key_secret,
        bucket_name=bucket_name,
        **kwargs
    )
    return MossProUtilsSync(config)


if __name__ == "__main__":
    # ===== 使用示例 =====
    import asyncio
    
    # 异步使用示例
    async def example_usage():
        """异步API使用示例 - 文件上传 + 媒资查询"""
        config = MossConfig(
            base_url="http://localhost:8001",
            access_key_id="YOUR_ACCESS_KEY_ID",
            access_key_secret="YOUR_ACCESS_KEY_SECRET",
            bucket_name="YOUR_BUCKET_NAME"
        )
        
        async with MossProUtils(config) as moss:
            # 示例1: 上传文件（带进度显示）
            log.info("=== 示例1: 上传文件 ===")
            
            def on_progress(uploaded, total):
                percent = (uploaded / total) * 100
                log.info(f"上传进度: {percent:.1f}% ({uploaded / 1024 / 1024:.2f} MB / {total / 1024 / 1024:.2f} MB)")
            
            try:
                result = await moss.upload_file(
                    file_path="/path/to/your/file.mp4",
                    folder_path="/",
                    tags=["test", "video"],
                    progress_callback=on_progress
                )
                
                if result.get("success"):
                    log.info(f"✅ 上传成功 - MOSS ID: {result['moss_id']}")
                else:
                    log.debug(f"📦 MOSS 复用已有文件: {result.get('message')}")
            except Exception as e:
                log.error(f"❌ 上传失败: {e}")
            
            # 示例2: 获取文件夹媒资列表
            log.info("=== 示例2: 获取文件夹媒资列表 ===")
            try:
                result = await moss.get_folder_media_ids(
                    folder_path="/",
                    recursive=False
                )
                log.info(f"总文件数: {result['stats']['total_files']}")
            except Exception as e:
                log.error(f"查询失败: {e}")
    
    # 同步使用示例
    def sync_example():
        """同步API使用示例 - 文件上传"""
        moss = create_moss_pro_utils(
            base_url="http://localhost:8000",
            access_key_id="YOUR_ACCESS_KEY_ID",
            access_key_secret="YOUR_ACCESS_KEY_SECRET",
            bucket_name="YOUR_BUCKET_NAME"
        )
        
        log.info("=== 同步API示例: 上传文件 ===")
        
        def on_progress(uploaded, total):
            percent = (uploaded / total) * 100
            print(f"\r上传进度: {percent:.1f}%", end="", flush=True)
        
        try:
            result = moss.upload_file(
                file_path="/path/to/your/file.mp4",
                folder_path="/",
                progress_callback=on_progress
            )
            
            if result.get("success"):
                print(f"\n✅ 上传成功 - MOSS ID: {result['moss_id']}")
            else:
                print(f"\n⚠️ {result.get('message')}")
        except Exception as e:
            print(f"\n❌ 上传失败: {e}")
    
    log.info("=" * 60)
    log.info("MOSS Pro SDK - 文件上传和媒资管理工具")
    log.info("特性: 文件上传 | OSS直传 | 分片上传 | 媒资查询 | AKSK认证")
    log.info("运行示例: asyncio.run(example_usage()) 或 sync_example()")
    log.info("=" * 60)

