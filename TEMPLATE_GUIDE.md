# AI图片生成器 - 模板配置完全指南

> **用户拥有所有参数的最高控制权**，可以完全自定义生成行为。

---

## 目录

1. [快速开始](#快速开始)
2. [两种生成模式](#两种生成模式)
3. [配置参数详解](#配置参数详解)
4. [Prompt模板编写](#prompt模板编写)
5. [常见配置示例](#常见配置示例)
6. [运行命令](#运行命令)

---

## 快速开始

### 1. 复制模板配置文件
```bash
cp templates/scene_generation_template.json templates/my_task.json
```

### 2. 修改配置参数
编辑 `templates/my_task.json`，根据需求调整参数。

### 3. 运行生成
```bash
python3 ai_image_generator.py -t templates/my_task.json
```

---

## 两种生成模式

### 场景生成 (scene_generation)
```
输入：产品图 + 场景Prompt
输出：产品在不同场景中的展示图
```

### 主体迁移 (subject_transfer)
```
输入：产品图 + 参考背景图 + 迁移Prompt
输出：产品主体迁移到参考背景中的图片
```

---

## 配置参数详解

### 基础参数

| 参数 | 类型 | 必填 | 说明 | 示例 |
|------|------|------|------|------|
| `name` | string | ✅ | 任务名称，用于输出目录命名 | `"海洋至尊场景生成"` |
| `mode` | string | ✅ | 生成模式 | `"scene_generation"` 或 `"subject_transfer"` |
| `group_count` | int | ✅ | 生成组数，每组使用不同的prompt | `50` |
| `images_per_group` | int/array | ✅ | 每组输出图片数 | `1` 或 `[1, 3]` |

### 产品图配置 (product_images)

| 参数 | 类型 | 必填 | 说明 | 示例 |
|------|------|------|------|------|
| `source_dir` | string | ✅ | 产品图目录路径 | `"产品图/海洋至尊"` |
| `count_per_group` | int/array | ✅ | 每组选择的产品图数量 | `4` 或 `[4, 6]` |
| `selection_mode` | string | ❌ | 选择模式，默认random | `"random"` |
| `must_include` | string/null | ❌ | 必须包含的图片路径 | `"产品图/主图.jpg"` |
| `specified_images` | array | ❌ | 指定模式下的图片列表 | `["img1.jpg"]` |

### 参考图配置 (reference_images) - 仅主体迁移模式

| 参数 | 类型 | 必填 | 说明 | 示例 |
|------|------|------|------|------|
| `source_dir` | string | ✅ | 参考背景图目录路径 | `"参考图/化妆品家用场景"` |
| `count_per_group` | int/array | ✅ | 每组选择的参考图数量 | `1` 或 `[1, 2]` |
| `selection_mode` | string | ❌ | 选择模式，默认random | `"random"` |
| `must_include` | string/null | ❌ | 必须包含的参考图路径 | `"参考图/浴室.jpg"` |
| `specified_images` | array | ❌ | 指定模式下的图片列表 | `["bg1.jpg"]` |

### Prompt配置 (prompts)

| 参数 | 类型 | 必填 | 说明 | 示例 |
|------|------|------|------|------|
| `source_dir` | string | ❌ | Prompt JSON 文件路径 | `"prompts/scene_generation.json"` |
| `selection_mode` | string | ❌ | 选择模式，默认random | `"random"` |
| `unique_per_group` | bool | ❌ | 每组使用不同Prompt | `true` |
| `custom_template` | string/null | ❌ | 自定义Prompt字符串 | `"生成一张..."` |

### 输出配置 (output)

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `base_dir` | string | `"./outputs"` | 输出基础目录 |
| `aspect_ratio` | string | `"4:5"` | 宽高比：1:1, 4:5, 16:9, 9:16 |
| `resolution` | string | `"2K"` | 分辨率：1K, 2K |
| `format` | string | `"png"` | 输出格式：png, jpg |

---

## 选择模式说明

### random（随机）
从可用列表中随机选择指定数量的项目。
```json
"selection_mode": "random",
"count_per_group": [4, 6]  // 随机选择4-6张
```

### sequential（顺序）
按文件名排序顺序依次选择。
```json
"selection_mode": "sequential",
"count_per_group": 4  // 按顺序选择前4张
```

### specified（指定）
使用用户指定的列表。
```json
"selection_mode": "specified",
"specified_images": ["主图.jpg", "细节图1.jpg", "细节图2.jpg"]
```

---

## Prompt模板编写

### 文件位置
- 场景生成：`prompts/scene_generation.json`
- 主体迁移：`prompts/subject_transfer.json`

### 内置变量

| 变量 | 说明 | 示例值 |
|------|------|--------|
| `{{ group_index }}` | 组索引（从0开始） | `0` |
| `{{ group_num }}` | 组号（从1开始） | `1` |
| `{{ image_index }}` | 图片索引（从0开始） | `0` |
| `{{ image_num }}` | 图片号（从1开始） | `1` |
| `{{ product_count }}` | 当前组产品图数量 | `5` |
| `{{ reference_count }}` | 当前组参考图数量 | `1` |
| `{{ total_groups }}` | 总组数 | `50` |
| `{{ mode }}` | 生成模式 | `"scene_generation"` |

### 自定义变量

在配置文件中定义：
```json
"template_variables": {
  "product_name": "海洋至尊护肤套装",
  "brand": "海洋至尊",
  "style": "清新自然"
}
```

在模板中使用：
```
产品名称：{{ product_name }}
品牌：{{ brand }}
风格：{{ style }}
```

### 模板示例

```jinja2
{# 这是注释，不会出现在最终prompt中 #}

生成一张{{ product_name }}的产品场景图。

保持产品外观完全一致：相同的瓶型结构、比例尺寸、材质质感。

{% if mode == 'subject_transfer' %}
将产品主体迁移到参考背景中，保持自然融合。
{% else %}
将产品放置在温馨的家居场景中。
{% endif %}

画面为手机随手拍摄风格，整体呈现真实生活拍摄质感。
```

---

## 常见配置示例

### 示例1：场景生成 - 50组，每组4-6张产品图

```json
{
  "name": "海洋至尊场景生成",
  "mode": "scene_generation",
  "group_count": 50,
  "images_per_group": [1, 2],
  
  "product_images": {
    "source_dir": "产品图/海洋至尊",
    "count_per_group": [4, 6],
    "selection_mode": "random",
    "must_include": null
  },
  
  "prompts": {
    "source_dir": "prompts/scene_generation.json",
    "unique_per_group": true
  }
}
```

### 示例2：场景生成 - 必须包含主图

```json
{
  "name": "海洋至尊场景生成",
  "mode": "scene_generation",
  "group_count": 20,
  "images_per_group": 1,
  
  "product_images": {
    "source_dir": "产品图/海洋至尊",
    "count_per_group": [4, 6],
    "selection_mode": "random",
    "must_include": "产品图/海洋至尊/微信图片_20260120101033_143_98.jpg"
  }
}
```

### 示例3：主体迁移 - 指定参考背景

```json
{
  "name": "海洋至尊主体迁移",
  "mode": "subject_transfer",
  "group_count": 10,
  "images_per_group": 1,
  
  "product_images": {
    "source_dir": "产品图/海洋至尊",
    "count_per_group": [2, 4],
    "must_include": null
  },
  
  "reference_images": {
    "source_dir": "参考图/化妆品家用场景",
    "count_per_group": 1,
    "selection_mode": "specified",
    "specified_images": ["浴室台面.jpg", "梳妆台.jpg", "床头柜.jpg"]
  }
}
```

### 示例4：使用自定义Prompt

```json
{
  "name": "自定义Prompt测试",
  "mode": "scene_generation",
  "group_count": 5,
  "images_per_group": 1,
  
  "product_images": {
    "source_dir": "产品图/海洋至尊",
    "count_per_group": 4
  },
  
  "prompts": {
    "custom_template": "生成一张{{ product_name }}的产品图，风格为{{ style }}，保持产品外观完全一致。"
  },
  
  "template_variables": {
    "product_name": "海洋至尊护肤套装",
    "style": "清新自然"
  }
}
```

---

## 运行命令

```bash
# 场景生成
python3 ai_image_generator.py -t templates/scene_generation_template.json

# 主体迁移
python3 ai_image_generator.py -t templates/subject_transfer_template.json

# 验证配置（不执行生成）
python3 ai_image_generator.py -t templates/xxx.json --dry-run

# 断点续传
python3 ai_image_generator.py -t templates/xxx.json --resume outputs/xxx_20260126_143000

# 指定日志级别
python3 ai_image_generator.py -t templates/xxx.json --log-level DEBUG
```

---

## 输出目录结构

```
outputs/
└── 海洋至尊场景生成_20260126_143000/
    ├── 001/
    │   ├── 01.png
    │   ├── 02.png
    │   └── result.json
    ├── 002/
    │   └── 01.png
    ├── ...
    ├── generation_log.json
    └── results.json
```

---

## 常见问题

### Q: 如何确保每组都包含某张特定图片？
A: 使用 `must_include` 参数指定图片路径。

### Q: Prompt文件数量少于组数怎么办？
A: 系统会自动复用Prompt，但确保相邻组使用不同的Prompt。

### Q: 如何使用绝对路径？
A: 直接在 `source_dir` 中填写绝对路径，如 `/var/www/NanoBanana-MZ/产品图/海洋至尊`。

### Q: 如何断点续传？
A: 使用 `--resume` 参数指定之前的输出目录。
