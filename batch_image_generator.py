#!/usr/bin/env python3
"""
批量图片生成脚本 - 用户拥有最高支配权

支持两种生成模式：
1. 场景生成 (scene_generation): 产品图 + Prompt -> 生成场景图
2. 主体迁移 (subject_transfer): 产品图 + 参考背景图 + Prompt -> 主体迁移到新背景

所有参数均可通过模版配置，用户拥有完全的控制权。
"""

import argparse
import asyncio
import json
import logging
import os
import random
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from jinja2 import Template, Environment, BaseLoader, TemplateError

from MOSS_pro_utils import MossConfig, MossProUtils
from nano_banana_generate import (
    NanoBananaError,
    _file_ext_from_output_format,
    _get_api_key,
    _load_config,
    _normalize_output_format,
    _parse_result_urls,
    create_task,
    download_file,
    wait_for_result,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
log = logging.getLogger("batch_image_generator")


# ============================================================================
# 默认路径配置 (用户可在模版中覆盖)
# ============================================================================
DEFAULT_PATHS = {
    "product_images_base": "/var/www/NanoBanana-MZ/产品图",
    "reference_images_base": "/var/www/NanoBanana-MZ/参考图/化妆品家用场景",
    "scene_prompts_dir": "/var/www/NanoBanana-MZ/Prompt/图片生成/场景生成",
    "transfer_prompts_dir": "/var/www/NanoBanana-MZ/Prompt/图片生成/主体迁移",
}


# ============================================================================
# 工具函数
# ============================================================================
def list_images_in_dir(dir_path: Path) -> List[Path]:
    """列出目录下所有图片文件"""
    if not dir_path.exists() or not dir_path.is_dir():
        return []
    exts = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
    return sorted([
        p for p in dir_path.iterdir()
        if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in exts
    ])


def list_prompts_in_dir(dir_path: Path) -> List[Path]:
    """列出目录下所有prompt文件(.j2, .txt, .md)"""
    if not dir_path.exists() or not dir_path.is_dir():
        return []
    exts = {".j2", ".jinja2", ".txt", ".md"}
    return sorted([
        p for p in dir_path.iterdir()
        if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in exts
    ])


def read_prompt_file(path: Path) -> str:
    """读取prompt文件内容"""
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def render_prompt_template(
    template_str: str,
    context: Dict[str, Any],
) -> str:
    """
    使用Jinja2渲染Prompt模版
    
    支持的变量:
    - {{ product_name }}: 产品名称
    - {{ category }}: 产品类别
    - {{ scene }}: 场景描述
    - {{ style }}: 风格描述
    - {{ group_index }}: 当前组索引
    - {{ image_index }}: 当前图片索引
    - {{ product_count }}: 产品图数量
    - {{ reference_count }}: 参考图数量
    - 以及用户在模版中定义的任何自定义变量
    
    支持的过滤器:
    - {{ text | upper }}: 转大写
    - {{ text | lower }}: 转小写
    - {{ list | join(', ') }}: 列表连接
    - {{ text | default('默认值') }}: 默认值
    
    支持的控制结构:
    - {% if condition %}...{% endif %}
    - {% for item in list %}...{% endfor %}
    """
    try:
        env = Environment(loader=BaseLoader())
        template = env.from_string(template_str)
        return template.render(**context)
    except TemplateError as e:
        log.warning(f"Jinja2模版渲染失败: {e}, 返回原始模版")
        return template_str


def random_range(range_config: Union[int, List[int], Tuple[int, int]]) -> int:
    """
    根据配置返回随机数或固定数
    - int: 返回固定值
    - [min, max]: 返回 [min, max] 范围内的随机整数
    """
    if isinstance(range_config, int):
        return range_config
    if isinstance(range_config, (list, tuple)) and len(range_config) == 2:
        return random.randint(range_config[0], range_config[1])
    return int(range_config)


def select_items(
    items: List[Any],
    count: Union[int, List[int]],
    mode: str = "random",
    specified_indices: Optional[List[int]] = None,
    specified_paths: Optional[List[str]] = None,
) -> List[Any]:
    """
    从列表中选择项目
    
    Args:
        items: 源列表
        count: 选择数量，可以是固定值或[min, max]范围
        mode: 选择模式 - "random"(随机), "sequential"(顺序), "specified"(指定)
        specified_indices: 指定的索引列表 (mode="specified"时使用)
        specified_paths: 指定的路径列表 (mode="specified"时使用，用于图片选择)
    
    Returns:
        选中的项目列表
    """
    if not items:
        return []
    
    n = random_range(count)
    
    if mode == "specified":
        if specified_paths:
            # 按路径匹配
            result = []
            for sp in specified_paths:
                for item in items:
                    if isinstance(item, Path) and (str(item) == sp or item.name == sp):
                        result.append(item)
                        break
            return result[:n] if n > 0 else result
        elif specified_indices:
            return [items[i] for i in specified_indices if 0 <= i < len(items)][:n]
    
    if mode == "sequential":
        return items[:n]
    
    # random mode
    if n >= len(items):
        return list(items)
    return random.sample(items, n)


def safe_slug(s: str) -> str:
    """生成安全的文件夹名"""
    import re
    s = s.strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_\-]", "", s)
    return s or "output"


