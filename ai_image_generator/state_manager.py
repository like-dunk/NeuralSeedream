"""
状态管理器 - 支持断点续传
"""

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Set

from .exceptions import GeneratorError
from .models import GroupResult, RunState

logger = logging.getLogger(__name__)


class StateManager:
    """状态管理器 - 支持断点续传（线程安全）"""
    
    STATE_FILE_NAME = "results.json"
    
    def __init__(self, state_dir: Path):
        """
        初始化状态管理器
        
        Args:
            state_dir: 状态文件所在目录
        """
        self.state_dir = Path(state_dir)
        self.state_file = self.state_dir / self.STATE_FILE_NAME
        self._state: Optional[RunState] = None
        self._lock = threading.Lock()  # 线程锁
    
    def load_state(self) -> Optional[RunState]:
        """
        加载之前的运行状态
        
        Returns:
            运行状态，如果不存在返回None
        """
        if not self.state_file.exists():
            return None
        
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            self._state = RunState.from_dict(data)
            logger.info(f"加载状态: 已完成{len(self._state.completed_groups)}组")
            return self._state
        
        except json.JSONDecodeError as e:
            logger.error(f"状态文件损坏: {e}")
            raise GeneratorError(f"状态文件损坏，无法恢复: {self.state_file}")
        except Exception as e:
            logger.error(f"加载状态失败: {e}")
            return None
    
    def save_state(self, state: RunState):
        """
        保存当前状态（线程安全）
        
        Args:
            state: 运行状态
        """
        with self._lock:
            self._state = state
            self.state_dir.mkdir(parents=True, exist_ok=True)
            
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)
            
            logger.debug(f"保存状态: 已完成{len(state.completed_groups)}组")
    
    def init_state(self, template_config_path: str, run_dir: Path) -> RunState:
        """
        初始化新的运行状态
        
        Args:
            template_config_path: 模板配置文件路径
            run_dir: 运行目录
            
        Returns:
            新的运行状态
        """
        self._state = RunState(
            template_config_path=template_config_path,
            run_dir=run_dir,
            started_at=datetime.now(),
            completed_groups={},
            current_group=None,
        )
        self.save_state(self._state)
        return self._state
    
    def mark_group_complete(self, group_index: int, result: GroupResult):
        """
        标记组完成（线程安全）
        
        Args:
            group_index: 组索引
            result: 组结果
        """
        with self._lock:
            if not self._state:
                raise GeneratorError("状态未初始化")
            
            self._state.completed_groups[group_index] = result.to_dict()
            self._state.current_group = None
            
            # 直接写入文件（已在锁内）
            self.state_dir.mkdir(parents=True, exist_ok=True)
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self._state.to_dict(), f, ensure_ascii=False, indent=2)
            
            logger.info(f"组{group_index + 1}完成，已保存状态")
    
    def mark_group_started(self, group_index: int):
        """
        标记组开始处理（线程安全）
        
        Args:
            group_index: 组索引
        """
        with self._lock:
            if not self._state:
                raise GeneratorError("状态未初始化")
            
            self._state.current_group = group_index
            
            # 直接写入文件（已在锁内）
            self.state_dir.mkdir(parents=True, exist_ok=True)
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self._state.to_dict(), f, ensure_ascii=False, indent=2)
    
    def get_next_group_index(self, total_groups: int) -> int:
        """
        获取下一个待处理的组索引
        
        Args:
            total_groups: 总组数
            
        Returns:
            下一个待处理的组索引，如果全部完成返回total_groups
        """
        if not self._state:
            return 0
        
        completed = set(self._state.completed_groups.keys())
        
        for i in range(total_groups):
            if i not in completed:
                return i
        
        return total_groups
    
    def is_group_complete(self, group_index: int) -> bool:
        """
        检查组是否已完成（线程安全）
        
        Args:
            group_index: 组索引
            
        Returns:
            是否已完成
        """
        with self._lock:
            if not self._state:
                return False
            
            return group_index in self._state.completed_groups
    
    def get_completed_groups(self) -> Set[int]:
        """
        获取已完成的组索引集合
        
        Returns:
            已完成的组索引集合
        """
        if not self._state:
            return set()
        
        return set(self._state.completed_groups.keys())
    
    def get_state(self) -> Optional[RunState]:
        """获取当前状态"""
        return self._state
