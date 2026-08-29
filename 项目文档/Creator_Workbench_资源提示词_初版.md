# Creator Workbench 资源提示词（项目024）

## 参考图拆解

把第二张图拆成可复用的产品结构，而不是只换颜色：

- 顶部：品牌、日期、通知、设置、菜单。
- 主卡：本月成果总览、大数字、环比标签、近 7 次趋势、完成率环形图、目标进度条。
- 右侧：粉丝增长、作品发布、播放量三张指标卡，每张都包含数字、时间范围和小图形。
- 底部：互动率、收益、待办任务三张业务卡。
- 底栏：发布、上传、分析、消息四个快捷入口。
- 材质：柔和塑料、内高光、浅描边、软阴影、轻微倒角；数字比装饰更醒目。

项目024的 Agent 面板采用同样的信息密度，但只展示真实页面上下文、草稿字数、当前模式、连接状态和编辑次数，不伪造粉丝或收益数据。

## 资源总提示词

```text
Create a polished 3D clay-plastic asset sheet for a Chinese creator workbench and operations assistant.
Soft peach pink base, coral accents, mint green and lavender secondary colors, warm ivory highlights,
rounded beveled geometry, frosted translucent plastic, diffuse studio lighting, subtle ambient occlusion,
soft contact shadows, crisp silhouettes readable at 64px.
Include separate isolated objects with generous spacing: compact camera, clipboard with pencil,
notification bell, settings gear, hamburger menu, publish tray, upload tray, analytics bars,
chat bubble, donut chart token, trend arrow, task check badge, three sparkle stars.
No text, no logos, no watermark, no photorealistic skin, no dark UI, no neon glow, no flat vector look.
Square 1:1 asset sheet, consistent material and lighting.
```

## 3D 小人候选

### A：数据管家（当前 CSS 槽位基准）

```text
Full-body 3D chibi assistant for a desktop content operations workbench, gender-neutral adult,
large friendly head, compact proportions, calm confident expression, coral jacket with mint trim,
ivory utility belt, small translucent data tablet in one hand, rounded sneakers, matte clay-plastic material,
soft studio lighting, subtle contact shadow, 3/4 standing pose, readable silhouette at small size,
isolated on a pale pink background for later cutout, no text, no logo, no watermark, no extra props.
```

### B：创意编辑

```text
Full-body 3D chibi creator assistant, gender-neutral adult, warm expressive eyes, lavender overshirt,
coral scarf, mint headphones around the neck, holding a small clipboard and pencil,
soft clay-plastic toy render, rounded bevels, 3/4 standing pose, clean silhouette, pale pink background,
no text, no logo, no watermark, no extra props.
```

### C：审核员

```text
Full-body 3D chibi review assistant, gender-neutral adult, focused but approachable expression,
mint jacket with coral accents, small translucent shield badge on the chest, holding a compact checklist tablet,
soft clay-plastic material, rounded geometry, gentle studio shadows, 3/4 standing pose, pale pink background,
no text, no logo, no watermark, no extra props.
```

## 把关标准

1. 缩小到 72px 仍能看清头、身体和手中物件。
2. 不做战斗服、复杂办公室场景或品牌 Logo；角色服务于状态表达。
3. 与卡片共用软塑料光照，避免写实皮肤、金属反光和霓虹。
4. 在线、工作、审核、暂停由徽章或设备表达，不靠肤色表达错误。
5. 未经人工确认不替换 CSS 角色槽位；生成后记录尺寸、格式和透明背景处理结果。

## 当前边界

本轮已完成女性 premium 角色、四帧动作精灵和桌面 Agent 面板接入。移动端按用户要求继续隐藏工作台；后续动态视频或更多动作仍需单独确认。

## 候选 A 生成记录

- 通道：工作区已有 `G:\Workspace\_全局工作台\scripts\jojocode-imagegen.ps1` 中转入口，模型为 `gpt-image-2`，质量 `low`，尺寸 `1024x1024`。
- 原始图：`工作文件/app/static/assets/characters/agent-character-a-source.png`，纯绿色背景。
- 透明图：`工作文件/app/static/assets/characters/agent-character-a.png`，RGBA，四角透明；使用工作区 `remove_chroma_key.py` 完成背景移除。
- 机器检查：透明像素 `766631/1048576`，部分透明像素 `41927/1048576`，输出非空且尺寸正确。
- 人工初审：轮廓、服装配色、平板道具和软塑料材质通过；年龄感偏年轻，等待用户确认后才进入动态动作生成。

## 女性 Premium 动作板生成记录

```text
Use case: stylized-concept
Asset type: desktop Agent character sprite sheet
Primary request: a polished full-body female 3D chibi creator-operations assistant with four consistent poses: holding a data tablet, waving, reviewing data on the tablet, and raising a completion badge.
Style/medium: premium clay-plastic 3D toy render, rounded bevels, subtle fabric and translucent-plastic details, soft studio lighting, ambient occlusion, crisp silhouette at small UI size.
Composition/framing: square 2x2 action sheet, one isolated character per quadrant, generous spacing, consistent scale and camera angle.
Constraints: coral utility jumpsuit with mint trim, ivory pouches, lavender accents; no surrounding copy, labels, logos, watermark, extra characters, or decorative text; flat green chroma-key background for local removal.
```

- 原始动作板：`工作文件/app/static/assets/characters/agent-character-female-premium-action-sheet-source.png`。
- 透明动作板：`工作文件/app/static/assets/characters/agent-character-female-premium-action-sheet.png`，RGBA `1024x1024`，透明像素 `821681/1048576`，非透明区域无残留绿色。
- 横向精灵条：`工作文件/app/static/assets/characters/agent-character-female-premium-actions-strip.png`，RGBA `2048x512`，四帧按“平板、挥手、查看数据、完成徽章”排列。
- 桌面接入：`工作文件/app/static/agent-panel.js` 使用横向精灵条；`工作文件/app/static/styles.css` 使用 `400% 100%` 背景切帧并将角色槽位调整为 `96x96`。角色旁说明文案隐藏，真实数据卡和快捷操作保留。
- 裸眼 3D 表现：角色展示区无边框、无底色、无落地椭圆平台；角色使用 `perspective`、`translateZ`、轻微 `rotateY`、非线性悬浮和跟随式投影制造空间层次，避免 PPT 式卡片感。