# ============================================================================
# 模版配置结构
# ============================================================================
"""
模版配置示例 (template.json):

{
    "name": "海洋至尊场景生成",
    "description": "为海洋至尊产品生成多组场景图",
    
    // 生成模式: "scene_generation" 或 "subject_transfer"
    "mode": "scene_generation",
    
    // 组数配置
    "group_count": 50,  // 生成50组
    
    // 每组图片数量配置
    "images_per_group": {
        "count": [4, 6],  // 每组4-6张图片，可以是固定值如 5
        "mode": "random"  // "random" 或 "fixed"
    },
    
    // 产品图配置
    "product_images": {
        // 产品图来源目录 (相对于 product_images_base 或绝对路径)
        "source_dir": "海洋至尊",
        
        // 每组使用的产品图数量
        "count_per_group": [3, 5],  // 每组3-5张产品图
        
        // 选择模式: "random", "sequential", "specified"
        "selection_mode": "random",
        
        // 是否必须包含某张图片
        "must_include": {
            "enabled": false,
            "path": null  // 如果enabled=true，填入图片路径
        },
        
        // 指定图片列表 (selection_mode="specified"时使用)
        "specified_images": []
    },
    
    // Prompt配置 (支持Jinja2模版语法)
    "prompts": {
        // Prompt来源目录 (相对于对应模式的prompts_dir或绝对路径)
        "source_dir": null,  // null表示使用默认目录
        
        // 选择模式: "random", "sequential", "specified"
        "selection_mode": "random",
        
        // 是否每组使用不同的prompt
        "unique_per_group": true,
        
        // 指定prompt列表 (selection_mode="specified"时使用)
        // 可以是文件路径或直接的prompt文本，支持Jinja2变量
        "specified_prompts": [],
        
        // 自定义prompt模版 (如果不使用文件)，支持Jinja2变量
        // 例如: "产品是{{ product_name }}，场景是{{ scene }}"
        "custom_template": null
    },
    
    // Jinja2模版变量 (用户自定义变量，可在prompt中使用)
    "template_variables": {
        "product_name": "海洋至尊精华液",
        "category": "护肤品",
        "brand": "海洋至尊",
        "style": "自然清新",
        "scenes": ["浴室梳妆台", "卧室床头柜", "办公桌"],
        "custom_var": "任意自定义值"
    },
    
    // 参考图配置 (仅 subject_transfer 模式)
    "reference_images": {
        "source_dir": "化妆品家用场景",
        "count_per_group": 1,
        "selection_mode": "random",
        "specified_images": []
    },
    
    // 输出配置
    "output": {
        "base_dir": "./outputs",
        "aspect_ratio": "4:5",
        "resolution": "2K",
        "format": "png"
    },
    
    // API配置 (可选，默认从config.json读取)
    "api": {
        "poll_interval": 2.0,
        "max_wait": 1500.0
    },
    
    // MOSS配置 (可选)
    "moss": {
        "folder": null,  // null表示自动生成
        "expire_seconds": 86400
    },
    
    // 路径覆盖 (可选，覆盖DEFAULT_PATHS)
    "paths": {
        "product_images_base": null,
        "reference_images_base": null,
        "scene_prompts_dir": null,
        "transfer_prompts_dir": null
    }
}

Jinja2模版变量说明:
- 系统内置变量:
  - {{ group_index }}: 当前组索引 (从0开始)
  - {{ group_num }}: 当前组编号 (从1开始)
  - {{ image_index }}: 当前图片索引
  - {{ product_count }}: 本组产品图数量
  - {{ reference_count }}: 本组参考图数量
  - {{ total_groups }}: 总组数
  
- 用户自定义变量 (在template_variables中定义):
  - {{ product_name }}, {{ category }}, {{ scene }} 等
  
- 支持Jinja2控制结构:
  - {% if condition %}...{% endif %}
  - {% for item in list %}...{% endfor %}
  - {{ value | default('默认值') }}
"""


