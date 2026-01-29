"""
生成引擎 - 核心协调器
"""

import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .api_client import APIClient
from .config import ConfigManager
from .exceptions import GeneratorError
from .image_selector import ImageSelector
from .models import (
    GenerationLog,
    GenerationMode,
    GroupResult,
    ImageResult,
    PromptItem,
    RunResult,
    TemplateContext,
    TextResult,
)
from .moss_uploader import MOSSUploader
from .output_manager import OutputManager
from .state_manager import StateManager
from .template_engine import TemplateEngine
from .text_generator import TextGenerator

logger = logging.getLogger(__name__)


class RateLimiter:
    """请求速率限制器 - 10秒20个请求"""
    
    def __init__(self, max_requests: int = 20, time_window: float = 10.0):
        """
        初始化速率限制器
        
        Args:
            max_requests: 时间窗口内最大请求数
            time_window: 时间窗口（秒）
        """
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests: List[float] = []
        self._lock = threading.Lock()
    
    def acquire(self):
        """获取请求许可，如果超过限制则等待"""
        while True:
            wait_time = 0
            with self._lock:
                now = time.time()
                # 清理过期的请求记录
                self.requests = [t for t in self.requests if now - t < self.time_window]
                
                if len(self.requests) < self.max_requests:
                    # 有配额，记录并返回
                    self.requests.append(now)
                    return
                else:
                    # 需要等待，计算等待时间
                    oldest = self.requests[0]
                    wait_time = self.time_window - (now - oldest) + 0.1
            
            # 在锁外面等待，让其他线程也能检查
            if wait_time > 0:
                logger.debug(f"速率限制，等待 {wait_time:.1f}秒")
                time.sleep(wait_time)


