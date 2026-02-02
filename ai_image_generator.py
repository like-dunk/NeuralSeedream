#!/usr/bin/env python3
"""
AI图片生成器 - 主入口脚本

使用方法:
    # 新运行（使用默认模板）
    python ai_image_generator.py
    
    # 指定模板运行
    python ai_image_generator.py -t templates/scene_generation_template.json
    
    # 验证配置
    python ai_image_generator.py --dry-run
    
    # 断点续传（直接传入之前的运行目录）
    python ai_image_generator.py outputs/海洋至尊_20260126_143000
"""

import subprocess
import sys


def check_and_install_dependencies():
    """检查并自动安装缺失的依赖"""
    required = [
        ("requests", "requests"),
        ("httpx", "httpx"),
        ("jinja2", "Jinja2"),
        ("openai", "openai"),
    ]
    
    missing = []
    for import_name, pip_name in required:
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pip_name)
    
    if missing:
        print(f"🔍 检测到缺失的依赖包: {', '.join(missing)}")
        print("   正在自动安装...")
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "--quiet"] + missing,
                stderr=subprocess.PIPE,
            )
            print("✅ 依赖安装完成！\n")
        except subprocess.CalledProcessError:
            print(f"❌ 安装失败，请手动运行: pip install {' '.join(missing)}")
            sys.exit(1)
    
    # 可选依赖提示
    try:
        __import__("pillow_heif")
    except ImportError:
        print("💡 提示: 如需支持 HEIC 图片，请运行: pip install pillow-heif\n")


if __name__ == "__main__":
    # 必须在导入包之前检查依赖
    check_and_install_dependencies()
    
    from ai_image_generator.cli import main
    sys.exit(main())
