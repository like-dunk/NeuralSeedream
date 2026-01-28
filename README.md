# AI图片生成器

批量生成产品场景图和主体迁移图的工具。

### 修改模板配置

编辑 `templates/generation_template.json`：

```json
{
  "name": "我的任务名称",
  "mode": "scene_generation",
  "group_count": 3,
  "images_per_group": [2, 4],
  "product_images": {
    "source_dir": "产品图/我的产品"
  },
  ...
}
```

- `mode`: 切换模式
  - `scene_generation` - 场景生成
  - `subject_transfer` - 主体迁移
- `group_count`: 生成几组
- `images_per_group`: 每组生成几张，可以是固定值 `4` 或范围 `[2, 4]`

### 运行

```bash
# 验证配置（推荐先执行）
python3 ai_image_generator.py --dry-run

# 执行生成
python3 ai_image_generator.py

# 跳过确认提示
python3 ai_image_generator.py -y
```

默认使用 `templates/generation_template.json` 配置文件。

## 两种生成模式

### 场景生成 (scene_generation)

产品图 + 场景 Prompt → 产品场景图

```bash
python3 ai_image_generator.py -t templates/scene_generation_template.json
```

### 主体迁移 (subject_transfer)

产品图 + 参考背景图 → 产品迁移到背景中

```bash
python3 ai_image_generator.py -t templates/subject_transfer_template.json
```

## 命令行参数

| 参数 | 说明 |
|------|------|
| `-t, --template` | 模板配置文件路径（必填） |
| `-c, --config` | 全局配置文件路径，默认 `config.json` |
| `--dry-run` | 验证配置，不执行生成 |
| `-y, --yes` | 跳过确认提示 |
| `--resume` | 断点续传，指定之前的输出目录 |
| `--log-level` | 日志级别：DEBUG, INFO, WARNING, ERROR |

## 常用命令

```bash
# 验证配置
python3 ai_image_generator.py -t templates/generation_template.json --dry-run

# 场景生成
python3 ai_image_generator.py -t templates/scene_generation_template.json -y

# 主体迁移
python3 ai_image_generator.py -t templates/subject_transfer_template.json -y

# 断点续传
python3 ai_image_generator.py -t templates/generation_template.json --resume outputs/xxx_20260126_143000

# 调试模式
python3 ai_image_generator.py -t templates/generation_template.json --log-level DEBUG
```

## 目录结构

```
├── ai_image_generator.py          # 主入口
├── ai_image_generator/            # 核心模块
├── config.json                    # API 配置
├── templates/
│   ├── generation_template.json   # 通用模板（推荐）
│   ├── scene_generation_template.json
│   ├── subject_transfer_template.json
│   └── CONFIG_REFERENCE.md        # 配置字段说明
├── Prompt/图片生成/
│   ├── 场景生成/*.j2              # 场景生成 Prompt
│   └── 主体迁移/*.j2              # 主体迁移 Prompt
├── 产品图/                        # 产品图片
├── 参考图/                        # 参考背景图
└── outputs/                       # 生成结果
```

## 输出结构

```
outputs/
└── 任务名_20260126_143000/
    ├── group_1/
    │   ├── image_1.png
    │   ├── image_2.png
    │   └── ...
    ├── group_2/
    ├── generation_log.json
    └── results.json
```

## 配置说明

### generation_template.json 字段对照

- **name**：任务名称（会体现在输出目录名里）
- **description**：备注说明（不影响生成）
- **mode**：生成模式
  - `scene_generation`：场景生成
  - `subject_transfer`：主体迁移
- **group_count**：生成多少组（group）
- **images_per_group**：每组生成多少张（固定值 `4` 或范围 `[min, max]`）

- **product_images.source_dir**：产品图目录（主体图来源）
- **product_images.specified_images**：指定产品图（写路径列表，优先/强制使用）
- **product_images.specified_coverage**：指定产品图覆盖率（百分比，`100`=全用指定）

- **reference_images.source_dir**：参考图目录（常用于主体迁移的背景参考）
- **reference_images.specified_images**：指定参考图（写路径列表）

- **scene_prompts.source_dir**：场景生成 prompt 库目录（读取该目录的 `prompts.json`）
- **scene_prompts.specified_prompts**：指定场景 prompt 的 `id` 列表（按顺序占用前 N 个 group）
- **scene_prompts.custom_template**：自定义场景 prompt 文本（填了就优先用）

- **transfer_prompts.source_dir**：主体迁移 prompt 库目录（读取该目录的 `prompts.json`）
- **transfer_prompts.specified_prompt**：指定主体迁移 prompt 的 `id`（所有 group 共用同一个）
- **transfer_prompts.custom_template**：自定义主体迁移 prompt 文本（填了就优先用）

- **output.base_dir**：输出根目录
- **output.aspect_ratio**：图片宽高比（如 `4:5`）
- **output.resolution**：分辨率档位（如 `2K`）
- **output.format**：输出格式（如 `png`）
- **output.max_concurrent_groups**：group 并发数上限
- **output.generate_text**：是否输出文案文件 `text.txt`

- **text_generation.enabled**：是否启用文案生成
- **text_generation.title_prompts_dir**：标题 few-shot 示例目录
- **text_generation.content_prompts_dir**：正文 few-shot 示例目录
- **text_generation.max_few_shot_examples**：few-shot 示例最多读取条数
- **text_generation.tags**：最终文案末尾追加的 `#标签`（用你的配置替换 AI 自己生成的标签）

- **template_variables.product_name / brand / style / target_audience / features**：文案生成用的产品信息与风格字段
- **template_variables.category**：产品类目（影响文案模板展示，并用于选择参考文案文件：`文案库/{category}产品参考.json`）

详见 [templates/CONFIG_REFERENCE.md](templates/CONFIG_REFERENCE.md)

## 核心逻辑

- **组内不重复**：同一组内的图片/Prompt 不会重复选择
- **组间可重复**：不同组之间可以使用相同的图片/Prompt
- **指定图片优先**：`specified_images` 中的图片会在每组优先使用
- **并发执行**：支持多组同时生成，通过 `max_concurrent_groups` 控制
