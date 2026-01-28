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
        with self._lock:
            now = time.time()
            # 清理过期的请求记录
            self.requests = [t for t in self.requests if now - t < self.time_window]
            
            if len(self.requests) >= self.max_requests:
                # 需要等待，计算等待时间
                oldest = self.requests[0]
                wait_time = self.time_window - (now - oldest) + 0.1
                if wait_time > 0:
                    logger.debug(f"速率限制，等待 {wait_time:.1f}秒")
                    time.sleep(wait_time)
                    # 重新清理
                    now = time.time()
                    self.requests = [t for t in self.requests if now - t < self.time_window]
            
            self.requests.append(time.time())


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
        self._prompt_assignments: List[Path] = []  # 每组分配的prompt
        self._uploaded_urls: Dict[str, str] = {}  # 路径 -> URL映射
        self._uploaded_moss_ids: Dict[str, str] = {}  # 路径 -> moss_id映射
        self._upload_lock = threading.Lock()  # 上传缓存锁
        
        # 速率限制器：10秒20个请求（仅 KieAI 需要）
        self._rate_limiter = RateLimiter(max_requests=20, time_window=10.0)
        self._use_rate_limiter = True  # 是否启用速率限制
        
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
        prompts: List[Path],
        group_count: int,
        mode: str,
    ) -> List[Path]:
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
            prompts: 可用的 prompt 文件列表
            group_count: 组数
            mode: 生成模式
            
        Returns:
            每组对应的 prompt 路径列表
        """
        template_cfg = self._template_config
        
        if mode == "scene_generation":
            return self._allocate_scene_prompts(prompts, group_count)
        else:  # subject_transfer
            return self._allocate_transfer_prompts(prompts, group_count)
    
    def _allocate_scene_prompts(self, prompts: List[Path], group_count: int) -> List[Path]:
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
            for spec in template_cfg.scene_prompts.specified_prompts:
                found = self.image_selector.find_image_by_path(prompts, spec)
                if found:
                    specified.append(found)
                else:
                    logger.warning(f"⚠️ 指定的 prompt 未找到: {spec}")
        
        # 分配 prompts
        for i in range(group_count):
            previous = result[-1] if result else None
            
            if i < len(specified):
                # 使用指定的 prompt
                selected = specified[i]
            else:
                # 随机选择未使用的 prompt
                selected = self.image_selector.select_unique_prompt(
                    prompts=prompts,
                    used_prompts=used_prompts,
                    previous_prompt=str(previous) if previous else None,
                )
            
            if selected:
                result.append(selected)
                used_prompts.add(str(selected))
            elif prompts:
                # 所有 prompts 都用过了，复用但确保与上一组不同
                available = [p for p in prompts if str(p) != str(previous)] if previous else prompts
                result.append(random.choice(available) if available else prompts[0])
            else:
                result.append(None)
        
        return result
    
    def _allocate_transfer_prompts(self, prompts: List[Path], group_count: int) -> List[Path]:
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
            spec = template_cfg.transfer_prompts.specified_prompt
            selected = self.image_selector.find_image_by_path(prompts, spec)
            if not selected:
                logger.warning(f"⚠️ 指定的 prompt 未找到: {spec}，将随机选择")
        
        # 如果没有指定或未找到，随机选择一个
        if not selected and prompts:
            selected = random.choice(prompts)
        
        if selected:
            logger.info(f"📝 主体迁移模式：所有组使用 prompt: {selected.name}")
        
        # 所有组使用同一个 prompt
        return [selected] * group_count
    
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
            prompts = self.image_selector.list_prompts(paths["prompts"])
            logger.info(f"找到 {len(prompts)} 个Prompt文件")
        
        # 计算每组需要的图片数量（使用最大值进行检查）
        images_per_group_cfg = template_cfg.images_per_group
        if isinstance(images_per_group_cfg, list) and len(images_per_group_cfg) == 2:
            max_images_per_group = images_per_group_cfg[1]
        else:
            max_images_per_group = int(images_per_group_cfg) if images_per_group_cfg else 1
        
        # 检查资源数量是否足够
        warnings = []
        
        # 检查 Prompt 数量
        if prompts and len(prompts) < template_cfg.group_count:
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
            if len(reference_images) < max_images_per_group:
                warnings.append(
                    f"参考图数量不足: 每组最多需要 {max_images_per_group} 张，但只有 {len(reference_images)} 张可用"
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
                    user_input = input("\n是否继续执行？(y/N): ").strip().lower()
                    if user_input != 'y':
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
        
        # 验证指定图片
        specified_product_images = []
        specified_reference_images = []
        
        prod_cfg = template_cfg.product_images
        if prod_cfg.specified_images:
            specified_product_images, errors = self.image_selector.validate_specified_images(
                specified=prod_cfg.specified_images,
                available_images=product_images,
            )
            if errors:
                for err in errors:
                    logger.error(f"❌ 产品图: {err}")
                raise GeneratorError(f"指定产品图验证失败: {'; '.join(errors)}")
            logger.info(f"📋 用户指定了 {len(specified_product_images)} 张产品图")
        
        if template_cfg.mode == "subject_transfer" and template_cfg.reference_images:
            ref_cfg = template_cfg.reference_images
            if ref_cfg.specified_images:
                specified_reference_images, errors = self.image_selector.validate_specified_images(
                    specified=ref_cfg.specified_images,
                    available_images=reference_images,
                )
                if errors:
                    for err in errors:
                        logger.error(f"❌ 参考图: {err}")
                    raise GeneratorError(f"指定参考图验证失败: {'; '.join(errors)}")
                logger.info(f"📋 用户指定了 {len(specified_reference_images)} 张参考图")
            
            # 检查主体迁移模式下指定数量是否匹配
            if specified_product_images and specified_reference_images:
                if len(specified_product_images) != len(specified_reference_images):
                    logger.warning(f"⚠️ 指定的产品图({len(specified_product_images)}张)和参考图({len(specified_reference_images)}张)数量不匹配")
                    logger.warning(f"   多出的图片将随机配对")
        
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
            logger.info(f"📋 指定图片将覆盖前 {coverage_groups}/{template_cfg.group_count} 组 ({specified_coverage}%)")
        
        # 获取最大并发组数
        max_concurrent_groups = template_cfg.output.max_concurrent_groups
        logger.info(f"🚀 最大并发组数: {max_concurrent_groups}")
        
        # 收集待执行的组
        pending_groups = []
        for group_index in range(template_cfg.group_count):
            if self.state_manager.is_group_complete(group_index):
                logger.info(f"⏭️ 跳过已完成的组 {group_index + 1}")
                continue
            
            use_specified = group_index < coverage_groups
            pending_groups.append({
                "group_index": group_index,
                "specified_product_images": specified_product_images if use_specified else [],
                "specified_reference_images": specified_reference_images if use_specified else [],
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
                    reference_images=reference_images,
                    specified_product_images=group_info["specified_product_images"],
                    specified_reference_images=group_info["specified_reference_images"],
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
        reference_images: List[Path],
        specified_product_images: List[Path],
        specified_reference_images: List[Path],
    ) -> GroupResult:
        """
        执行单组生成
        
        每组生成 images_per_group 张图片，同组内图片不重复
        
        Args:
            group_index: 组索引
            product_images: 所有可用产品图列表
            reference_images: 所有可用参考图列表
            specified_product_images: 用户指定的产品图（优先使用）
            specified_reference_images: 用户指定的参考图（优先使用）
            
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
        
        # 组内已使用的图片（每组重置）
        used_products_in_group = set()
        used_references_in_group = set()
        
        # 为本组分配图片任务
        # 每个任务是一个元组：(product_image, reference_image or None)
        group_tasks = []
        
        # 场景生成模式：每次请求1张产品图
        # 主体迁移模式：每次请求1张产品图 + 1张参考图
        
        if template_cfg.mode == "scene_generation":
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
            # 1. 先添加指定的配对
            specified_pairs = min(len(specified_product_images), len(specified_reference_images))
            for i in range(specified_pairs):
                if len(group_tasks) >= images_per_group:
                    break
                prod_img = specified_product_images[i]
                ref_img = specified_reference_images[i]
                if str(prod_img) not in used_products_in_group and str(ref_img) not in used_references_in_group:
                    group_tasks.append((prod_img, ref_img))
                    used_products_in_group.add(str(prod_img))
                    used_references_in_group.add(str(ref_img))
            
            # 2. 处理多出的指定图片（随机配对）
            extra_prods = specified_product_images[specified_pairs:]
            extra_refs = specified_reference_images[specified_pairs:]
            
            # 多出的产品图配随机参考图
            available_refs = [r for r in reference_images if str(r) not in used_references_in_group]
            random.shuffle(available_refs)
            ref_idx = 0
            for prod_img in extra_prods:
                if len(group_tasks) >= images_per_group:
                    break
                if str(prod_img) not in used_products_in_group and ref_idx < len(available_refs):
                    ref_img = available_refs[ref_idx]
                    group_tasks.append((prod_img, ref_img))
                    used_products_in_group.add(str(prod_img))
                    used_references_in_group.add(str(ref_img))
                    ref_idx += 1
            
            # 多出的参考图配随机产品图
            available_prods = [p for p in product_images if str(p) not in used_products_in_group]
            random.shuffle(available_prods)
            prod_idx = 0
            for ref_img in extra_refs:
                if len(group_tasks) >= images_per_group:
                    break
                if str(ref_img) not in used_references_in_group and prod_idx < len(available_prods):
                    prod_img = available_prods[prod_idx]
                    group_tasks.append((prod_img, ref_img))
                    used_products_in_group.add(str(prod_img))
                    used_references_in_group.add(str(ref_img))
                    prod_idx += 1
            
            # 3. 剩余任务随机配对（组内不重复）
            available_prods = [p for p in product_images if str(p) not in used_products_in_group]
            available_refs = [r for r in reference_images if str(r) not in used_references_in_group]
            random.shuffle(available_prods)
            random.shuffle(available_refs)
            
            for i in range(min(len(available_prods), len(available_refs))):
                if len(group_tasks) >= images_per_group:
                    break
                group_tasks.append((available_prods[i], available_refs[i]))
                used_products_in_group.add(str(available_prods[i]))
                used_references_in_group.add(str(available_refs[i]))
            
            if len(group_tasks) < images_per_group:
                logger.warning(f"{log_prefix} ⚠️ 可用图片不足，只能生成{len(group_tasks)}张")
        
        # 获取Prompt（本组所有任务使用相同Prompt）
        prompt_path = self._prompt_assignments[group_index] if group_index < len(self._prompt_assignments) else None
        prompt_template = ""
        if prompt_path:
            prompt_template = self.template_engine.load_template(prompt_path)
        else:
            # 检查自定义模板
            custom_template = self._get_custom_template()
            if custom_template:
                prompt_template = custom_template
        
        # 创建组目录
        group_dir = self.output_manager.create_group_directory(group_num)
        
        actual_images_count = len(group_tasks)
        logger.info(f"{log_prefix} 📋 本组将生成 {actual_images_count} 张图片")
        
        # 准备所有生成任务
        tasks = []
        all_selected_products = []
        all_selected_references = []
        
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
        
        # 生成文案（如果启用）
        text_result = None
        if self.text_generator and self.text_generator.is_enabled():
            text_gen_cfg = template_cfg.text_generation
            if text_gen_cfg and text_gen_cfg.enabled:
                logger.info(f"{log_prefix} 📝 开始生成文案...")
                try:
                    product_info = {
                        "product_name": template_cfg.template_variables.get("product_name", template_cfg.name),
                        "brand": template_cfg.template_variables.get("brand", ""),
                        "style": template_cfg.template_variables.get("style", "种草分享"),
                        "features": template_cfg.template_variables.get("features", ""),
                        "target_audience": template_cfg.template_variables.get("target_audience", "年轻女性"),
                    }
                    
                    text_data = self.text_generator.generate_sync(product_info)
                    
                    # 移除 AI 生成的标签（如果有）
                    content = text_data["content"]
                    # 移除文案末尾的 # 标签
                    content = self._remove_ai_tags(content)
                    
                    text_result = TextResult(
                        title=text_data["title"],
                        content=content,
                        success=True,
                    )
                    logger.info(f"{log_prefix} 📝 文案生成成功: {text_data['title'][:30]}...")
                    
                    # 保存文案到文件
                    text_file = group_dir / "text.txt"
                    with open(text_file, "w", encoding="utf-8") as f:
                        f.write(f"标题：{text_data['title']}\n\n")
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
        
        # 创建组结果
        group_result = GroupResult(
            group_index=group_index,
            group_dir=group_dir,
            product_images=all_selected_products,
            reference_images=all_selected_references,
            prompt_template=str(prompt_path) if prompt_path else "",
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
        
        def generate_single(task: Dict) -> Tuple[int, ImageResult]:
            """生成单张图片"""
            image_index = task["image_index"]
            image_num = task["image_num"]
            prompt = task["prompt"]
            output_path = task["output_path"]
            image_urls = task["image_urls"]
            task_log_prefix = f"{log_prefix}[{image_num}/{images_count}]"
            
            # 速率限制（仅 KieAI 需要）
            if self._use_rate_limiter:
                self._rate_limiter.acquire()
            
            logger.info(f"{task_log_prefix} 🎨 开始生成...")
            
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
                logger.error(f"{task_log_prefix} ❌ 失败: {e}")
                return image_index, ImageResult(
                    index=image_index,
                    output_path=output_path,
                    task_id="",
                    prompt=prompt,
                    input_images=image_urls,
                    success=False,
                    error=str(e),
                )
        
        # 使用线程池并发执行
        # KieAI 限制组内最多5个并发，OpenRouter 不限制
        if self._use_rate_limiter:
            max_workers = min(len(tasks), 5)
        else:
            max_workers = len(tasks)  # OpenRouter 全并发
        
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
