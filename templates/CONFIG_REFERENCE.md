# 配置字段参考文档

本文档详细说明模板配置文件中所有字段的含义和用法。

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

## scene_prompts 场景生成 Prompt 配置

> 仅 `scene_generation` 模式使用

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `source_dir` | string | ❌ | Prompt 目录，默认 `Prompt/图片生成/场景生成` |
| `specified_prompts` | string[] | ❌ | 指定优先使用的 Prompt 文件名列表 |
| `custom_template` | string | ❌ | 自定义 Prompt 字符串（优先级最高） |

### 场景生成 Prompt 选择规则

- **每组使用不同的 Prompt**（不重复随机）
- 指定的 Prompts 优先分配给前面的组，剩余组继续随机
- Prompt 用完后才会复用（确保相邻组不同）

**示例：**
```json
{
  "scene_prompts": {
    "source_dir": "Prompt/图片生成/场景生成",
    "specified_prompts": ["01_wood_desk_storage_box.j2", "02_wood_desk_bunny.j2"],
    "custom_template": null
  }
}
```

假设有 5 组，目录下有 4 个 Prompt 文件：
- 组 1：使用 `01_wood_desk_storage_box.j2`（指定）
- 组 2：使用 `02_wood_desk_bunny.j2`（指定）
- 组 3：随机选择剩余的（如 `03_reading_desk_books.j2`）
- 组 4：随机选择剩余的（如 `04_vanity_table_dog.j2`）
- 组 5：复用（确保与组 4 不同）

---

## transfer_prompts 主体迁移 Prompt 配置

> 仅 `subject_transfer` 模式使用

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `source_dir` | string | ❌ | Prompt 目录，默认 `Prompt/图片生成/主体迁移` |
| `specified_prompt` | string | ❌ | 指定使用的单个 Prompt 文件名 |
| `custom_template` | string | ❌ | 自定义 Prompt 字符串（优先级最高） |

### 主体迁移 Prompt 选择规则

- **所有组共用同一个 Prompt**
- 默认从目录中随机选择一个
- 如果指定了 `specified_prompt`，所有组都使用该 Prompt

**示例：**
```json
{
  "transfer_prompts": {
    "source_dir": "Prompt/图片生成/主体迁移",
    "specified_prompt": "01_product_to_background.j2",
    "custom_template": null
  }
}
```

所有组都会使用 `01_product_to_background.j2`。

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
    ├── 001/                       # 第1组
    │   ├── 01.png                 # 图片1
    │   ├── 02.png                 # 图片2
    │   ├── text.txt               # 文案（标题+内容）
    │   └── result.json            # 组结果
    ├── 002/                       # 第2组
    │   └── ...
    ├── generation_log.json        # 生成日志
    └── results.json               # 运行状态（用于断点续传）
```

---

## text_generation 文案生成配置

每组图片生成完成后，会自动生成一个标题和文案，保存到 `text.txt` 文件中。

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `enabled` | bool | ❌ | `true` | 是否启用文案生成 |
| `title_prompts_dir` | string | ❌ | `Prompt/文案生成/标题` | 标题样本目录（Few-shot） |
| `content_prompts_dir` | string | ❌ | `Prompt/文案生成/文案` | 文案样本目录（Few-shot） |
| `max_few_shot_examples` | int | ❌ | `5` | 最大 Few-shot 样本数 |

### 文案生成原理

1. 从 `title_prompts_dir` 和 `content_prompts_dir` 加载样本文件作为 Few-shot 示例
2. 使用 OpenRouter API（需在 config.json 中配置）调用 LLM 生成文案
3. 生成的文案会参考样本风格，但内容基于 `template_variables` 中的产品信息

### 样本文件配对规则

- 标题和文案文件按文件名匹配配对
- 例如：`01_溪木源山茶花洁面泡沫.j2`（标题）配对 `01_溪木源山茶花洁面泡沫.j2`（文案）

### 禁用文案生成

如果不需要文案生成，可以设置：

```json
{
  "text_generation": {
    "enabled": false
  }
}
```

或者不配置 `openrouter` 部分，系统会自动跳过文案生成。

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

### 内置上下文变量

除了自定义变量，模板中还可以使用以下内置变量：

| 变量 | 说明 |
|------|------|
| `group_index` | 当前组索引（从 0 开始） |
| `group_num` | 当前组编号（从 1 开始） |
| `image_index` | 当前图片索引（从 0 开始） |
| `image_num` | 当前图片编号（从 1 开始） |
| `product_count` | 产品图数量 |
| `reference_count` | 参考图数量 |
| `total_groups` | 总组数 |
| `mode` | 当前生成模式 |

---

## 完整配置示例

### 通用模板（支持模式切换）

```json
{
  "name": "海洋至尊01",
  "description": "AI图片生成配置",
  "mode": "subject_transfer",
  "group_count": 3,
  "images_per_group": [3, 5],

  "product_images": {
    "source_dir": "产品图/化妆品2",
    "specified_images": [],
    "specified_coverage": 100
  },

  "reference_images": {
    "source_dir": "参考图/小红书家用场景参考图",
    "specified_images": []
  },

  "scene_prompts": {
    "source_dir": "Prompt/图片生成/场景生成",
    "specified_prompts": [],
    "custom_template": null
  },

  "transfer_prompts": {
    "source_dir": "Prompt/图片生成/主体迁移",
    "specified_prompt": null,
    "custom_template": null
  },

  "output": {
    "base_dir": "./outputs",
    "aspect_ratio": "4:5",
    "resolution": "2K",
    "format": "png",
    "max_concurrent_groups": 3,
    "generate_text": true
  },

  "text_generation": {
    "enabled": true,
    "title_prompts_dir": "Prompt/文案生成/标题",
    "content_prompts_dir": "Prompt/文案生成/文案",
    "max_few_shot_examples": 5
  },

  "template_variables": {
    "product_name": "海洋至尊护肤套装",
    "brand": "海洋至尊",
    "style": "清新自然",
    "target_audience": "年轻女性",
    "features": "保湿滋润、温和不刺激"
  }
}
```

只需修改 `mode` 字段，即可在场景生成和主体迁移模式之间切换。

---

## 命令行使用

```bash
# 使用默认模板执行
python -m ai_image_generator

