"""
Google Cloud Storage 上传器 - 负责图片上传和 URL 管理

使用方法：
1. 安装依赖: pip install google-cloud-storage
2. 配置认证: 设置 GOOGLE_APPLICATION_CREDENTIALS 环境变量指向服务账号 JSON 文件
   或者在 config.json 中配置 credentials_path
"""

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .exceptions import MOSSError  # 复用异常类
from .models import UploadResult

logger = logging.getLogger(__name__)


class GCSUploader:
    """Google Cloud Storage 上传器"""
    
    def __init__(
        self,
        bucket_name: str,
        folder_path: str = "ImageUpload",
        credentials_path: Optional[str] = None,
        project_id: Optional[str] = None,
        make_public: bool = True,
        url_expiration_hours: int = 24,
    ):
        """
        初始化上传器
        
        Args:
            bucket_name: GCS 存储桶名称
            folder_path: 上传文件夹路径（在 bucket 内）
            credentials_path: 服务账号 JSON 文件路径（可选，默认使用环境变量）
            project_id: GCP 项目 ID（可选）
            make_public: 是否将上传的文件设为公开访问（需要 bucket 未启用统一访问控制）
            url_expiration_hours: 签名 URL 过期时间（小时）
        """
        self.bucket_name = bucket_name
        self.folder_path = folder_path.strip("/")
        self.credentials_path = credentials_path
        self.project_id = project_id
        self.make_public = make_public
        self.url_expiration_hours = url_expiration_hours
        
        # URL 缓存: path -> (url, blob_name)
        self._cache: Dict[str, Tuple[str, str]] = {}
        
        # GCS 客户端（延迟初始化）
        self._client = None
        self._bucket = None
    
    def _get_client(self):
        """获取 GCS 客户端"""
        if self._client is None:
            try:
                from google.cloud import storage
                
                if self.credentials_path:
                    self._client = storage.Client.from_service_account_json(
                        self.credentials_path
                    )
                elif self.project_id:
                    # 使用默认凭证 + 指定项目 ID
                    self._client = storage.Client(project=self.project_id)
                else:
                    # 使用默认凭证（GOOGLE_APPLICATION_CREDENTIALS 环境变量）
                    self._client = storage.Client()
                
                self._bucket = self._client.bucket(self.bucket_name)
                
            except ImportError:
                raise MOSSError(
                    "无法导入 google-cloud-storage 模块，请运行: pip install google-cloud-storage"
                )
            except Exception as e:
                raise MOSSError(f"初始化 GCS 客户端失败: {e}")
        
        return self._client, self._bucket
    
    def _convert_heic_to_jpg(self, heic_path: Path, output_dir: Path) -> Path:
        """
        将 HEIC/HEIF 转换为 JPG
        
        Args:
            heic_path: HEIC 文件路径
            output_dir: 输出目录
            
        Returns:
            转换后的 JPG 文件路径
        """
        out_name = heic_path.stem + ".jpg"
        out_path = output_dir / out_name
        
        try:
            # 尝试使用 sips（macOS）
            subprocess.run(
                ["sips", "-s", "format", "jpeg", str(heic_path), "--out", str(out_path)],
                check=True,
                capture_output=True,
            )
            return out_path
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
        
        try:
            # 尝试使用 ImageMagick
            subprocess.run(
                ["convert", str(heic_path), str(out_path)],
                check=True,
                capture_output=True,
            )
            return out_path
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
        
        try:
            # 尝试使用 pillow-heif
            from PIL import Image
            import pillow_heif
            
            pillow_heif.register_heif_opener()
            img = Image.open(heic_path)
            img.save(out_path, "JPEG", quality=95)
            return out_path
        except ImportError:
            raise MOSSError(f"无法转换 HEIC 文件，请安装 pillow-heif: {heic_path}")
        except Exception as e:
            raise MOSSError(f"HEIC 转换失败: {heic_path}, {e}")
    
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
        获取缓存的 URL
        
        Args:
            path: 图片路径
            
        Returns:
            缓存的 URL，如果没有返回 None
        """
        key = str(path.resolve())
        if key in self._cache:
            return self._cache[key][0]
        return None
    
    def clear_cache(self):
        """清除 URL 缓存"""
        self._cache.clear()
    
    def upload_image(self, path: Path, folder: Optional[str] = None) -> UploadResult:
        """
        上传单张图片
        
        Args:
            path: 图片路径
            folder: 自定义文件夹路径（可选，默认使用初始化时的 folder_path）
            
        Returns:
            UploadResult: 包含 url 和 blob_name
        """
        # 检查内存缓存
        cache_key = str(path.resolve())
        if cache_key in self._cache:
            url, blob_name = self._cache[cache_key]
            logger.debug(f"使用缓存 URL: {path.name}")
            return UploadResult(path=path, url=url, moss_id=blob_name)
        
        # 准备图片（转换 HEIC 等）
        temp_dir = Path(tempfile.mkdtemp(prefix="gcs_upload_"))
        try:
            upload_path = self._prepare_image(path, temp_dir)
            
            # 获取客户端
            _, bucket = self._get_client()
            
            # 构建 blob 路径
            target_folder = folder.strip("/") if folder else self.folder_path
            blob_name = f"{target_folder}/{upload_path.name}"
            
            # 检查文件是否已存在于 bucket 中
            blob = bucket.blob(blob_name)
            if blob.exists():
                # 文件已存在，直接返回公开 URL
                url = blob.public_url
                self._cache[cache_key] = (url, blob_name)
                logger.info(f"文件已存在，跳过上传: {path.name}")
                return UploadResult(path=path, url=url, moss_id=blob_name)
            
            # 上传文件
            blob.upload_from_filename(str(upload_path))
            
            # 获取 URL
            # 尝试设为公开访问，如果失败则直接使用 public_url
            # （bucket 已设置为公开访问时，不需要单独设置每个对象）
            try:
                if self.make_public:
                    blob.make_public()
            except Exception as e:
                # bucket 启用了统一访问控制，无法设置单个对象的 ACL
                # 但如果 bucket 已设置公开访问，public_url 仍然可用
                logger.debug(f"无法设置单个对象公开访问（bucket 可能已公开）: {e}")
            
            # 使用公开 URL
            url = blob.public_url
            
            # 缓存结果
            self._cache[cache_key] = (url, blob_name)
            
            logger.info(f"上传成功: {path.name} -> {blob_name}")
            return UploadResult(path=path, url=url, moss_id=blob_name)
        
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    
    def _generate_signed_url(self, blob) -> str:
        """
        生成签名 URL
        
        Args:
            blob: GCS blob 对象
            
        Returns:
            签名 URL
        """
        from datetime import timedelta
        
        # 生成签名 URL，有效期默认 24 小时
        url = blob.generate_signed_url(
            version="v4",
            expiration=timedelta(hours=self.url_expiration_hours),
            method="GET",
        )
        return url
    
    def upload_batch(
        self,
        paths: List[Path],
        folder: Optional[str] = None,
    ) -> List[UploadResult]:
        """
        批量上传图片
        
        Args:
            paths: 图片路径列表
            folder: 自定义文件夹路径（可选）
            
        Returns:
            上传结果列表
        """
        results = []
        for path in paths:
            result = self.upload_image(path, folder)
            results.append(result)
        return results
    
    def upload_batch_sync(
        self,
        paths: List[Path],
        folder: Optional[str] = None,
    ) -> List[UploadResult]:
        """
        同步批量上传（与 MOSSUploader 接口兼容）
        
        Args:
            paths: 图片路径列表
            folder: 自定义文件夹路径（可选）
            
        Returns:
            上传结果列表
        """
        return self.upload_batch(paths, folder)
    
    def refresh_urls_sync(self, blob_names: List[str]) -> List[str]:
        """
        刷新 URL（GCS 公开 URL 不会过期，直接返回）
        
        与 MOSSUploader 接口兼容
        
        Args:
            blob_names: blob 名称列表
            
        Returns:
            URL 列表
        """
        urls = []
        for blob_name in blob_names:
            url = f"https://storage.googleapis.com/{self.bucket_name}/{blob_name}"
            urls.append(url)
        return urls
    
    def delete_blob(self, blob_name: str) -> bool:
        """
        删除文件
        
        Args:
            blob_name: blob 名称
            
        Returns:
            是否删除成功
        """
        try:
            _, bucket = self._get_client()
            blob = bucket.blob(blob_name)
            blob.delete()
            logger.info(f"删除成功: {blob_name}")
            return True
        except Exception as e:
            logger.error(f"删除失败: {blob_name}, {e}")
            return False
