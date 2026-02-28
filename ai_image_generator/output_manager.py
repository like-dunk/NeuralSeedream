"""
输出管理器 - 负责输出目录和文件管理
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .models import GenerationLog, GroupResult, RunResult

logger = logging.getLogger(__name__)


def _safe_slug(s: str) -> str:
    """将字符串转换为安全的文件名"""
    s = s.strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_\-]", "", s)
    return s or "output"


class OutputManager:
    """输出管理器"""
    ALL_IMAGES_DIR_NAME = "All_images"
    
    def __init__(self, base_dir: Path, run_name: str):
        """
        初始化输出管理器
        
        Args:
            base_dir: 输出基础目录
            run_name: 运行名称（用于创建子目录）
        """
        self.base_dir = Path(base_dir)
        self.run_name = run_name
        self.run_dir: Optional[Path] = None
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    def create_run_directory(self) -> Path:
        """
        创建运行目录
        
        Returns:
            运行目录路径
        """
        if self.run_dir:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            return self.run_dir

        safe_name = _safe_slug(self.run_name)
        dir_name = f"{safe_name}_{self.timestamp}"
        self.run_dir = self.base_dir / dir_name
        self.run_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"创建运行目录: {self.run_dir}")
        return self.run_dir
    
    def create_group_directory(self, group_num: int) -> Path:
        """
        创建组目录
        
        Args:
            group_num: 组号（从1开始）
            
        Returns:
            组目录路径
        """
        if not self.run_dir:
            self.create_run_directory()
        
        group_dir = self.run_dir / f"{group_num:03d}"
        group_dir.mkdir(parents=True, exist_ok=True)
        
        logger.debug(f"创建组目录: {group_dir}")
        return group_dir
    
    def get_output_path(
        self,
        group_num: int,
        image_num: int,
        extension: str = "png",
    ) -> Path:
        """
        获取输出文件路径
        
        Args:
            group_num: 组号（从1开始）
            image_num: 图片号（从1开始）
            extension: 文件扩展名
            
        Returns:
            输出文件路径
        """
        group_dir = self.create_group_directory(group_num)
        filename = f"{image_num:02d}.{extension.lstrip('.')}"
        return group_dir / filename

    def create_all_images_directory(self) -> Path:
        """
        创建本次运行的图片汇总目录

        Returns:
            汇总目录路径
        """
        run_dir = self.get_run_dir()
        all_images_dir = run_dir / self.ALL_IMAGES_DIR_NAME
        all_images_dir.mkdir(parents=True, exist_ok=True)
        return all_images_dir

    def get_all_images_output_path(
        self,
        group_num: int,
        image_num: int,
        extension: str = "png",
    ) -> Path:
        """
        获取本次运行图片汇总目录中的输出路径

        Args:
            group_num: 组号（从1开始）
            image_num: 图片号（从1开始）
            extension: 文件扩展名

        Returns:
            汇总目录中的输出文件路径
        """
        all_images_dir = self.create_all_images_directory()
        filename = f"g{group_num:03d}_i{image_num:02d}.{extension.lstrip('.')}"
        return all_images_dir / filename
    
    def save_generation_log(self, log: GenerationLog):
        """
        保存生成日志
        
        Args:
            log: 生成日志对象
        """
        if not self.run_dir:
            self.create_run_directory()
        
        log_path = self.run_dir / "generation_log.json"
        
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(log.to_dict(), f, ensure_ascii=False, indent=2)
        
        logger.info(f"保存生成日志: {log_path}")
    
    def save_group_result(self, group_result: GroupResult):
        """
        保存单组结果
        
        Args:
            group_result: 组结果对象
        """
        group_dir = Path(group_result.group_dir)
        result_path = group_dir / "result.json"
        
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(group_result.to_dict(), f, ensure_ascii=False, indent=2)
        
        logger.debug(f"保存组结果: {result_path}")
    
    def load_generation_log(self) -> Optional[GenerationLog]:
        """
        加载生成日志
        
        Returns:
            生成日志对象，如果不存在返回None
        """
        if not self.run_dir:
            return None
        
        log_path = self.run_dir / "generation_log.json"
        if not log_path.exists():
            return None
        
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            return GenerationLog(
                template_name=data.get("template_name", ""),
                mode=data.get("mode", ""),
                started_at=datetime.fromisoformat(data["started_at"]),
                completed_at=datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None,
                groups=data.get("groups", []),
                summary=data.get("summary"),
            )
        except Exception as e:
            logger.warning(f"加载生成日志失败: {e}")
            return None
    
    def get_run_dir(self) -> Path:
        """获取运行目录"""
        if not self.run_dir:
            self.create_run_directory()
        return self.run_dir
    
    def set_run_dir(self, run_dir: Path):
        """设置运行目录（用于断点续传）"""
        self.run_dir = run_dir
