# 配置字段参考文档

本文档详细说明 `generation_template.json` 中所有配置字段的含义和用法。

---

## 基础配置

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | ✅ | 任务名称，用于输出目录命名 |
| `description` | string | ❌ | 任务描述 |
| `mode` | string | ✅ | 生成模式，见下方说明 |
| `group_count` | int | ✅ | 生成组数，每组使用相同的 Prompt |
| `images_per_group` | int 或 [min, max] | ✅ | 每组生成图片数 |

### mode 生成模式

| 值 | 说明 |
|----|------|
| `scene_generation` | 场景生成：产品图 + 场景 Prompt，生成产品在特定场景中的图片 |
| `subject_transfer` | 主体迁移：产品图 + 参考背景图，将产品主体迁移到参考背景中 |

### images_per_group 每组图片数

- 固定值：`4` 表示每组固定生成 4 张
- 范围值：`[2, 4]` 表示每组随机生成 2-4 张
- 组内图片不重复，组间可以重复

---

## product_images 产品图配置

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `source_dir` | string | ✅ | 产品图目录路径 |
| `specified_images` | string[] | ❌ | 指定优先使用的图片列表 |
| `specified_coverage` | int | ❌ | 指定图片覆盖的组百分比，默认 100 |

### specified_images 指定图片

- 示例：`["主图.jpg", "细节图.jpg"]`
- 这些图片在每组都会被优先使用
- 剩余任务从其他图片中随机选择（组内不重复）

### specified_coverage 覆盖百分比

- `100`：所有组都使用指定图片（默认）
- `50`：前 50% 的组使用指定图片，后 50% 完全随机
- `0`：所有组都完全随机

---

## reference_images 参考图配置

> ⚠️ 仅 `subject_transfer` 主体迁移模式使用，`scene_generation` 模式会忽略此配置

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `source_dir` | string | ✅ | 参考背景图目录路径 |
| `specified_images` | string[] | ❌ | 指定优先使用的参考图列表 |

### 主体迁移配对规则

- 指定的产品图和参考图按顺序一一配对
- 示例：产品图 `["P1.jpg", "P2.jpg"]`，参考图 `["R1.jpg", "R2.jpg"]`
- 配对结果：(P1+R1), (P2+R2)，剩余任务随机配对
- `specified_coverage` 由 `product_images` 中的配置统一控制

---

## prompts Prompt 配置

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `source_dir` | string | ❌ | Prompt 目录（通用） |
| `source_dir_scene` | string | ❌ | 场景生成模式的 Prompt 目录 |
| `source_dir_transfer` | string | ❌ | 主体迁移模式的 Prompt 目录 |
| `selection_mode` | string | ❌ | 选择模式，默认 `random` |
| `unique_per_group` | bool | ❌ | 是否每组使用不同 Prompt，默认 `true` |
| `specified_prompts` | string[] | ❌ | 指定使用的 Prompt 文件名列表 |
| `custom_template` | string | ❌ | 自定义 Prompt 字符串 |

### source_dir 优先级

1. 如果设置了 `source_dir`，直接使用
2. 否则根据 `mode` 自动选择 `source_dir_scene` 或 `source_dir_transfer`

### selection_mode 选择模式

| 值 | 说明 |
|----|------|
| `random` | 随机不重复选择，Prompt 不足时会随机复用 |
| `sequential` | 按文件名顺序选择 |
| `specified` | 使用 `specified_prompts` 指定的列表 |

### custom_template 自定义 Prompt

- 设置后会忽略 `source_dir`
- 支持 Jinja2 模板语法
- 可使用 `template_variables` 中定义的变量

---

## output 输出配置

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `base_dir` | string | ❌ | `./outputs` | 输出根目录 |
| `aspect_ratio` | string | ❌ | `4:5` | 宽高比 |
| `resolution` | string | ❌ | `2K` | 分辨率 |
| `format` | string | ❌ | `png` | 输出格式 |
| `max_concurrent_groups` | int | ❌ | `3` | 最大并发组数 |

### aspect_ratio 宽高比

| 值 | 说明 |
|----|------|
| `1:1` | 正方形 |
| `4:5` | 小红书竖版 |
| `16:9` | 横版 |
| `9:16` | 竖版 |

### resolution 分辨率

| 值 | 说明 |
|----|------|
| `1K` | 标准分辨率 |
| `2K` | 高分辨率 |

### format 输出格式

| 值 | 说明 |
|----|------|
| `png` | PNG 格式，无损 |
| `jpg` | JPEG 格式，有损压缩 |

### 输出目录结构

```
outputs/
└── {name}_{时间戳}/
    ├── group_1/
    │   ├── image_1.png
    │   ├── image_2.png
    │   └── ...
    ├── group_2/
    └── ...
```

---

## template_variables 模板变量

自定义变量，可在 Prompt（.j2）模板中使用 `{{ 变量名 }}` 引用。

示例：
```json
{
  "product_name": "海洋至尊护肤套装",
  "brand": "海洋至尊",
  "style": "清新自然"
}
```

在 Prompt 中使用：
```
请为 {{ brand }} 的 {{ product_name }} 生成一张 {{ style }} 风格的产品图
```
用途：让同一个 Prompt 模板可以复用，只需要改配置文件里的变量值，不用改 Prompt 文件本身。
---

## 完整配置示例

### 场景生成模式

```json
{
  "name": "海洋至尊场景生成",
  "mode": "scene_generation",
  "group_count": 3,
  "images_per_group": [2, 4],
  "product_images": {
    "source_dir": "产品图/海洋至尊",
    "specified_images": [],
    "specified_coverage": 100
  },
  "prompts": {
    "source_dir_scene": "Prompt/图片生成/场景生成",
    "source_dir_transfer": "Prompt/图片生成/主体迁移",
    "selection_mode": "random",
    "unique_per_group": true
  },
  "output": {
    "base_dir": "./outputs",
    "aspect_ratio": "4:5",
    "resolution": "2K",
    "format": "png",
    "max_concurrent_groups": 3
  },
  "template_variables": {
    "product_name": "海洋至尊护肤套装",
    "brand": "海洋至尊"
  }
}
```

### 主体迁移模式

```json
{
  "name": "海洋至尊主体迁移",
  "mode": "subject_transfer",
  "group_count": 3,
  "images_per_group": [2, 4],
  "product_images": {
    "source_dir": "产品图/海洋至尊",
    "specified_images": [],
    "specified_coverage": 100
  },
  "reference_images": {
    "source_dir": "参考图/化妆品家用场景",
    "specified_images": []
  },
  "prompts": {
    "source_dir_scene": "Prompt/图片生成/场景生成",
    "source_dir_transfer": "Prompt/图片生成/主体迁移",
    "selection_mode": "random",
    "unique_per_group": true
  },
  "output": {
    "base_dir": "./outputs",
    "aspect_ratio": "4:5",
    "resolution": "2K",
    "format": "png",
    "max_concurrent_groups": 3
  },
  "template_variables": {
    "product_name": "海洋至尊护肤套装",
    "brand": "海洋至尊"
  }
}
```

---

## 命令行使用

```bash
# 验证配置
python3 ai_image_generator.py -t templates/generation_template.json --dry-run

# 执行生成（会提示确认）
python3 ai_image_generator.py -t templates/generation_template.json

# 执行生成（跳过确认）
python3 ai_image_generator.py -t templates/generation_template.json -y

# 断点续传
python3 ai_image_generator.py -t templates/generation_template.json --resume outputs/xxx_20260126_143000
```