# 指定模板配置
python -m ai_image_generator -t templates/scene_generation_template.json

# 验证配置（不执行生成）
python -m ai_image_generator -t templates/generation_template.json --dry-run

# 执行生成（跳过确认提示）
python -m ai_image_generator -t templates/generation_template.json -y

# 断点续传
python -m ai_image_generator -t templates/generation_template.json --resume outputs/xxx_20260126_143000

# 指定全局配置文件
python -m ai_image_generator -t templates/xxx.json -c config.json

# 覆盖 API 密钥
python -m ai_image_generator -t templates/xxx.json --api-key YOUR_API_KEY

# 设置日志级别
python -m ai_image_generator -t templates/xxx.json --log-level DEBUG
```

### 命令行参数

| 参数 | 简写 | 默认值 | 说明 |
|------|------|--------|------|
| `--template` | `-t` | `templates/generation_template.json` | 模板配置文件路径 |
| `--config` | `-c` | `config.json` | 全局配置文件路径 |
| `--api-key` | - | - | API 密钥（覆盖配置文件） |
| `--dry-run` | - | - | 试运行模式，只验证配置 |
| `--yes` | `-y` | - | 自动确认，跳过提示 |
| `--resume` | - | - | 断点续传，指定运行目录 |
| `--log-level` | - | `INFO` | 日志级别：DEBUG/INFO/WARNING/ERROR |

---

## 全局配置文件 (config.json)

```json
{
  "image_service": "kieai",

  "kieai": {
    "api_key": "your-kieai-api-key",
    "base_url": "https://api.kie.ai/api/v1",
    "model": "nano-banana-pro",
    "poll_interval": 2.0,
    "max_wait_seconds": 1500.0
  },

  "openrouter_image": {
    "model": "google/gemini-2.5-flash-preview-05-20"
  },

  "moss": {
    "base_url": "https://your-moss-endpoint",
    "access_key_id": "your-access-key-id",
    "access_key_secret": "your-access-key-secret",
    "bucket_name": "your-bucket-name",
    "expire_seconds": 86400
  },

  "openrouter": {
    "api_key": "your-openrouter-api-key",
    "base_url": "https://openrouter.ai/api/v1",
    "site_url": "https://your-site.com",
    "site_name": "Your-Site-Name",
    "model": "google/gemini-3-flash-preview"
  }
}
```

### image_service 图片生成服务选择

| 值 | 说明 |
|----|------|
| `kieai` | 使用 KieAI 服务（默认），异步任务模式 |
| `openrouter` | 使用 OpenRouter 服务，同步生成模式 |

切换服务只需修改 `image_service` 字段，方便测试不同服务的并发和速度。

### openrouter_image 配置

OpenRouter 图片生成的独立配置。如果不配置，会复用 `openrouter` 中的 `api_key`。

| 字段 | 说明 |
|------|------|
| `model` | 图片生成模型，默认 `google/gemini-2.5-flash-preview-05-20` |
| `api_key` | API 密钥（可选，默认复用 openrouter.api_key） |
| `base_url` | API 地址（可选，默认复用 openrouter.base_url） |
| `site_url` | 站点 URL（可选） |
| `site_name` | 站点名称（可选） |

### 环境变量支持

以下配置项支持通过环境变量覆盖：

| 环境变量 | 对应配置 |
|----------|----------|
| `KIEAI_API_KEY` | `kieai.api_key` |
| `MOSS_BASE_URL` | `moss.base_url` |
| `MOSS_ACCESS_KEY_ID` | `moss.access_key_id` |
| `MOSS_ACCESS_KEY_SECRET` | `moss.access_key_secret` |
| `MOSS_BUCKET_NAME` | `moss.bucket_name` |
| `OPENROUTER_API_KEY` | `openrouter.api_key` |
| `OPENROUTER_IMAGE_API_KEY` | `openrouter_image.api_key` |
| `OPENROUTER_BASE_URL` | `openrouter.base_url` |
| `OPENROUTER_SITE_URL` | `openrouter.site_url` |
| `OPENROUTER_SITE_NAME` | `openrouter.site_name` |
