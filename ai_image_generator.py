#!/usr/bin/env python3
"""
AI图片生成器 - 主入口脚本

使用方法:
    # 场景生成
    python ai_image_generator.py -t templates/scene_generation_template.json
    
    # 主体迁移
    python ai_image_generator.py -t templates/subject_transfer_template.json
    
    # 验证配置
    python ai_image_generator.py -t templates/xxx.json --dry-run
    
    # 断点续传
    python ai_image_generator.py -t templates/xxx.json --resume outputs/xxx_20260126_143000
"""

import sys
from ai_image_generator.cli import main

if __name__ == "__main__":
    sys.exit(main())
