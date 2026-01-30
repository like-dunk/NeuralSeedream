"""
图片选择器 - 负责图片和Prompt的选择
"""

import json
import logging
import random
import re
from pathlib import Path
from typing import List, Optional, Set, Tuple, TypeVar, Union

from .exceptions import SelectionError
from .models import PromptItem, SelectionMode

logger = logging.getLogger(__name__)


def natural_sort_key(path: Path) -> List:
    """
    自然排序键函数，让数字按数值大小排序
    与 macOS Finder 默认排序方式一致
    例如: 1, 2, 3, 10, 11 而不是 1, 10, 11, 2, 3
    """
    def convert(text):
        return int(text) if text.isdigit() else text.lower()
    
    return [convert(c) for c in re.split(r'(\d+)', path.name)]


def get_finder_sort_order(directory: Path) -> Optional[List[str]]:
    """
    尝试从 .DS_Store 读取 Finder 的自定义排序顺序
    
    Args:
        directory: 目录路径
        
    Returns:
        文件名列表（按 Finder 排序），如果无法读取返回 None
    """
    ds_store_path = directory / ".DS_Store"
    if not ds_store_path.exists():
        return None
    
    try:
        from ds_store import DSStore
        
        with DSStore.open(str(ds_store_path), 'r') as d:
            # 收集所有有 Iloc（图标位置）的文件
            iloc_entries = {}
            for e in d:
                if e.code == b'Iloc' and e.value:
                    iloc_entries[e.filename] = e.value
            
            if len(iloc_entries) > 1:
                # 按 y 坐标排序（从上到下），然后按 x 坐标（从左到右）
                sorted_files = sorted(iloc_entries.items(), key=lambda x: (x[1][1], x[1][0]))
                return [name for name, _ in sorted_files]
    except ImportError:
        logger.debug("ds-store 库未安装，使用自然排序")
    except Exception as e:
        logger.debug(f"读取 .DS_Store 失败: {e}")
    
    return None

T = TypeVar("T")

# 支持的图片格式
SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}

# 支持的Prompt文件格式
SUPPORTED_PROMPT_EXTENSIONS = {".j2", ".txt", ".md"}


