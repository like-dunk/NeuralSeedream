"""
Excel 报告生成器 - 生成图片统计报告
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def generate_excel_report(
    run_dir: Path,
    output_filename: Optional[str] = None,
    target_width: int = 120,
    target_height: int = 150,
    padding: int = 5,
) -> Optional[Path]:
    """
    为运行目录生成 Excel 统计报告
    
    每一行代表一个组（文件夹），展示该组生成的所有图片
    所有图片统一缩放到相同的目标尺寸
    
    Args:
        run_dir: 运行目录路径（包含 001, 002 等组目录）
        output_filename: 输出文件名（默认为 output_smart.xlsx）
        target_width: 图片目标宽度（像素）
        target_height: 图片目标高度（像素）
        padding: 图片边距
        
    Returns:
        生成的 Excel 文件路径，失败返回 None
    """
    # 依赖检查（CLI 入口已提前安装，这里做兜底）
    try:
        import xlsxwriter
        from PIL import Image
    except ImportError as e:
        logger.warning(f"⚠️ 无法生成 Excel 报告，缺少依赖: {e}")
        logger.warning("   请安装: pip install xlsxwriter Pillow")
        return None
    
    # 确定输出文件路径
    if output_filename is None:
        output_filename = "output_smart.xlsx"
    
    output_path = run_dir / output_filename
    
    try:
        workbook = xlsxwriter.Workbook(str(output_path))
        worksheet = workbook.add_worksheet()
        
        # 样式
        cell_format = workbook.add_format({
            'align': 'center',
            'valign': 'vcenter',
            'border': 1
        })
        header_format = workbook.add_format({
            'bold': True,
            'align': 'center',
            'valign': 'vcenter',
            'border': 1,
            'bg_color': '#D7E4BC'
        })
        
        # 写入表头
        headers = ["组号", "图片展示"]
        worksheet.write(0, 0, headers[0], header_format)
        
        # 设置第一列宽度
        worksheet.set_column(0, 0, 15)
        
        # 设置表头行高度（正常高度）
        worksheet.set_row(0, 20)
        
        # 设置数据行的默认高度（图片高度 + 边距）
        worksheet.set_default_row(target_height + padding)
        
        # 计算统一的列宽（基于目标宽度）
        # Excel 宽度单位约 7 像素
        uniform_col_width = (target_width + padding) / 7
        
        # 获取所有组目录（001, 002, ...）
        group_dirs = []
        for item in run_dir.iterdir():
            if item.is_dir() and item.name.isdigit():
                group_dirs.append(item)
        
        group_dirs.sort(key=lambda x: int(x.name))
        
        if not group_dirs:
            logger.warning(f"⚠️ 未找到组目录: {run_dir}")
            workbook.close()
            # 删除空文件
            try:
                output_path.unlink()
            except Exception:
                pass
            return None
        
        # 先遍历一遍，计算最大图片数量（用于合并表头和设置列宽）
        max_images_count = 0
        for group_dir in group_dirs:
            images_count = 0
            for img_file in group_dir.iterdir():
                if img_file.is_file():
                    suffix = img_file.suffix.lower()
                    name = img_file.name.lower()
                    if suffix in ('.png', '.jpg', '.jpeg') and '参考图' not in name:
                        images_count += 1
            max_images_count = max(max_images_count, images_count)
        
        # 设置所有图片列的统一宽度
        for col in range(1, max_images_count + 1):
            worksheet.set_column(col, col, uniform_col_width)
        
        # 合并"图片展示"表头（从第2列到最后一列）
        if max_images_count > 1:
            worksheet.merge_range(0, 1, 0, max_images_count, headers[1], header_format)
        else:
            worksheet.write(0, 1, headers[1], header_format)
        
        current_row = 1
        
        for group_dir in group_dirs:
            group_num = group_dir.name
            worksheet.write(current_row, 0, f"组 {group_num}", cell_format)
            
            # 获取该组的所有生成图片（排除参考图）
            images = []
            for img_file in group_dir.iterdir():
                if img_file.is_file():
                    suffix = img_file.suffix.lower()
                    name = img_file.name.lower()
                    # 只包含生成的图片（01.png, 02.png 等），排除参考图
                    if suffix in ('.png', '.jpg', '.jpeg') and '参考图' not in name:
                        images.append(img_file)
            
            images.sort(key=lambda x: x.name)
            
            current_col = 1
            for image_file in images:
                try:
                    # 使用 PIL 读取图片真实尺寸
                    with Image.open(image_file) as img:
                        orig_w, orig_h = img.size
                    
                    # 计算缩放比例（fit 模式：保持宽高比，适应目标尺寸）
                    scale_w = target_width / orig_w
                    scale_h = target_height / orig_h
                    scale_factor = min(scale_w, scale_h)  # 取较小值确保图片完全在目标区域内
                    
                    # 计算缩放后的实际尺寸
                    scaled_w = orig_w * scale_factor
                    scaled_h = orig_h * scale_factor
                    
                    # 计算偏移量使图片在单元格中居中
                    x_offset = (target_width - scaled_w) / 2
                    y_offset = (target_height - scaled_h) / 2
                    
                    # 插入图片
                    worksheet.insert_image(current_row, current_col, str(image_file), {
                        'x_scale': scale_factor,
                        'y_scale': scale_factor,
                        'x_offset': x_offset,
                        'y_offset': y_offset,
                        'object_position': 1
                    })
                    
                except Exception as e:
                    logger.warning(f"处理图片 {image_file.name} 出错: {e}")
                
                current_col += 1
            
            current_row += 1
        
        workbook.close()
        logger.info(f"📊 Excel 报告已生成: {output_path}")
        return output_path
        
    except Exception as e:
        logger.error(f"❌ 生成 Excel 报告失败: {e}")
        return None
