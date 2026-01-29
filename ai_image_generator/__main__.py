"""
允许通过 python -m ai_image_generator 运行
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


# 必须在导入其他模块之前检查依赖
check_and_install_dependencies()

from .cli import main

if __name__ == "__main__":
    main()
