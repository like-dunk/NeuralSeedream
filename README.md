**AI 图片生成器**

批量生成产品场景图和主体迁移图的工具。

**快速开始**

**1. 修改模板配置**

编辑 `templates/generation_template.json`：

```json
{
  "name": "我的任务名称",
  "description": "任务描述（可选）",
  "mode": "scene_generation",
  "generation_target": "both",
  "image_model": "nano-banana-pro",
  "group_count": 3,
  "images_per_group": [2, 4],
  "product_images": {
    "source_dir": "产品图/我的产品",
    "specified_images": [],
    "specified_coverage": 50
  },
  "reference_images": {
    "source_dir": "参考图/我的参考图",
    "specified_images": "",
    "specified_coverage": 100
  },
  "scene_prompts": {
    "source_dir": "prompts/scene_generation.json",
    "specified_prompts": [],
    "custom_template": null
  },
  "transfer_prompts": {
    "source_dir": "prompts/subject_transfer.json",
    "specified_prompt": "background_to_product",
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
    "max_few_shot_examples": 5,
    "tags": ["品牌名", "护肤分享", "好物推荐"]
  },
  "template_variables": {
    "product_name": "产品名称",
    "brand": "品牌名",
    "category": "美妆",
    "style": "清新自然",
    "target_audience": "年轻女性",
    "features": "产品特点描述"
  }
}
```

**2. 运行**

```bash
python3 ai_image_generator.py --dry-run    # 验证配置（推荐先执行）
python3 ai_image_generator.py              # 执行生成
```

默认使用 `templates/generation_template.json` 配置文件。

**两种生成模式**

| 模式 | 说明 |
| --- | --- |
| scene_generation | 场景生成：产品图 + 场景 Prompt → 产品场景图 |
| subject_transfer | 主体迁移：产品图 + 参考背景图 → 产品迁移到背景中 |

**命令行参数**

| 参数 | 说明 |
| --- | --- |
| -t, --template | 模板配置文件路径 |
| -c, --config | 全局配置文件路径，默认 config.json |
| --dry-run | 验证配置，不执行生成 |
| -y, --yes | 跳过确认提示 |
| --resume | 断点续传，指定之前的输出目录 |
| --log-level | 日志级别 DEBUG / INFO / WARNING / ERROR |

**目录结构**

| 路径 | 说明 |
| --- | --- |
| ai_image_generator.py | 主入口 |
| ai_image_generator/ | 核心模块 |
| config.json | API 配置 |
| templates/generation_template.json | 通用模板（推荐） |
| templates/CONFIG_REFERENCE.md | 配置字段说明 |
| prompts/scene_generation.json | 场景生成 Prompt 库 |
| prompts/subject_transfer.json | 主体迁移 Prompt 库 |
| prompts/text_template.j2 | 文案生成模板 |
| 产品图/ | 产品图片 |
| 参考图/ | 参考背景图 |
| outputs/ | 生成结果 |

**输出结构**

| 路径 | 说明 |
| --- | --- |
| outputs/任务名_时间戳/ | 任务输出目录 |
| outputs/任务名_时间戳/001/ | 第1组 |
| outputs/任务名_时间戳/001/image_1.png | 生成的图片 |
| outputs/任务名_时间戳/001/text.txt | 生成的文案 |
| outputs/任务名_时间戳/generation_log.json | 生成日志 |
| outputs/任务名_时间戳/results.json | 结果汇总 |

**配置字段说明**

**基础配置**
| 字段 | 说明 |
| --- | --- |
| name | 任务名称（体现在输出目录名） |
| description | 备注说明（不影响生成） |
| mode | 生成模式 scene_generation / subject_transfer |
| generation_target | 生成目标 both / image_only / text_only，默认 both |
| image_model | 图片生成模型，见下方说明 |
| group_count | 生成多少组 |
| images_per_group | 每组生成多少张，固定值 4 或范围 [min, max] |

**image_model 图片生成模型**
| 值 | 说明 |
| --- | --- |
| nano-banana-pro | KieAI Nano Banana Pro 模型（默认），支持多图输入，适合主体迁移 |
| seedream/4.5-edit | KieAI Seedream 4.5 Edit 模型，图片编辑模型，适合风格转换 |

配置示例：
```json
{
  "image_model": "seedream/4.5-edit"
}
```

注意事项：
- `nano-banana-pro`：支持宽高比 `4:5`、分辨率 `1K/2K`、输出格式 `png/jpg`
- `seedream/4.5-edit`：支持宽高比 `1:1`, `4:3`, `3:4`, `16:9`, `9:16`, `2:3`, `3:2`, `21:9`，不支持 `4:5`（会自动转换为 `3:4` 并提示确认）

**generation_target 生成目标**
用于单独测试图片生成或文案生成功能：
| 值 | 说明 |
| --- | --- |
| both | 同时生成图片和文案（默认） |
| image_only | 仅生成图片，跳过文案生成 |
| text_only | 仅生成文案，跳过图片生成 |

**产品图配置 product_images**
| 字段 | 说明 |
| --- | --- |
| source_dir | 产品图目录（支持递归扫描子文件夹） |
| specified_images | 指定产品图路径列表（数组），每个元素代表每组中必须包含的一张产品图 |
| specified_coverage | 指定图覆盖率（百分比），100 = 所有组都包含这些指定图，50 = 一半的组包含 |