class ImageSelector:
    """图片和Prompt选择器"""
    
    def __init__(self):
        """初始化选择器"""
        # 已使用的图片集合（用于不重复选择）
        self._used_images: Set[str] = set()
    
    def reset_used_images(self):
        """重置已使用图片记录"""
        self._used_images.clear()
    
    def validate_specified_images(
        self,
        specified: List[str],
        available_images: List[Path],
    ) -> Tuple[List[Path], List[str]]:
        """
        验证指定的图片列表
        
        Args:
            specified: 用户指定的图片路径/文件名列表
            available_images: 可用图片列表
            
        Returns:
            (有效的图片路径列表, 错误信息列表)
            
        Raises:
            SelectionError: 如果有重复或找不到的图片
        """
        errors = []
        valid_images = []
        seen = set()
        
        for spec in specified:
            # 检查重复
            if spec in seen:
                errors.append(f"指定图片重复: {spec}")
                continue
            seen.add(spec)
            
            # 查找图片
            found = self.find_image_by_path(available_images, spec)
            if not found:
                errors.append(f"找不到指定的图片: {spec}")
            else:
                # 检查是否已经添加过（不同路径指向同一文件）
                if str(found) in [str(v) for v in valid_images]:
                    errors.append(f"指定图片重复（不同路径指向同一文件）: {spec}")
                else:
                    valid_images.append(found)
        
        return valid_images, errors
    
    def select_unique_image(
        self,
        images: List[Path],
        must_include: Optional[Path] = None,
    ) -> Optional[Path]:
        """
        选择一张未使用过的图片（不重复）
        
        Args:
            images: 可用图片列表
            must_include: 必须包含的图片（优先选择）
            
        Returns:
            选中的图片，如果没有可用的返回None
        """
        # 如果有must_include且未使用过，优先返回
        if must_include and str(must_include) not in self._used_images:
            self._used_images.add(str(must_include))
            return must_include
        
        # 过滤出未使用的图片
        available = [img for img in images if str(img) not in self._used_images]
        
        if not available:
            return None
        
        # 随机选择一张
        selected = random.choice(available)
        self._used_images.add(str(selected))
        return selected
    
    def mark_image_used(self, image: Path):
        """标记图片为已使用"""
        self._used_images.add(str(image))
    
    def is_image_used(self, image: Path) -> bool:
        """检查图片是否已使用"""
        return str(image) in self._used_images
    
    def get_remaining_images_count(self, images: List[Path]) -> int:
        """获取剩余可用图片数量"""
        return len([img for img in images if str(img) not in self._used_images])
    
    def get_remaining_images(self, images: List[Path]) -> List[Path]:
        """获取剩余可用图片列表"""
        return [img for img in images if str(img) not in self._used_images]
    
    def list_images(self, directory: Path) -> List[Path]:
        """
        列出目录下所有图片文件（递归遍历子文件夹）
        
        排序优先级：
        1. 尝试从 .DS_Store 读取 Finder 自定义排序
        2. 回退到自然排序（与 Finder 默认排序一致）
        
        Args:
            directory: 目录路径
            
        Returns:
            图片文件路径列表（按 Finder 显示顺序排列）
        """
        if not directory.exists() or not directory.is_dir():
            return []
        
        images = []
        
        def scan_directory(dir_path: Path):
            """递归扫描目录"""
            for p in dir_path.iterdir():
                # 忽略隐藏文件和文件夹
                if p.name.startswith("."):
                    continue
                
                if p.is_dir():
                    # 递归扫描子文件夹
                    scan_directory(p)
                elif p.is_file():
                    # 检查扩展名
                    if p.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                        images.append(p)
        
        scan_directory(directory)
        
        # 尝试从 .DS_Store 获取 Finder 排序
        finder_order = get_finder_sort_order(directory)
        
        if finder_order and len(finder_order) >= len(images):
            # 使用 Finder 自定义排序
            order_map = {name: i for i, name in enumerate(finder_order)}
            images.sort(key=lambda p: order_map.get(p.name, float('inf')))
            logger.debug(f"使用 Finder 自定义排序: {directory}")
        else:
            # 回退到自然排序
            images.sort(key=natural_sort_key)
            logger.debug(f"使用自然排序: {directory}")
        
        return images
    
    def load_prompts_from_json(self, path: Path) -> List[PromptItem]:
        """
        从 JSON 文件加载 Prompt 列表

        Args:
            path: Prompt JSON 文件路径（可以是 .json 文件或包含 prompts.json 的目录）

        Returns:
            PromptItem 对象列表（仅包含 enabled=true 的）

        Raises:
            SelectionError: 如果 JSON 文件不存在或格式错误
        """
        # 支持两种方式：直接指定 JSON 文件，或指定包含 prompts.json 的目录
        if path.is_file():
            json_path = path
        elif path.is_dir():
            json_path = path / "prompts.json"
        else:
            # 路径不存在，尝试判断是文件还是目录
            if str(path).endswith('.json'):
                json_path = path
            else:
                json_path = path / "prompts.json"

        if not json_path.exists():
            raise SelectionError(f"Prompt 配置文件不存在: {json_path}")

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise SelectionError(f"Prompt 配置文件格式错误: {e}")
        except Exception as e:
            raise SelectionError(f"读取 Prompt 配置文件失败: {e}")

        if "prompts" not in data:
            raise SelectionError(f"Prompt 配置文件缺少 'prompts' 字段: {json_path}")

        # 解析并过滤 enabled=true 的 prompts
        prompts = []
        for item in data["prompts"]:
            try:
                prompt = PromptItem(
                    id=item["id"],
                    name=item.get("name") or item["id"],
                    description=item.get("description", ""),
                    enabled=item.get("enabled", True),
                    tags=item.get("tags", []),
                    template=item["template"],
                )
                # 只返回启用的 prompts
                if prompt.enabled:
                    prompts.append(prompt)
            except KeyError as e:
                raise SelectionError(f"Prompt 配置项缺少必需字段 {e}: {item}")

        return prompts

    def find_prompt_by_id(self, prompts: List[PromptItem], prompt_id: str) -> Optional[PromptItem]:
        """
        根据 ID 查找 Prompt

        Args:
            prompts: Prompt 列表
            prompt_id: 目标 Prompt ID

        Returns:
            找到的 Prompt，未找到返回 None
        """
        for prompt in prompts:
            if prompt.id == prompt_id:
                return prompt
        return None

    def list_prompts(self, directory: Path) -> List[Path]:
        """
        列出目录下所有Prompt文件

        Args:
            directory: 目录路径

        Returns:
            Prompt文件路径列表（已排序）
        """
        if not directory.exists() or not directory.is_dir():
            return []

        prompts = []
        for p in sorted(directory.iterdir()):
            if not p.is_file():
                continue
            # 忽略隐藏文件
            if p.name.startswith("."):
                continue
            # 检查扩展名
            if p.suffix.lower() in SUPPORTED_PROMPT_EXTENSIONS:
                prompts.append(p)

        return prompts
    
    def _parse_count(self, count: Union[int, List[int], Tuple[int, int]]) -> int:
        """
        解析数量配置，返回实际数量
        
        Args:
            count: 固定值或 [min, max] 范围
            
        Returns:
            实际选择的数量
        """
        if isinstance(count, int):
            return count
        elif isinstance(count, (list, tuple)) and len(count) == 2:
            min_val, max_val = count
            return random.randint(min_val, max_val)
        else:
            return int(count)
    
    def select_items(
        self,
        items: List[T],
        count: Union[int, List[int], Tuple[int, int]],
        mode: Union[str, SelectionMode],
        specified: Optional[List[str]] = None,
        must_include: Optional[T] = None,
    ) -> List[T]:
        """
        选择项目
        
        Args:
            items: 可选项目列表
            count: 选择数量（固定值或范围）
            mode: 选择模式 (random, sequential, specified)
            specified: 指定的项目列表（用于specified模式）
            must_include: 必须包含的项目
            
        Returns:
            选中的项目列表
        """
        if isinstance(mode, str):
            mode = SelectionMode(mode)
        
        actual_count = self._parse_count(count)
        
        if mode == SelectionMode.SPECIFIED:
            # 指定模式：使用用户指定的列表
            if not specified:
                raise SelectionError("指定模式需要提供specified列表")
            
            # 将指定的字符串转换为实际项目
            result = []
            for spec in specified[:actual_count]:
                for item in items:
                    if hasattr(item, "name") and item.name == spec:
                        result.append(item)
                        break
                    elif str(item) == spec or (hasattr(item, "__fspath__") and str(item).endswith(spec)):
                        result.append(item)
                        break
            
            # 确保包含must_include
            if must_include and must_include not in result:
                if len(result) >= actual_count:
                    result[-1] = must_include
                else:
                    result.append(must_include)
            
            return result
        
        elif mode == SelectionMode.SEQUENTIAL:
            # 顺序模式：按顺序选择
            result = items[:actual_count]
            
            # 确保包含must_include
            if must_include and must_include not in result:
                if len(result) >= actual_count:
                    result[-1] = must_include
                else:
                    result.append(must_include)
            
            return result
        
        else:  # RANDOM
            # 随机模式
            if len(items) == 0:
                return []
            
            if must_include:
                # 先确保包含必须的项目
                available = [item for item in items if item != must_include]
                need_count = min(actual_count - 1, len(available))
                
                if need_count > 0:
                    selected = random.sample(available, need_count)
                else:
                    selected = []
                
                # 将must_include插入随机位置
                insert_pos = random.randint(0, len(selected))
                selected.insert(insert_pos, must_include)
                return selected
            else:
                # 普通随机选择
                select_count = min(actual_count, len(items))
                return random.sample(items, select_count)

    def select_unique_prompt(
        self,
        prompts: List[Path],
        used_prompts: Set[str],
        previous_prompt: Optional[str] = None,
    ) -> Optional[Path]:
        """
        选择未使用过的Prompt，确保与上一组不同
        
        Args:
            prompts: 可用的Prompt列表
            used_prompts: 已使用的Prompt集合
            previous_prompt: 上一组使用的Prompt路径
            
        Returns:
            选中的Prompt路径，如果没有可用的返回None
        """
        if not prompts:
            return None
        
        # 优先选择未使用过的
        unused = [p for p in prompts if str(p) not in used_prompts]
        
        if unused:
            # 如果有上一个prompt，确保不选择相同的
            if previous_prompt:
                different = [p for p in unused if str(p) != previous_prompt]
                if different:
                    return random.choice(different)
            return random.choice(unused)
        
        # 所有prompt都用过了，需要复用
        # 但确保与上一组不同
        if previous_prompt and len(prompts) > 1:
            different = [p for p in prompts if str(p) != previous_prompt]
            if different:
                return random.choice(different)
        
        # 只有一个prompt或没有上一个，随机选择
        return random.choice(prompts)
    
    def select_prompts_for_groups(
        self,
        prompts: List[Path],
        group_count: int,
        unique_per_group: bool = True,
    ) -> List[Path]:
        """
        为所有组预先选择Prompt，确保相邻组不同
        
        Args:
            prompts: 可用的Prompt列表
            group_count: 组数
            unique_per_group: 是否每组使用不同的Prompt
            
        Returns:
            每组对应的Prompt列表
        """
        if not prompts:
            return [None] * group_count
        
        result = []
        used_prompts: Set[str] = set()
        previous_prompt: Optional[str] = None
        
        for i in range(group_count):
            if unique_per_group:
                selected = self.select_unique_prompt(prompts, used_prompts, previous_prompt)
            else:
                # 不要求唯一，但仍确保相邻组不同
                selected = self.select_unique_prompt(prompts, set(), previous_prompt)
            
            if selected:
                result.append(selected)
                used_prompts.add(str(selected))
                previous_prompt = str(selected)
            else:
                result.append(prompts[0] if prompts else None)
        
        return result
    
    def find_image_by_path(self, images: List[Path], target_path: str) -> Optional[Path]:
        """
        根据路径查找图片
        
        Args:
            images: 图片列表
            target_path: 目标路径（可以是完整路径或文件名）
            
        Returns:
            找到的图片路径，未找到返回None
        """
        target = Path(target_path)
        
        for img in images:
            # 完整路径匹配
            if img == target or str(img) == target_path:
                return img
            # 文件名匹配
            if img.name == target.name:
                return img
            # 相对路径匹配
            if str(img).endswith(target_path):
                return img
        
        return None
