"""
MOSS上传器 - 负责图片上传和URL管理
"""

import asyncio
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .exceptions import MOSSError
from .models import UploadResult

logger = logging.getLogger(__name__)


class MOSSUploader:
    """MOSS存储上传器"""
    
    def __init__(
        self,
        base_url: str,
        access_key_id: str,
        access_key_secret: str,
        bucket_name: str,
        expire_seconds: int = 86400,
    ):
        """
        初始化上传器
        
        Args:
            base_url: MOSS服务基础URL
            access_key_id: 访问密钥ID
            access_key_secret: 访问密钥
            bucket_name: 存储桶名称
            expire_seconds: URL过期时间（秒）
        """
        self.base_url = base_url
        self.access_key_id = access_key_id
        self.access_key_secret = access_key_secret
        self.bucket_name = bucket_name
        self.expire_seconds = expire_seconds
        
        # URL缓存: path -> (url, moss_id)
        self._cache: Dict[str, Tuple[str, str]] = {}
        
        # MOSS工具实例（延迟初始化）
        self._moss_utils = None
        self._moss_config = None
    
    def _get_moss_config(self):
        """获取MOSS配置"""
        if self._moss_config is None:
            # 导入MOSS工具
            try:
                from MOSS_pro_utils import MossConfig
                self._moss_config = MossConfig(
                    base_url=self.base_url,
                    access_key_id=self.access_key_id,
                    access_key_secret=self.access_key_secret,
                    bucket_name=self.bucket_name,
                )
            except ImportError:
                raise MOSSError("无法导入MOSS_pro_utils模块")
        return self._moss_config
    
    def _convert_heic_to_jpg(self, heic_path: Path, output_dir: Path) -> Path:
        """
        将HEIC/HEIF转换为JPG
        
        Args:
            heic_path: HEIC文件路径
            output_dir: 输出目录
            
        Returns:
            转换后的JPG文件路径
        """
        out_name = heic_path.stem + ".jpg"
        out_path = output_dir / out_name
        
        try:
            # 尝试使用sips（macOS）
            subprocess.run(
                ["sips", "-s", "format", "jpeg", str(heic_path), "--out", str(out_path)],
                check=True,
                capture_output=True,
            )
            return out_path
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
        
        try:
            # 尝试使用ImageMagick
            subprocess.run(
                ["convert", str(heic_path), str(out_path)],
                check=True,
                capture_output=True,
            )
            return out_path
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
        
        try:
            # 尝试使用pillow-heif
            from PIL import Image
            import pillow_heif
            
            pillow_heif.register_heif_opener()
            img = Image.open(heic_path)
            img.save(out_path, "JPEG", quality=95)
            return out_path
        except ImportError:
            raise MOSSError(f"无法转换HEIC文件，请安装pillow-heif: {heic_path}")
        except Exception as e:
            raise MOSSError(f"HEIC转换失败: {heic_path}, {e}")
    
    def _prepare_image(self, path: Path, temp_dir: Path) -> Path:
        """
        准备图片用于上传（如需要则转换格式）
        
        Args:
            path: 原始图片路径
            temp_dir: 临时目录
            
        Returns:
            准备好的图片路径
        """
        if path.suffix.lower() in {".heic", ".heif"}:
            return self._convert_heic_to_jpg(path, temp_dir)
        return path
    
    def get_cached_url(self, path: Path) -> Optional[str]:
        """
        获取缓存的URL
        
        Args:
            path: 图片路径
            
        Returns:
            缓存的URL，如果没有返回None
        """
        key = str(path.resolve())
        if key in self._cache:
            return self._cache[key][0]
        return None
    
    def clear_cache(self):
        """清除URL缓存"""
        self._cache.clear()

    async def upload_image(self, path: Path, folder: str) -> UploadResult:
        """
        上传单张图片
        
        Args:
            path: 图片路径
            folder: MOSS文件夹路径
            
        Returns:
            UploadResult: 包含url和moss_id
        """
        # 检查缓存
        cache_key = str(path.resolve())
        if cache_key in self._cache:
            url, moss_id = self._cache[cache_key]
            logger.debug(f"使用缓存URL: {path.name}")
            return UploadResult(path=path, url=url, moss_id=moss_id)
        
        # 准备图片（转换HEIC等）
        temp_dir = Path(tempfile.mkdtemp(prefix="moss_upload_"))
        try:
            upload_path = self._prepare_image(path, temp_dir)
            
            # 导入MOSS工具
            from MOSS_pro_utils import MossProUtils
            
            config = self._get_moss_config()
            
            async with MossProUtils(config) as moss:
                # 上传文件
                upload_res = await moss.upload_file(
                    file_path=str(upload_path),
                    folder_path=folder,
                )
                
                moss_id = upload_res.get("moss_id") or upload_res.get("existing_moss_id")
                if not moss_id:
                    raise MOSSError(f"上传未返回moss_id: {upload_res}")
                
                # 获取下载URL
                url_res = await moss.get_download_url_by_moss_id(
                    moss_id=str(moss_id),
                    expire_seconds=self.expire_seconds,
                )
                
                if not url_res.get("success") or not url_res.get("url"):
                    raise MOSSError(f"获取下载URL失败: {url_res}")
                
                url = str(url_res["url"])
                
                # 缓存结果
                self._cache[cache_key] = (url, str(moss_id))
                
                logger.info(f"上传成功: {path.name} -> {moss_id}")
                return UploadResult(path=path, url=url, moss_id=str(moss_id))
        
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    
    async def upload_batch(
        self,
        paths: List[Path],
        folder: str,
    ) -> List[UploadResult]:
        """
        批量上传图片
        
        Args:
            paths: 图片路径列表
            folder: MOSS文件夹路径
            
        Returns:
            上传结果列表
        """
        results = []
        for path in paths:
            result = await self.upload_image(path, folder)
            results.append(result)
        return results
    
    async def refresh_url(self, moss_id: str) -> str:
        """
        刷新URL
        
        Args:
            moss_id: MOSS文件ID
            
        Returns:
            新的URL
        """
        from MOSS_pro_utils import MossProUtils
        
        config = self._get_moss_config()
        
        async with MossProUtils(config) as moss:
            url_res = await moss.get_download_url_by_moss_id(
                moss_id=moss_id,
                expire_seconds=self.expire_seconds,
            )
            
            if not url_res.get("success") or not url_res.get("url"):
                raise MOSSError(f"刷新URL失败: moss_id={moss_id}, {url_res}")
            
            return str(url_res["url"])
    
    def upload_batch_sync(self, paths: List[Path], folder: str) -> List[UploadResult]:
        """
        同步批量上传（包装异步方法）
        
        Args:
            paths: 图片路径列表
            folder: MOSS文件夹路径
            
        Returns:
            上传结果列表
        """
        return asyncio.run(self.upload_batch(paths, folder))
    
    def refresh_urls_sync(self, moss_ids: List[str]) -> List[str]:
        """
        同步刷新多个URL
        
        Args:
            moss_ids: MOSS文件ID列表
            
        Returns:
            新的URL列表
        """
        async def _refresh_all():
            urls = []
            for moss_id in moss_ids:
                url = await self.refresh_url(moss_id)
                urls.append(url)
            return urls
        
        return asyncio.run(_refresh_all())