**参考图配置 reference_images**
| 字段 | 说明 |
| --- | --- |
| source_dir | 参考图目录（支持递归扫描子文件夹） |
| specified_images | 指定参考图路径（字符串，只能指定一张），因为每组只能用一张背景参考图 |
| specified_coverage | 指定图覆盖率（百分比），100 = 所有组都用这张图，50 = 一半的组用这张图 |

**场景 Prompt 配置 scene_prompts**
| 字段 | 说明 |
| --- | --- |
| source_dir | Prompt 库文件路径 |
| specified_prompts | 指定 Prompt 的 id 列表 |
| custom_template | 自定义 Prompt 文本（优先使用） |

**主体迁移 Prompt 配置 transfer_prompts**
| 字段 | 说明 |
| --- | --- |
| source_dir | Prompt 库文件路径 |
| specified_prompt | 指定 Prompt 的 id（所有组共用） |
| custom_template | 自定义 Prompt 文本（优先使用） |

**输出配置 output**
| 字段 | 说明 |
| --- | --- |
| base_dir | 输出根目录 |
| aspect_ratio | 图片宽高比（如 4:5） |
| resolution | 分辨率档位（如 2K） |
| format | 输出格式（如 png） |
| max_concurrent_groups | 并发组数上限 |
| generate_text | 是否生成文案文件 text.txt |

**文案生成配置 text_generation**
| 字段 | 说明 |
| --- | --- |
| enabled | 是否启用文案生成 |
| max_few_shot_examples | few-shot 示例最多读取条数 |
| tags | 文案末尾追加的标签列表 |

**模板变量 template_variables**
| 字段 | 说明 |
| --- | --- |
| product_name | 产品名称 |
| brand | 品牌名 |
| category | 产品类目（用于选择参考文案：文案库/{category}产品参考.json） |
| style | 风格 |
| target_audience | 目标人群 |
| features | 产品特点 |

**核心逻辑**
| 规则 | 说明 |
| --- | --- |
| 目录递归扫描 | source_dir 会递归扫描所有子文件夹中的图片 |
| 产品图组内不重复 | 同一组内的产品图不会重复选择 |
| 产品图组间可重复 | 不同组之间可以使用相同的产品图 |
| 参考图组间不重复 | 主体迁移模式下，每组使用不同的参考图作为背景（参考图用完后才会复用） |
| 参考图组内共用 | 主体迁移模式下，同一组内所有产品图共用同一张背景参考图 |
| 指定图片优先 | specified_images 中的图片会优先使用 |
| 并发执行 | 支持多组同时生成，通过 max_concurrent_groups 控制 |

**specified_images 区别**
| 配置 | 类型 | 说明 |
| --- | --- | --- |
| product_images.specified_images | 数组 | 每个元素是一张产品图路径，这些图会在每组中优先使用 |
| reference_images.specified_images | 字符串 | 只能指定一张参考图路径，根据 coverage 决定多少组使用这张图 |

**主体迁移模式示例**
```
组1: 产品图A + 背景1, 产品图B + 背景1, 产品图C + 背景1  (同组共用背景1)
组2: 产品图D + 背景2, 产品图E + 背景2, 产品图F + 背景2  (同组共用背景2)
组3: 产品图G + 背景3, 产品图H + 背景3, 产品图I + 背景3  (同组共用背景3)
```

**常用命令**

| 命令 | 说明 |
| --- | --- |
| python3 ai_image_generator.py --dry-run | 验证配置 |
| python3 ai_image_generator.py -t templates/scene_generation_template.json -y | 场景生成 |
| python3 ai_image_generator.py -t templates/subject_transfer_template.json -y | 主体迁移 |
| python3 ai_image_generator.py --resume outputs/xxx_20260126_143000 | 断点续传 |
| python3 ai_image_generator.py --log-level DEBUG | 调试模式 |

**文案生成配置**

文案生成功能会根据产品信息和参考文案自动生成小红书风格的种草文案。

**1. 参考文案库**

文件路径：`文案库/{category}产品参考.json`

系统会根据 `template_variables.category` 自动选择对应的参考文案文件。例如 category 为 "美妆" 时，会读取 `文案库/美妆产品参考.json`。

文件格式：

```json
[
    {
        "title": "文案标题（15-30字）",
        "text": "文案正文内容（200-500字）..."
    },
    {
        "title": "另一篇文案标题",
        "text": "另一篇文案正文..."
    }
]
```

| 字段 | 说明 |
| --- | --- |
| title | 小红书标题，用于 few-shot 示例 |
| text | 小红书正文，包含标签，用于 few-shot 示例 |

添加新类目：创建 `文案库/{新类目}产品参考.json`，并在模板中设置 `category` 为对应类目名。

**2. 文案生成模板**

文件路径：`prompts/text_template.j2`

这是 Jinja2 模板文件，定义了 AI 生成文案时的 Prompt 结构。

可用变量：

| 变量 | 来源 |
| --- | --- |
| product_name | template_variables.product_name |
| brand | template_variables.brand |
| category | template_variables.category |
| style | template_variables.style |
| features | template_variables.features |
| target_audience | template_variables.target_audience |
| reference_examples | 从文案库读取的参考文案列表 |

修改建议：
- 调整标题/正文的字数要求
- 修改创作风格指引
- 添加或删除禁止事项
- 调整输出格式要求