class GenerationEngine:
    """生成引擎 - 核心协调器"""
    
    def __init__(
        self,
        config_manager: ConfigManager,
        template_engine: TemplateEngine,
        image_selector: ImageSelector,
        moss_uploader: MOSSUploader,
        api_client: APIClient,
        output_manager: OutputManager,
        state_manager: StateManager,
        text_generator: Optional[TextGenerator] = None,
    ):
        """初始化生成引擎"""
        self.config_manager = config_manager
        self.template_engine = template_engine
        self.image_selector = image_selector
        self.moss_uploader = moss_uploader
        self.api_client = api_client
        self.output_manager = output_manager
        self.state_manager = state_manager
        self.text_generator = text_generator
        
        # 缓存配置
        self._template_config = None
        self._global_config = None
        
        # 运行时状态
        self._prompt_assignments: List[PromptItem] = []  # 每组分配的prompt
        self._reference_assignments: List[Path] = []  # 每组分配的参考图（主体迁移模式）
        self._uploaded_urls: Dict[str, str] = {}  # 路径 -> URL映射
        self._uploaded_moss_ids: Dict[str, str] = {}  # 路径 -> moss_id映射
        self._upload_lock = threading.Lock()  # 上传缓存锁
        
        # 速率限制器：10秒10个新请求（仅 KieAI 需要）
        self._rate_limiter = RateLimiter(max_requests=10, time_window=10.0)
        self._use_rate_limiter = True  # 是否启用速率限制
        
        # 全局并发限制：最多100个同时进行的任务（组内+组外总和）
        self._concurrent_semaphore = threading.Semaphore(100)
        
        # 生成日志锁
        self._log_lock = threading.Lock()
    
    def _load_configs(self):
        """加载配置"""
        self._global_config = self.config_manager.load_global_config()
        self._template_config = self.config_manager.load_template_config()
        
        # OpenRouter 不需要速率限制
        if self._global_config.image_service == "openrouter":
            self._use_rate_limiter = False
    
    def _get_moss_folder(self) -> str:
        """获取MOSS上传文件夹路径"""
        name = self._template_config.name if self._template_config else "default"
        return f"/ai_image_generator/{name}/"
    
    def _upload_images(self, paths: List[Path]) -> List[str]:
        """
        上传图片并返回URL列表（线程安全）
        
        Args:
            paths: 图片路径列表
            
        Returns:
            URL列表
        """
        urls = []
        folder = self._get_moss_folder()
        
        for path in paths:
            key = str(path.resolve())
            
            with self._upload_lock:
                # 检查缓存
                if key in self._uploaded_urls:
                    urls.append(self._uploaded_urls[key])
                    continue
                
                # 上传
                results = self.moss_uploader.upload_batch_sync([path], folder)
                if results:
                    result = results[0]
                    self._uploaded_urls[key] = result.url
                    self._uploaded_moss_ids[key] = result.moss_id
                    urls.append(result.url)
        
        return urls
    
    def _refresh_urls(self, paths: List[Path]) -> List[str]:
        """刷新URL（防止过期，线程安全）"""
        with self._upload_lock:
            moss_ids = []
            for path in paths:
                key = str(path.resolve())
                if key in self._uploaded_moss_ids:
                    moss_ids.append(self._uploaded_moss_ids[key])
            
            if moss_ids:
                new_urls = self.moss_uploader.refresh_urls_sync(moss_ids)
                # 更新缓存
                for i, path in enumerate(paths):
                    if i < len(new_urls):
                        key = str(path.resolve())
                        self._uploaded_urls[key] = new_urls[i]
                return new_urls
            
            return []
    
    def _upload_images_no_cache(self, paths: List[Path]) -> List[str]:
        """上传图片（不使用缓存，用于刷新）"""
        return self._upload_images(paths)

    def _allocate_prompts_for_groups(
        self,
        prompts: List["PromptItem"],
        group_count: int,
        mode: str,
    ) -> List["PromptItem"]:
        """
        根据模式为所有组分配 Prompt

        场景生成模式：
        - 每组使用不同的 prompt（不重复随机）
        - 指定的 prompts 优先使用，剩余组继续随机
        - prompt 用完后才会复用

        主体迁移模式：
        - 所有组共用同一个 prompt
        - 默认随机选择一个，也可以指定

        Args:
            prompts: 可用的 PromptItem 列表
            group_count: 组数
            mode: 生成模式

        Returns:
            每组对应的 PromptItem 列表
        """
        template_cfg = self._template_config

        if mode == "scene_generation":
            return self._allocate_scene_prompts(prompts, group_count)
        else:  # subject_transfer
            return self._allocate_transfer_prompts(prompts, group_count)
    
    def _allocate_scene_prompts(self, prompts: List["PromptItem"], group_count: int) -> List["PromptItem"]:
        """
        场景生成模式的 prompt 分配

        规则：
        1. 指定的 prompts 优先分配给前面的组
        2. 剩余组从未使用的 prompts 中随机选择（不重复）
        3. 如果 prompts 用完，则从头开始复用（但确保相邻组不同）
        """
        template_cfg = self._template_config
        result = []
        used_prompts = set()

        # 获取指定的 prompts
        specified = []
        if template_cfg.scene_prompts and template_cfg.scene_prompts.specified_prompts:
            for prompt_id in template_cfg.scene_prompts.specified_prompts:
                found = self.image_selector.find_prompt_by_id(prompts, prompt_id)
                if found:
                    specified.append(found)
                else:
                    logger.warning(f"⚠️ 指定的 prompt 未找到: {prompt_id}")

        # 分配 prompts
        for i in range(group_count):
            previous = result[-1] if result else None

            if i < len(specified):
                # 使用指定的 prompt
                selected = specified[i]
            else:
                # 随机选择未使用的 prompt
                # 将 PromptItem 转换为 Path 对象以兼容现有的 select_unique_prompt 方法
                # 使用 prompt.id 作为唯一标识
                available = [p for p in prompts if p.id not in used_prompts]
                if available:
                    if previous:
                        # 确保与上一组不同
                        different = [p for p in available if p.id != previous.id]
                        selected = random.choice(different) if different else random.choice(available)
                    else:
                        selected = random.choice(available)
                else:
                    # 所有 prompts 都用过了，复用但确保与上一组不同
                    if previous and len(prompts) > 1:
                        different = [p for p in prompts if p.id != previous.id]
                        selected = random.choice(different) if different else prompts[0]
                    else:
                        selected = random.choice(prompts) if prompts else None

            if selected:
                result.append(selected)
                used_prompts.add(selected.id)
            elif prompts:
                # 所有 prompts 都用过了，复用但确保与上一组不同
                available = [p for p in prompts if p.id != previous.id] if previous else prompts
                result.append(random.choice(available) if available else prompts[0])
            else:
                result.append(None)

        return result
    
    def _allocate_transfer_prompts(self, prompts: List["PromptItem"], group_count: int) -> List["PromptItem"]:
        """
        主体迁移模式的 prompt 分配

        规则：
        1. 如果指定了 prompt，所有组都使用该 prompt
        2. 否则随机选择一个，所有组共用
        """
        template_cfg = self._template_config

        selected = None

        # 检查是否指定了 prompt
        if template_cfg.transfer_prompts and template_cfg.transfer_prompts.specified_prompt:
            prompt_id = template_cfg.transfer_prompts.specified_prompt
            selected = self.image_selector.find_prompt_by_id(prompts, prompt_id)
            if not selected:
                logger.warning(f"⚠️ 指定的 prompt 未找到: {prompt_id}，将随机选择")

        # 如果没有指定或未找到，随机选择一个
        if not selected and prompts:
            selected = random.choice(prompts)

        if selected:
            logger.info(f"📝 主体迁移模式：所有组使用 prompt: {selected.name}")

        # 所有组使用同一个 prompt
        return [selected] * group_count
    
    def _allocate_references_for_groups(
        self,
        reference_images: List[Path],
        group_count: int,
        specified_image: Optional[Path],
        specified_coverage: int,
    ) -> List[Path]:
        """
        为所有组预分配参考图（主体迁移模式专用）
        
        规则：
        - 每组使用同一张参考图作为背景
        - 组间不重复选择参考图
        - 如果指定了参考图，根据 coverage 百分比决定多少组使用这张图
        - 未指定或超出 coverage 的组随机选择（组间不重复）
        
        Args:
            reference_images: 所有可用参考图
            group_count: 组数
            specified_image: 用户指定的单张参考图（可选）
            specified_coverage: 指定图片覆盖的组百分比
            
        Returns:
            每组对应的参考图列表
        """
        result = []
        used_refs = set()
        
        # 计算指定图片覆盖的组数
        coverage_groups = int(group_count * specified_coverage / 100) if specified_image else 0
        
        if specified_image and coverage_groups > 0:
            logger.info(f"📷 指定参考图将覆盖前 {coverage_groups}/{group_count} 组 ({specified_coverage}%): {specified_image.name}")
        
        for i in range(group_count):
            previous = result[-1] if result else None
            
            if specified_image and i < coverage_groups:
                # 使用指定的参考图
                selected = specified_image
                logger.debug(f"组{i+1} 使用指定参考图: {selected.name}")
            else:
                # 随机选择未使用的参考图（排除已指定的图，避免重复）
                exclude_set = used_refs.copy()
                if specified_image:
                    exclude_set.add(str(specified_image))
                
                available = [r for r in reference_images if str(r) not in exclude_set]
                if available:
                    if previous and len(available) > 1:
                        # 确保与上一组不同
                        different = [r for r in available if str(r) != str(previous)]
                        selected = random.choice(different) if different else random.choice(available)
                    else:
                        selected = random.choice(available)
                else:
                    # 所有参考图都用过了，复用但确保与上一组不同
                    if previous and len(reference_images) > 1:
                        different = [r for r in reference_images if str(r) != str(previous)]
                        selected = random.choice(different) if different else reference_images[0]
                    else:
                        selected = random.choice(reference_images) if reference_images else None
            
            if selected:
                result.append(selected)
                used_refs.add(str(selected))
            else:
                result.append(None)
        
        return result
    
    def _get_custom_template(self) -> Optional[str]:
        """获取自定义模板内容"""
        template_cfg = self._template_config
        
        if template_cfg.mode == "scene_generation" and template_cfg.scene_prompts:
            return template_cfg.scene_prompts.custom_template
        elif template_cfg.mode == "subject_transfer" and template_cfg.transfer_prompts:
            return template_cfg.transfer_prompts.custom_template
        
        return None
    
    def _remove_ai_tags(self, content: str) -> str:
        """
        移除 AI 生成的标签
        
        AI 生成的文案末尾通常会有 #标签1 #标签2 这样的格式，
        我们需要移除它们，使用用户配置的标签代替。
        
        Args:
            content: 原始文案内容
            
        Returns:
            移除标签后的文案
        """
        import re
        
        # 匹配末尾的标签行（一行或多行以 # 开头的标签）
        # 例如: #海洋至尊 #护肤分享 #补水保湿
        lines = content.rstrip().split('\n')
        
        # 从末尾开始检查，移除纯标签行
        while lines:
            last_line = lines[-1].strip()
            # 检查是否是标签行（以 # 开头，且主要由 #xxx 组成）
            if last_line and last_line.startswith('#'):
                # 检查这一行是否主要是标签
                tags_pattern = r'^[#\w\u4e00-\u9fff\s]+$'
                if re.match(tags_pattern, last_line):
                    lines.pop()
                    continue
            break
        
        return '\n'.join(lines)

    def run(self, dry_run: bool = False, auto_confirm: bool = False) -> RunResult:
        """
        执行完整的生成流程
        
        Args:
            dry_run: 是否为试运行（只验证配置不执行生成）
            auto_confirm: 是否自动确认（跳过用户确认提示）
            
        Returns:
            运行结果
        """
        start_time = time.time()
        self._load_configs()
        
        template_cfg = self._template_config
        paths = self.config_manager.get_all_resolved_paths()
        
        logger.info(f"开始生成: {template_cfg.name}, 模式={template_cfg.mode}, 组数={template_cfg.group_count}")
        
        # 验证配置
        errors = self.config_manager.validate_config()
        if errors:
            for err in errors:
                logger.error(f"配置错误: {err}")
            raise GeneratorError(f"配置验证失败: {errors}")
        
        # 列出可用资源（在dry_run检查之前，用于验证）
        product_images = self.image_selector.list_images(paths["product_images"])
        logger.info(f"找到 {len(product_images)} 张产品图")
        
        reference_images = []
        if "reference_images" in paths:
            reference_images = self.image_selector.list_images(paths["reference_images"])
            logger.info(f"找到 {len(reference_images)} 张参考图")
        
        prompts = []
        if "prompts" in paths:
            prompts = self.image_selector.load_prompts_from_json(paths["prompts"])
            logger.info(f"找到 {len(prompts)} 个可用 Prompt")
        
        # 计算每组需要的图片数量（使用最大值进行检查）
        images_per_group_cfg = template_cfg.images_per_group
        if isinstance(images_per_group_cfg, list) and len(images_per_group_cfg) == 2:
            max_images_per_group = images_per_group_cfg[1]
        else:
            max_images_per_group = int(images_per_group_cfg) if images_per_group_cfg else 1
        
        # 检查资源数量是否足够
        warnings = []
        
        # 检查 Prompt 数量（仅场景生成模式需要多个 prompt，主体迁移模式所有组共用一个）
        if prompts and template_cfg.mode == "scene_generation" and len(prompts) < template_cfg.group_count:
            warnings.append(
                f"Prompt数量不足: 需要 {template_cfg.group_count} 个，但只有 {len(prompts)} 个可用，超出的组将随机复用"
            )
        
        # 检查图片数量
        if template_cfg.mode == "scene_generation":
            if len(product_images) < max_images_per_group:
                warnings.append(
                    f"产品图数量不足: 每组最多需要 {max_images_per_group} 张，但只有 {len(product_images)} 张可用"
                )
        else:  # subject_transfer
            if len(product_images) < max_images_per_group:
                warnings.append(
                    f"产品图数量不足: 每组最多需要 {max_images_per_group} 张，但只有 {len(product_images)} 张可用"
                )
            # 主体迁移模式：参考图是组间不重复，检查参考图数量是否足够组数
            if len(reference_images) < template_cfg.group_count:
                warnings.append(
                    f"参考图数量不足: 需要 {template_cfg.group_count} 张（每组1张），但只有 {len(reference_images)} 张可用，超出的组将复用参考图"
                )
        
        # 如果有警告，提示用户确认
        if warnings:
            for warn in warnings:
                logger.warning(f"⚠️ {warn}")
            
            actual_per_group = min(
                len(product_images),
                len(reference_images) if template_cfg.mode == "subject_transfer" else len(product_images)
            )
            logger.warning(f"⚠️ 实际每组只能生成 {actual_per_group} 张图片")
            
            if not auto_confirm:
                try:
                    user_input = input("\n是否继续执行？(Y/n): ").strip().lower()
                    if user_input == 'n':
                        logger.info("用户取消执行")
                        return RunResult(
                            run_dir=Path("."),
                            total_groups=template_cfg.group_count,
                            completed_groups=0,
                            total_images=0,
                            successful_images=0,
                            failed_images=0,
                            duration_seconds=time.time() - start_time,
                        )
                except EOFError:
                    # 非交互模式下无法获取输入
                    logger.warning("非交互模式，自动继续执行")
        
        # 验证指定图片（在 dry-run 检查之前，这样用户可以看到验证结果）
        specified_product_images = []
        
        prod_cfg = template_cfg.product_images
        if prod_cfg.specified_images:
            # 确保 specified_images 是列表，并过滤掉空字符串
            if isinstance(prod_cfg.specified_images, list):
                spec_list = [s for s in prod_cfg.specified_images if s]  # 过滤空字符串
            elif prod_cfg.specified_images:
                spec_list = [prod_cfg.specified_images]
            else:
                spec_list = []
            
            if spec_list:
                specified_product_images, errors = self.image_selector.validate_specified_images(
                    specified=spec_list,
                    available_images=product_images,
                )
                if errors:
                    for err in errors:
                        logger.error(f"❌ 产品图: {err}")
                    raise GeneratorError(f"指定产品图验证失败: {'; '.join(errors)}")
                logger.info(f"📋 用户指定了 {len(specified_product_images)} 张产品图")
        
        # 主体迁移模式：验证并预分配参考图
        specified_reference_image: Optional[Path] = None
        if template_cfg.mode == "subject_transfer" and template_cfg.reference_images:
            ref_cfg = template_cfg.reference_images
            
            # 参考图 specified_images 是字符串（只能指定一张）
            # 为了兼容，如果用户误传了数组，取第一个并警告
            specified_ref_path = None
            if ref_cfg.specified_images:
                if isinstance(ref_cfg.specified_images, str):
                    specified_ref_path = ref_cfg.specified_images if ref_cfg.specified_images.strip() else None
                elif isinstance(ref_cfg.specified_images, list) and len(ref_cfg.specified_images) > 0:
                    # 兼容处理：取第一个非空元素
                    for item in ref_cfg.specified_images:
                        if item and item.strip():
                            specified_ref_path = item
                            break
                    if specified_ref_path and len([x for x in ref_cfg.specified_images if x and x.strip()]) > 1:
                        logger.warning(f"⚠️ 参考图只支持指定一张，将使用: {specified_ref_path}")
            
            if specified_ref_path:
                # 验证指定的参考图
                found = self.image_selector.find_image_by_path(reference_images, specified_ref_path)
                if found:
                    specified_reference_image = found
                    logger.info(f"📋 用户指定了参考图: {specified_reference_image.name}")
                else:
                    raise GeneratorError(f"指定的参考图不存在: {specified_ref_path}")
        
        # 主体迁移模式：预分配每组的参考图（每组共用一张背景图，组间不重复）
        if template_cfg.mode == "subject_transfer" and reference_images:
            ref_cfg = template_cfg.reference_images
            ref_specified_coverage = getattr(ref_cfg, 'specified_coverage', 100) if ref_cfg else 100
            
            self._reference_assignments = self._allocate_references_for_groups(
                reference_images=reference_images,
                group_count=template_cfg.group_count,
                specified_image=specified_reference_image,
                specified_coverage=ref_specified_coverage,
            )
            
            # 打印分配结果
            logger.info(f"📷 参考图分配完成（每组共用一张背景图）:")
            for i, ref in enumerate(self._reference_assignments[:5]):  # 只显示前5组
                if ref:
                    logger.info(f"   组{i+1}: {ref.name}")
            if len(self._reference_assignments) > 5:
                logger.info(f"   ... 共 {len(self._reference_assignments)} 组")
        
        if dry_run:
            logger.info("试运行模式，配置验证通过")
            return RunResult(
                run_dir=Path("."),
                total_groups=template_cfg.group_count,
                completed_groups=0,
                total_images=0,
                successful_images=0,
                failed_images=0,
                duration_seconds=time.time() - start_time,
            )
        
        # 创建输出目录
        run_dir = self.output_manager.create_run_directory()
        
        # 初始化状态
        self.state_manager.state_dir = run_dir
        self.state_manager.init_state(
            template_config_path=str(self.config_manager.template_path),
            run_dir=run_dir,
        )
        
        # 预分配Prompt（根据模式使用不同策略）
        self._prompt_assignments = self._allocate_prompts_for_groups(
            prompts=prompts,
            group_count=template_cfg.group_count,
            mode=template_cfg.mode,
        )
        
        # 初始化生成日志
        generation_log = GenerationLog(
            template_name=template_cfg.name,
            mode=template_cfg.mode,
            started_at=datetime.now(),
            completed_at=None,
            groups=[],
        )
        
        # 计算指定图片覆盖的组数
        prod_cfg = template_cfg.product_images
        specified_coverage = getattr(prod_cfg, 'specified_coverage', 100)
        coverage_groups = int(template_cfg.group_count * specified_coverage / 100)
        
        if specified_product_images:
            logger.info(f"📋 指定产品图将覆盖前 {coverage_groups}/{template_cfg.group_count} 组 ({specified_coverage}%)")
        
        # 获取最大并发组数
        # 全局信号量已控制总并发数，组间不再需要额外限制
        max_concurrent_groups = template_cfg.output.max_concurrent_groups
        logger.info(f"🚀 最大并发组数: {max_concurrent_groups}")
        
        # 收集待执行的组
        pending_groups = []
        for group_index in range(template_cfg.group_count):
            if self.state_manager.is_group_complete(group_index):
                logger.info(f"⏭️ 跳过已完成的组 {group_index + 1}")
                continue
            
            use_specified_products = group_index < coverage_groups
            pending_groups.append({
                "group_index": group_index,
                "specified_product_images": specified_product_images if use_specified_products else [],
            })
        
        if not pending_groups:
            logger.info("所有组已完成")
            return RunResult(
                run_dir=run_dir,
                total_groups=template_cfg.group_count,
                completed_groups=template_cfg.group_count,
                total_images=0,
                successful_images=0,
                failed_images=0,
                duration_seconds=time.time() - start_time,
            )
        
        logger.info(f"📋 待执行组数: {len(pending_groups)}")
        
        # 统计结果（线程安全）
        results_lock = threading.Lock()
        total_images = 0
        successful_images = 0
        failed_images = 0
        
        def execute_group(group_info: Dict) -> Optional[GroupResult]:
            """执行单个组（在线程中运行）"""
            nonlocal total_images, successful_images, failed_images
            
            group_index = group_info["group_index"]
            group_num = group_index + 1
            
            try:
                group_result = self.run_group(
                    group_index=group_index,
                    product_images=product_images,
                    specified_product_images=group_info["specified_product_images"],
                )
                
                # 更新统计（线程安全）
                with results_lock:
                    for img in group_result.images:
                        total_images += 1
                        if img.success:
                            successful_images += 1
                        else:
                            failed_images += 1
                
                return group_result
                
            except Exception as e:
                logger.error(f"[组{group_num}] ❌ 生成失败: {e}")
                with results_lock:
                    failed_images += 1
                return None
        
        # 并发执行组
        group_results = []
        with ThreadPoolExecutor(max_workers=max_concurrent_groups) as executor:
            futures = {executor.submit(execute_group, g): g for g in pending_groups}
            
            for future in as_completed(futures):
                group_info = futures[future]
                group_num = group_info["group_index"] + 1
                
                try:
                    result = future.result()
                    if result:
                        with self._log_lock:
                            generation_log.groups.append(result.to_dict())
                        group_results.append(result)
                except Exception as e:
                    logger.error(f"[组{group_num}] ❌ 执行异常: {e}")
        
        # 完成
        duration = time.time() - start_time
        
        result = RunResult(
            run_dir=run_dir,
            total_groups=template_cfg.group_count,
            completed_groups=len(group_results),
            total_images=total_images,
            successful_images=successful_images,
            failed_images=failed_images,
            duration_seconds=duration,
        )
        
        generation_log.completed_at = datetime.now()
        generation_log.summary = result.to_dict()
        self.output_manager.save_generation_log(generation_log)
        
        logger.info(f"🎉 生成完成: {successful_images}/{total_images}张成功, 耗时{duration:.1f}秒")
        
        return result

    def run_group(
        self,
        group_index: int,
        product_images: List[Path],
        specified_product_images: List[Path],
    ) -> GroupResult:
        """
        执行单组生成
        
        场景生成模式：每组生成 images_per_group 张图片，产品图组内不重复
        主体迁移模式：每组共用一张参考图，产品图组内不重复
        
        Args:
            group_index: 组索引
            product_images: 所有可用产品图列表
            specified_product_images: 用户指定的产品图（优先使用）
            
        Returns:
            组结果
        """
        template_cfg = self._template_config
        group_num = group_index + 1
        log_prefix = f"[组{group_num}]"
        
        logger.info(f"{log_prefix} 📦 开始执行 (共{template_cfg.group_count}组)")
        self.state_manager.mark_group_started(group_index)
        
        # 确定本组生成图片数量
        images_per_group = self.image_selector._parse_count(template_cfg.images_per_group)
        
        # 组内已使用的产品图（每组重置）
        used_products_in_group = set()
        
        # 为本组分配图片任务
        # 每个任务是一个元组：(product_image, reference_image or None)
        group_tasks = []
        
        # 获取本组的参考图（主体迁移模式，从预分配中获取）
        group_reference_image = None
        if template_cfg.mode == "subject_transfer" and group_index < len(self._reference_assignments):
            group_reference_image = self._reference_assignments[group_index]
            if group_reference_image:
                logger.info(f"{log_prefix} 🖼️ 本组背景参考图: {group_reference_image.name}")
        
        if template_cfg.mode == "scene_generation":
            # 场景生成模式：每次请求1张产品图
            # 1. 先添加指定的产品图
            for prod_img in specified_product_images:
                if len(group_tasks) >= images_per_group:
                    break
                if str(prod_img) not in used_products_in_group:
                    group_tasks.append((prod_img, None))
                    used_products_in_group.add(str(prod_img))
            
            # 2. 剩余任务随机选择（组内不重复）
            available_prods = [p for p in product_images if str(p) not in used_products_in_group]
            random.shuffle(available_prods)
            
            for prod_img in available_prods:
                if len(group_tasks) >= images_per_group:
                    break
                group_tasks.append((prod_img, None))
                used_products_in_group.add(str(prod_img))
            
            if len(group_tasks) < images_per_group:
                logger.warning(f"{log_prefix} ⚠️ 可用产品图不足，只能生成{len(group_tasks)}张")
        
        else:  # subject_transfer
            # 主体迁移模式：每组共用同一张参考图，产品图组内不重复
            if not group_reference_image:
                logger.error(f"{log_prefix} ❌ 未分配参考图，无法执行主体迁移")
                raise GeneratorError(f"组{group_num}未分配参考图")
            
            # 1. 先添加指定的产品图（都配同一张参考图）
            for prod_img in specified_product_images:
                if len(group_tasks) >= images_per_group:
                    break
                if str(prod_img) not in used_products_in_group:
                    group_tasks.append((prod_img, group_reference_image))
                    used_products_in_group.add(str(prod_img))
            
            # 2. 剩余任务随机选择产品图（组内不重复，都配同一张参考图）
            available_prods = [p for p in product_images if str(p) not in used_products_in_group]
            random.shuffle(available_prods)
            
            for prod_img in available_prods:
                if len(group_tasks) >= images_per_group:
                    break
                group_tasks.append((prod_img, group_reference_image))
                used_products_in_group.add(str(prod_img))
            
            if len(group_tasks) < images_per_group:
                logger.warning(f"{log_prefix} ⚠️ 可用产品图不足，只能生成{len(group_tasks)}张")
        
        # 获取Prompt（本组所有任务使用相同Prompt）
        prompt_item = self._prompt_assignments[group_index] if group_index < len(self._prompt_assignments) else None
        prompt_template = ""
        prompt_source = ""
        if prompt_item:
            prompt_template = prompt_item.template
            prompt_source = getattr(prompt_item, "id", "") or getattr(prompt_item, "name", "") or ""
        else:
            # 检查自定义模板
            custom_template = self._get_custom_template()
            if custom_template:
                prompt_template = custom_template
                prompt_source = "custom_template"
        
        # 创建组目录
        group_dir = self.output_manager.create_group_directory(group_num)
        
        actual_images_count = len(group_tasks)
        
        # 获取生成目标配置
        generation_target = getattr(template_cfg, 'generation_target', 'both') or 'both'
        should_generate_images = generation_target in ('image_only', 'both')
        should_generate_text = generation_target in ('text_only', 'both')
        
        if should_generate_images:
            logger.info(f"{log_prefix} 📋 本组将生成 {actual_images_count} 张图片")
        if should_generate_text:
            logger.info(f"{log_prefix} 📝 本组将生成文案")
        
        # 准备所有生成任务
        tasks = []
        all_selected_products = []
        all_selected_references = []
        image_results = []
        
        # 仅在需要生成图片时准备图片任务
        if should_generate_images:
            for image_index, (prod_img, ref_img) in enumerate(group_tasks):
                image_num = image_index + 1
                
                all_selected_products.append(prod_img)
                if ref_img:
                    all_selected_references.append(ref_img)
                
                # 上传图片
                images_to_upload = [prod_img]
                if ref_img:
                    images_to_upload.append(ref_img)
                image_urls = self._upload_images(images_to_upload)
                
                # 刷新URL
                fresh_urls = self._refresh_urls(images_to_upload)
                
                # 构建模板上下文
                context = self.template_engine.build_context(
                    group_index=group_index,
                    image_index=image_index,
                    product_count=1,
                    reference_count=1 if ref_img else 0,
                    total_groups=template_cfg.group_count,
                    mode=template_cfg.mode,
                    custom_vars=template_cfg.template_variables,
                )
                
                # 渲染Prompt
                rendered_prompt = self.template_engine.render(prompt_template, context)
                
                # 输出路径
                output_path = self.output_manager.get_output_path(
                    group_num=group_num,
                    image_num=image_num,
                    extension=template_cfg.output.format,
                )
                
                tasks.append({
                    "image_index": image_index,
                    "image_num": image_num,
                    "prompt": rendered_prompt,
                    "output_path": output_path,
                    "image_urls": fresh_urls,
                    "product_image": prod_img,
                    "reference_image": ref_img,
                })
            
            # 并发执行生成任务
            image_results = self._run_concurrent_generation_v2(
                tasks=tasks,
                group_num=group_num,
                aspect_ratio=template_cfg.output.aspect_ratio,
                resolution=template_cfg.output.resolution,
                output_format=template_cfg.output.format,
            )
        else:
            logger.info(f"{log_prefix} ⏭️ 跳过图片生成（generation_target={generation_target}）")
        
        # 生成文案（如果启用且 generation_target 包含文案）
        text_result = None
        if should_generate_text and self.text_generator and self.text_generator.is_enabled():
            text_gen_cfg = template_cfg.text_generation
            if text_gen_cfg and text_gen_cfg.enabled:
                logger.info(f"{log_prefix} 📝 开始生成文案...")
                try:
                    product_info = {
                        "product_name": template_cfg.template_variables.get("product_name", template_cfg.name),
                        "brand": template_cfg.template_variables.get("brand", ""),
                        "category": template_cfg.template_variables.get("category", "美妆"),
                        "style": template_cfg.template_variables.get("style", "种草分享"),
                        "features": template_cfg.template_variables.get("features", ""),
                        "target_audience": template_cfg.template_variables.get("target_audience", "年轻女性"),
                    }
                    
                    text_data = self.text_generator.generate_sync(product_info)

                    # 移除 AI 生成的标签（如果有）
                    content = text_data.content
                    # 移除文案末尾的 # 标签
                    content = self._remove_ai_tags(content)

                    text_result = TextResult(
                        title=text_data.title,
                        content=content,
                        success=text_data.success,
                        error=text_data.error,
                    )
                    logger.info(f"{log_prefix} 📝 文案生成成功: {text_data.title[:30]}...")

                    # 保存文案到文件
                    text_file = group_dir / "text.txt"
                    with open(text_file, "w", encoding="utf-8") as f:
                        f.write(f"标题：{text_data.title}\n\n")
                        f.write(f"文案：\n{content}\n")
                        
                        # 添加用户配置的标签
                        if text_gen_cfg.tags:
                            tags_str = " ".join([f"#{tag}" for tag in text_gen_cfg.tags])
                            f.write(f"\n{tags_str}\n")
                    
                except Exception as e:
                    logger.error(f"{log_prefix} 📝 文案生成失败: {e}")
                    text_result = TextResult(
                        title="",
                        content="",
                        success=False,
                        error=str(e),
                    )
        elif should_generate_text:
            logger.info(f"{log_prefix} ⏭️ 文案生成器未启用")
        
        # 创建组结果
        group_result = GroupResult(
            group_index=group_index,
            group_dir=group_dir,
            product_images=all_selected_products,
            reference_images=all_selected_references,
            prompt_template=prompt_source,
            prompt_rendered=tasks[0]["prompt"] if tasks else "",
            images=image_results,
            completed_at=datetime.now(),
            text_result=text_result,
        )
        
        # 保存组结果
        self.output_manager.save_group_result(group_result)
        self.state_manager.mark_group_complete(group_index, group_result)
        
        # 统计结果
        success_count = sum(1 for r in image_results if r.success)
        logger.info(f"{log_prefix} 📊 完成: {success_count}/{len(image_results)} 张成功")
        
        return group_result
    
    def _run_concurrent_generation_v2(
        self,
        tasks: List[Dict],
        group_num: int,
        aspect_ratio: str,
        resolution: str,
        output_format: str,
    ) -> List[ImageResult]:
        """
        并发执行图片生成任务（v2版本，每个任务有独立的image_urls）
        
        Args:
            tasks: 任务列表，每个任务包含独立的image_urls
            group_num: 组号（用于日志）
            aspect_ratio: 宽高比
            resolution: 分辨率
            output_format: 输出格式
            
        Returns:
            图片结果列表
        """
        results = {}
        images_count = len(tasks)
        log_prefix = f"[组{group_num}]"
        
        # 重试配置
        max_retries = 6  # 最大重试次数
        retry_delay_base = 5  # 基础重试延迟（秒）
        
        def generate_single(task: Dict) -> Tuple[int, ImageResult]:
            """生成单张图片（带重试，受全局并发限制）"""
            image_index = task["image_index"]
            image_num = task["image_num"]
            prompt = task["prompt"]
            output_path = task["output_path"]
            image_urls = task["image_urls"]
            task_log_prefix = f"{log_prefix}[{image_num}/{images_count}]"
            
            last_error = None
            
            # 获取全局并发许可
            self._concurrent_semaphore.acquire()
            try:
                for attempt in range(max_retries + 1):
                    # 速率限制（仅 KieAI 需要）
                    if self._use_rate_limiter:
                        self._rate_limiter.acquire()
                    
                    if attempt == 0:
                        logger.info(f"{task_log_prefix} 🎨 开始生成...")
                    else:
                        logger.info(f"{task_log_prefix} 🔄 重试 {attempt}/{max_retries}...")
                    
                    try:
                        result = self.api_client.generate_image(
                            prompt=prompt,
                            image_urls=image_urls,
                            output_path=output_path,
                            aspect_ratio=aspect_ratio,
                            resolution=resolution,
                            output_format=output_format,
                            log_prefix=task_log_prefix,
                        )
                        
                        logger.info(f"{task_log_prefix} ✅ 完成")
                        
                        return image_index, ImageResult(
                            index=image_index,
                            output_path=output_path,
                            task_id=result.task_id,
                            prompt=prompt,
                            input_images=image_urls,
                            success=True,
                        )
                        
                    except Exception as e:
                        last_error = e
                        error_str = str(e)
                        
                        # 判断是否可重试的错误
                        is_retryable = (
                            "429" in error_str or  # 速率限制
                            "too high" in error_str.lower() or  # 频率过高
                            "timeout" in error_str.lower() or  # 超时
                            "timed out" in error_str.lower() or
                            "500" in error_str or  # 服务器内部错误
                            "502" in error_str or  # 网关错误
                            "503" in error_str or  # 服务不可用
                            "520" in error_str or  # Cloudflare 错误
                            "522" in error_str or  # Cloudflare 连接超时
                            "524" in error_str or  # Cloudflare 超时
                            "fail" in error_str.lower()  # KieAI 任务失败
                        )
                        
                        if is_retryable and attempt < max_retries:
                            # 指数退避延迟
                            delay = retry_delay_base * (2 ** attempt)
                            logger.warning(f"{task_log_prefix} ⚠️ 失败: {e}，{delay}秒后重试...")
                            time.sleep(delay)
                            continue
                        else:
                            logger.error(f"{task_log_prefix} ❌ 失败: {e}")
                            break
                
                # 所有重试都失败
                return image_index, ImageResult(
                    index=image_index,
                    output_path=output_path,
                    task_id="",
                    prompt=prompt,
                    input_images=image_urls,
                    success=False,
                    error=str(last_error),
                )
            finally:
                # 释放全局并发许可
                self._concurrent_semaphore.release()
        
        # 使用线程池并发执行
        # 组内不限制并发数，由全局信号量控制总并发（最多100个同时进行的任务）
        max_workers = len(tasks)
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(generate_single, task): task for task in tasks}
            
            for future in as_completed(futures):
                try:
                    image_index, result = future.result()
                    results[image_index] = result
                except Exception as e:
                    task = futures[future]
                    logger.error(f"{log_prefix} ❌ 任务异常: {e}")
                    results[task["image_index"]] = ImageResult(
                        index=task["image_index"],
                        output_path=task["output_path"],
                        task_id="",
                        prompt=task["prompt"],
                        input_images=task["image_urls"],
                        success=False,
                        error=str(e),
                    )
        
        # 按索引排序返回
        return [results[i] for i in sorted(results.keys())]
    
    def resume(self, resume_dir: Path, auto_confirm: bool = False) -> RunResult:
        """
        从断点恢复执行
        
        Args:
            resume_dir: 之前的运行目录
            auto_confirm: 是否自动确认（跳过用户确认提示）
            
        Returns:
            运行结果
        """
        logger.info(f"从断点恢复: {resume_dir}")
        
        # 设置状态管理器
        self.state_manager.state_dir = resume_dir
        state = self.state_manager.load_state()
        
        if not state:
            raise GeneratorError(f"无法加载状态文件: {resume_dir}")
        
        # 设置输出管理器
        self.output_manager.set_run_dir(resume_dir)
        
        # 加载配置
        self.config_manager.template_path = Path(state.template_config_path)
        self._load_configs()
        
        # 继续执行
        return self.run(auto_confirm=auto_confirm)