# ============================================================================
# 核心生成器类
# ============================================================================
class BatchImageGenerator:
    """批量图片生成器"""
    
    def __init__(
        self,
        template: Dict[str, Any],
        config_path: Path = Path("config.json"),
        api_key: Optional[str] = None,
    ):
        self.template = template
        self.config = _load_config(config_path)
        
        # API配置
        kieai_cfg = self.config.get("kieai", {})
        self.base_url = str(kieai_cfg.get("base_url") or "https://api.kie.ai/api/v1")
        self.model = str(kieai_cfg.get("model") or "nano-banana-pro")
        self.api_key = _get_api_key(api_key, kieai_cfg.get("api_key"))
        
        # 模版配置
        self.mode = template.get("mode", "scene_generation")
        self.group_count = template.get("group_count", 1)
        
        # 路径配置
        self.paths = {**DEFAULT_PATHS}
        if template.get("paths"):
            for k, v in template["paths"].items():
                if v is not None:
                    self.paths[k] = v
        
        # 输出配置
        output_cfg = template.get("output", {})
        self.output_base_dir = Path(output_cfg.get("base_dir", "./outputs"))
        self.aspect_ratio = output_cfg.get("aspect_ratio", "4:5")
        self.resolution = output_cfg.get("resolution", "2K")
        self.output_format = _normalize_output_format(
            self.model, output_cfg.get("format", "png")
        )
        
        # API轮询配置
        api_cfg = template.get("api", {})
        self.poll_interval = float(api_cfg.get("poll_interval", 2.0))
        self.max_wait = float(api_cfg.get("max_wait", 1500.0))
        
        # MOSS配置
        self.moss_config = self._build_moss_config()
        moss_cfg = template.get("moss", {})
        self.moss_folder = moss_cfg.get("folder")
        self.moss_expire = int(moss_cfg.get("expire_seconds", 86400))
        
        # Jinja2模版变量
        self.template_variables = template.get("template_variables", {})
        
        # 缓存
        self._product_images: Optional[List[Path]] = None
        self._reference_images: Optional[List[Path]] = None
        self._prompts: Optional[List[str]] = None
        self._prompt_files: Optional[List[Path]] = None
        self._uploaded_urls: Dict[str, str] = {}  # path -> url
        self._uploaded_moss_ids: Dict[str, str] = {}  # path -> moss_id
    
    def _build_moss_config(self) -> MossConfig:
        """构建MOSS配置"""
        moss_cfg = self.config.get("moss", {})
        return MossConfig(
            base_url=os.getenv("MOSS_BASE_URL") or moss_cfg.get("base_url"),
            access_key_id=os.getenv("MOSS_ACCESS_KEY_ID") or moss_cfg.get("access_key_id"),
            access_key_secret=os.getenv("MOSS_ACCESS_KEY_SECRET") or moss_cfg.get("access_key_secret"),
            bucket_name=os.getenv("MOSS_BUCKET_NAME") or moss_cfg.get("bucket_name"),
        )
    
    def _get_product_images_dir(self) -> Path:
        """获取产品图目录"""
        cfg = self.template.get("product_images", {})
        source_dir = cfg.get("source_dir", "")
        
        if source_dir and Path(source_dir).is_absolute():
            return Path(source_dir)
        
        base = Path(self.paths["product_images_base"])
        if source_dir:
            return base / source_dir
        return base
    
    def _get_reference_images_dir(self) -> Path:
        """获取参考图目录"""
        cfg = self.template.get("reference_images", {})
        source_dir = cfg.get("source_dir", "")
        
        if source_dir and Path(source_dir).is_absolute():
            return Path(source_dir)
        
        base = Path(self.paths["reference_images_base"])
        if source_dir:
            return base / source_dir
        return base
    
    def _get_prompts_dir(self) -> Path:
        """获取Prompt目录"""
        cfg = self.template.get("prompts", {})
        source_dir = cfg.get("source_dir")
        
        if source_dir and Path(source_dir).is_absolute():
            return Path(source_dir)
        
        if self.mode == "subject_transfer":
            base = Path(self.paths["transfer_prompts_dir"])
        else:
            base = Path(self.paths["scene_prompts_dir"])
        
        if source_dir:
            return base / source_dir
        return base
    
    def _load_product_images(self) -> List[Path]:
        """加载产品图列表"""
        if self._product_images is None:
            self._product_images = list_images_in_dir(self._get_product_images_dir())
        return self._product_images
    
    def _load_reference_images(self) -> List[Path]:
        """加载参考图列表"""
        if self._reference_images is None:
            self._reference_images = list_images_in_dir(self._get_reference_images_dir())
        return self._reference_images
    
    def _load_prompts(self) -> List[str]:
        """加载Prompt列表"""
        if self._prompts is not None:
            return self._prompts
        
        cfg = self.template.get("prompts", {})
        
        # 优先使用自定义模版
        custom_template = cfg.get("custom_template")
        if custom_template:
            self._prompts = [custom_template]
            return self._prompts
        
        # 使用指定的prompt列表
        specified = cfg.get("specified_prompts", [])
        if specified:
            prompts = []
            for item in specified:
                if Path(item).exists():
                    prompts.append(read_prompt_file(Path(item)))
                else:
                    # 直接作为prompt文本
                    prompts.append(str(item))
            self._prompts = prompts
            return self._prompts
        
        # 从目录加载
        prompt_dir = self._get_prompts_dir()
        self._prompt_files = list_prompts_in_dir(prompt_dir)
        self._prompts = [read_prompt_file(p) for p in self._prompt_files]
        
        return self._prompts
    
    def _select_product_images_for_group(self, group_index: int) -> List[Path]:
        """为一组选择产品图"""
        cfg = self.template.get("product_images", {})
        all_images = self._load_product_images()
        
        if not all_images:
            raise NanoBananaError("No product images found")
        
        count = cfg.get("count_per_group", [3, 5])
        mode = cfg.get("selection_mode", "random")
        specified = cfg.get("specified_images", [])
        
        selected = select_items(
            all_images,
            count=count,
            mode=mode,
            specified_paths=specified,
        )
        
        # 处理必须包含的图片
        must_include = cfg.get("must_include", {})
        if must_include.get("enabled") and must_include.get("path"):
            must_path = Path(must_include["path"])
            # 查找匹配的图片
            must_image = None
            for img in all_images:
                if str(img) == str(must_path) or img.name == must_path.name:
                    must_image = img
                    break
            
            if must_image and must_image not in selected:
                # 替换第一张或添加
                if selected:
                    selected[0] = must_image
                else:
                    selected = [must_image]
        
        return selected
    
    def _select_reference_images_for_group(self, group_index: int) -> List[Path]:
        """为一组选择参考图 (仅subject_transfer模式)"""
        if self.mode != "subject_transfer":
            return []
        
        cfg = self.template.get("reference_images", {})
        all_images = self._load_reference_images()
        
        if not all_images:
            log.warning("No reference images found for subject_transfer mode")
            return []
        
        count = cfg.get("count_per_group", 1)
        mode = cfg.get("selection_mode", "random")
        specified = cfg.get("specified_images", [])
        
        return select_items(
            all_images,
            count=count,
            mode=mode,
            specified_paths=specified,
        )
    
    def _select_prompt_for_group(self, group_index: int, used_prompts: set) -> str:
        """为一组选择Prompt"""
        cfg = self.template.get("prompts", {})
        all_prompts = self._load_prompts()
        
        if not all_prompts:
            raise NanoBananaError("No prompts found")
        
        unique_per_group = cfg.get("unique_per_group", True)
        mode = cfg.get("selection_mode", "random")
        
        if mode == "sequential":
            idx = group_index % len(all_prompts)
            return all_prompts[idx]
        
        if mode == "specified" and cfg.get("specified_prompts"):
            idx = group_index % len(all_prompts)
            return all_prompts[idx]
        
        # random mode
        if unique_per_group:
            # 尝试选择未使用过的prompt
            available = [p for p in all_prompts if p not in used_prompts]
            if available:
                return random.choice(available)
        
        return random.choice(all_prompts)
    
    def _get_images_count_for_group(self) -> int:
        """获取一组应生成的图片数量"""
        cfg = self.template.get("images_per_group", {})
        count = cfg.get("count", [4, 6])
        return random_range(count)
    
    def _build_template_context(
        self,
        group_index: int,
        image_index: int = 0,
        product_count: int = 0,
        reference_count: int = 0,
        extra_vars: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        构建Jinja2模版渲染上下文
        
        Args:
            group_index: 当前组索引
            image_index: 当前图片索引
            product_count: 产品图数量
            reference_count: 参考图数量
            extra_vars: 额外变量
        
        Returns:
            模版上下文字典
        """
        # 系统内置变量
        context = {
            "group_index": group_index,
            "group_num": group_index + 1,
            "image_index": image_index,
            "image_num": image_index + 1,
            "product_count": product_count,
            "reference_count": reference_count,
            "total_groups": self.group_count,
            "mode": self.mode,
        }
        
        # 用户自定义变量
        context.update(self.template_variables)
        
        # 额外变量
        if extra_vars:
            context.update(extra_vars)
        
        return context
    
    def _render_prompt(
        self,
        prompt_template: str,
        context: Dict[str, Any],
    ) -> str:
        """渲染Prompt模版"""
        return render_prompt_template(prompt_template, context)

    
    async def _upload_images_to_moss(
        self,
        images: List[Path],
        folder_path: str,
    ) -> Tuple[List[str], List[str]]:
        """上传图片到MOSS并返回URL列表"""
        urls = []
        moss_ids = []
        
        async with MossProUtils(self.moss_config) as moss:
            for img_path in images:
                key = str(img_path.resolve())
                
                # 检查缓存
                if key in self._uploaded_urls:
                    urls.append(self._uploaded_urls[key])
                    moss_ids.append(self._uploaded_moss_ids[key])
                    continue
                
                # 上传
                upload_res = await moss.upload_file(
                    file_path=str(img_path),
                    folder_path=folder_path,
                )
                
                moss_id = upload_res.get("moss_id") or upload_res.get("existing_moss_id")
                if not moss_id:
                    raise NanoBananaError(f"MOSS upload failed: {upload_res}")
                
                # 获取URL
                url_res = await moss.get_download_url_by_moss_id(
                    moss_id=str(moss_id),
                    expire_seconds=self.moss_expire,
                )
                if not url_res.get("success") or not url_res.get("url"):
                    raise NanoBananaError(f"Failed to get MOSS URL: {url_res}")
                
                url = str(url_res["url"])
                
                # 缓存
                self._uploaded_urls[key] = url
                self._uploaded_moss_ids[key] = str(moss_id)
                
                urls.append(url)
                moss_ids.append(str(moss_id))
        
        return urls, moss_ids
    
    async def _refresh_urls(self, moss_ids: List[str]) -> List[str]:
        """刷新MOSS URL (避免过期)"""
        urls = []
        async with MossProUtils(self.moss_config) as moss:
            for mid in moss_ids:
                url_res = await moss.get_download_url_by_moss_id(
                    moss_id=mid,
                    expire_seconds=3600,
                )
                if url_res.get("success") and url_res.get("url"):
                    urls.append(str(url_res["url"]))
                else:
                    raise NanoBananaError(f"Failed to refresh URL for moss_id={mid}")
        return urls
    
    def _generate_one_image(
        self,
        prompt: str,
        image_urls: List[str],
        on_task_id: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """生成单张图片"""
        input_params = {
            "prompt": prompt,
            "image_input": image_urls[:8],  # API限制最多8张
            "aspect_ratio": self.aspect_ratio,
            "resolution": self.resolution,
            "output_format": self.output_format,
        }
        
        task_id = create_task(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            input_params=input_params,
        )
        
        if on_task_id:
            on_task_id(task_id)
        
        info = wait_for_result(
            api_key=self.api_key,
            base_url=self.base_url,
            task_id=task_id,
            poll_interval_seconds=self.poll_interval,
            max_wait_seconds=self.max_wait,
        )
        
        urls = _parse_result_urls(info.get("resultJson"))
        if not urls:
            raise NanoBananaError(f"No result URLs: taskId={task_id}")
        
        return {"taskId": task_id, "info": info, "resultUrls": urls}
    
    def run(self) -> Dict[str, Any]:
        """执行批量生成"""
        # 创建输出目录
        template_name = self.template.get("name", "batch")
        now = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = self.output_base_dir / f"{safe_slug(template_name)}_{now}"
        run_dir.mkdir(parents=True, exist_ok=True)
        
        # MOSS文件夹
        moss_folder = self.moss_folder or f"/batch_gen/{safe_slug(template_name)}_{now}/"
        
        # 记录
        results = {
            "template": self.template,
            "run_dir": str(run_dir),
            "groups": [],
        }
        
        used_prompts: set = set()
        
        for group_idx in range(self.group_count):
            group_num = group_idx + 1
            log.info(f"=== 生成第 {group_num}/{self.group_count} 组 ===")
            
            # 创建组目录
            group_dir = run_dir / f"{group_num:03d}"
            group_dir.mkdir(parents=True, exist_ok=True)
            
            # 选择产品图
            product_images = self._select_product_images_for_group(group_idx)
            log.info(f"选择了 {len(product_images)} 张产品图")
            
            # 选择参考图 (subject_transfer模式)
            reference_images = self._select_reference_images_for_group(group_idx)
            if reference_images:
                log.info(f"选择了 {len(reference_images)} 张参考图")
            
            # 选择Prompt模版
            prompt_template = self._select_prompt_for_group(group_idx, used_prompts)
            used_prompts.add(prompt_template)
            
            # 上传图片到MOSS
            all_images = product_images + reference_images
            try:
                image_urls, moss_ids = asyncio.run(
                    self._upload_images_to_moss(all_images, moss_folder)
                )
            except Exception as e:
                log.error(f"上传图片失败: {e}")
                continue
            
            # 确定生成数量
            num_images = self._get_images_count_for_group()
            log.info(f"本组将生成 {num_images} 张图片")
            
            group_result = {
                "group_index": group_idx,
                "group_dir": str(group_dir),
                "product_images": [str(p) for p in product_images],
                "reference_images": [str(p) for p in reference_images],
                "prompt_template": prompt_template,
                "generated_images": [],
            }
            
            # 生成图片
            for img_idx in range(num_images):
                img_num = img_idx + 1
                log.info(f"生成图片 {img_num}/{num_images}...")
                
                # 构建模版上下文并渲染prompt
                context = self._build_template_context(
                    group_index=group_idx,
                    image_index=img_idx,
                    product_count=len(product_images),
                    reference_count=len(reference_images),
                )
                prompt = self._render_prompt(prompt_template, context)
                log.info(f"渲染后Prompt: {prompt[:80]}...")
                
                try:
                    # 刷新URL避免过期
                    if moss_ids:
                        image_urls = asyncio.run(self._refresh_urls(moss_ids))
                    
                    result = self._generate_one_image(
                        prompt=prompt,
                        image_urls=image_urls,
                    )
                    
                    # 下载结果
                    ext = _file_ext_from_output_format(self.output_format)
                    out_path = group_dir / f"{img_num:02d}.{ext}"
                    download_file(result["resultUrls"][0], out_path)
                    
                    group_result["generated_images"].append({
                        "index": img_idx,
                        "path": str(out_path),
                        "task_id": result["taskId"],
                        "rendered_prompt": prompt,
                    })
                    
                    log.info(f"图片 {img_num} 生成成功: {out_path}")
                    
                except Exception as e:
                    log.error(f"生成图片 {img_num} 失败: {e}")
                    group_result["generated_images"].append({
                        "index": img_idx,
                        "error": str(e),
                    })
                
                time.sleep(0.5)  # 避免请求过快
            
            results["groups"].append(group_result)
            
            # 保存中间结果
            with open(run_dir / "results.json", "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
        
        log.info(f"批量生成完成，输出目录: {run_dir}")
        return results


# ============================================================================
# 命令行入口
# ============================================================================
def main() -> int:
    parser = argparse.ArgumentParser(
        description="批量图片生成脚本 - 用户拥有最高支配权"
    )
    parser.add_argument(
        "--template", "-t",
        required=True,
        help="模版配置文件路径 (JSON格式)",
    )
    parser.add_argument(
        "--config", "-c",
        default="config.json",
        help="API配置文件路径",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API Key (可选，覆盖配置文件)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅验证配置，不执行生成",
    )
    
    args = parser.parse_args()
    
    # 加载模版
    template_path = Path(args.template)
    if not template_path.exists():
        log.error(f"模版文件不存在: {template_path}")
        return 1
    
    with open(template_path, "r", encoding="utf-8") as f:
        template = json.load(f)
    
    log.info(f"加载模版: {template.get('name', 'unnamed')}")
    log.info(f"模式: {template.get('mode', 'scene_generation')}")
    log.info(f"组数: {template.get('group_count', 1)}")
    
    if args.dry_run:
        log.info("Dry run 模式，验证配置...")
        generator = BatchImageGenerator(
            template=template,
            config_path=Path(args.config),
            api_key=args.api_key,
        )
        
        # 验证
        product_images = generator._load_product_images()
        log.info(f"找到 {len(product_images)} 张产品图")
        
        if template.get("mode") == "subject_transfer":
            ref_images = generator._load_reference_images()
            log.info(f"找到 {len(ref_images)} 张参考图")
        
        prompts = generator._load_prompts()
        log.info(f"找到 {len(prompts)} 个Prompt")
        
        log.info("配置验证通过")
        return 0
    
    # 执行生成
    generator = BatchImageGenerator(
        template=template,
        config_path=Path(args.config),
        api_key=args.api_key,
    )
    
    results = generator.run()
    
    print(json.dumps({
        "run_dir": results["run_dir"],
        "total_groups": len(results["groups"]),
    }, ensure_ascii=False))
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
