# tools 目录说明

本目录主要保存 FinSight agent 头像资产相关脚本，包括候选图生成、透明抠图、动画 WebP/GIF 生成、对比图生成和法务助手头像定稿处理。

这些脚本不是前后端运行必需项；它们用于生成或维护 `finall_all_front_0516/front/public/pet/` 下的静态资源。

## 1. 当前前端头像资源

当前主前端头像映射位于：

```text
/home/maoyd/finsight/finall_all_front_0516/front/src/components/agent/AgentAvatar.tsx
```

专业 agent 当前使用：

```text
public/pet/agent-drafts/finsight-analysis-avatar-animated-transparent.webp
public/pet/agent-drafts/finsight-factchecker-avatar-animated-transparent.webp
public/pet/agent-drafts/finsight-tracking-avatar-animated-transparent.webp
public/pet/agent-drafts/finsight-legal-avatar-animated-transparent.webp
```

普通财报助手当前使用：

```text
public/pet/finsight-avatar-animated.webp
```

已确认存档：

```text
/home/maoyd/finsight/agent-avatar-archive-20260520
/home/maoyd/finsight/agent-avatar-archive-20260520.tar.gz
```

## 2. 脚本清单

| 脚本 | 作用 |
| --- | --- |
| `generate_finsight_agent_avatar_drafts.py` | 早期生成 analysis/factchecker/tracking 候选头像草图 |
| `create_analysis_avatar_animation.py` | 为分析助手生成早期动画 |
| `refine_finsight_female_agent_avatars.py` | 早期女性头像细化候选 |
| `create_selected_agent_avatar_animations.py` | 为选中的候选生成动画 |
| `create_high_quality_agent_avatar_animations.py` | 生成更高质量的头像动画 |
| `create_transparent_agent_avatar_animations.py` | 对头像生成透明静态图和透明动画 |
| `generate_finsight_beauty_avatar_candidates.py` | 生成更高颜值版本候选 |
| `generate_remaining_finsight_beauty_candidates.py` | 补生成剩余候选 |
| `finalize_selected_a_agent_avatars.py` | 定稿 selected A 版本头像资源 |
| `rebuild_finsight_agent_avatar_assets.py` | 重建当前专业 agent 头像资源 |
| `make_avatar_alpha_review_sheet.py` | 生成透明通道检查图 |
| `generate_finsight_legal_avatar_candidates.py` | 生成法务助手头像候选 |
| `create_finsight_legal_avatar_assets.py` | 基于选中法务源图生成透明 PNG、动画 WebP/GIF |
| `make_finsight_legal_avatar_version_comparison.py` | 生成法务头像版本对比图 |

## 3. 法务助手头像当前版本

用户确认的当前 UI 法务头像来源链路：

```text
源图:
/home/maoyd/finsight/finall_all_front_0516/front/public/pet/agent-drafts/finsight-legal-avatar-selected-native-checker-source.png

透明静态图:
/home/maoyd/finsight/finall_all_front_0516/front/public/pet/agent-drafts/finsight-legal-avatar-transparent.png

前端动画 WebP:
/home/maoyd/finsight/finall_all_front_0516/front/public/pet/agent-drafts/finsight-legal-avatar-animated-transparent.webp

动画 GIF:
/home/maoyd/finsight/finall_all_front_0516/front/public/pet/agent-drafts/finsight-legal-avatar-animated-transparent.gif

当前 UI 首帧备份:
/home/maoyd/finsight/finall_all_front_0516/front/public/pet/agent-drafts/finsight-legal-avatar-current-webp-frame0.png
```

已归档到：

```text
/home/maoyd/finsight/agent-avatar-archive-20260520/legal/
  legal-avatar-source-native-checker.png
  legal-avatar-transparent.png
  legal-avatar-current-ui-frame0.png
  legal-avatar-animated-transparent.webp
  legal-avatar-animated-transparent.gif
```

## 4. 运行注意

1. 这些脚本通常依赖 Pillow 等图像处理库，部分候选生成脚本可能依赖本机 ComfyUI、OpenAI 图片生成或已有生成图片路径。
2. 运行脚本前先打开脚本顶部的输入/输出路径，确认不会覆盖当前前端满意版本。
3. 如果只是恢复当前确认版头像，优先从 `agent-avatar-archive-20260520` 复制，而不是重新生成。
4. 前端资源路径固定写在 `AgentAvatar.tsx` 和 `PetFairy.tsx` 中；改文件名后要同步修改映射。
5. 生成 WebP/GIF 后建议在浏览器 UI 小尺寸下确认，因为大图检查和 UI 实际观感可能不同。

## 5. 常用操作

### 5.1 备份当前确认版头像

当前已完成备份。若以后要手动复制，可参考：

```bash
cp -a /home/maoyd/finsight/finall_all_front_0516/front/public/pet/agent-drafts/finsight-legal-avatar-animated-transparent.webp \
  /home/maoyd/finsight/agent-avatar-archive-20260520/legal/legal-avatar-animated-transparent.webp
```

### 5.2 恢复法务头像

```bash
cp -a /home/maoyd/finsight/agent-avatar-archive-20260520/legal/legal-avatar-animated-transparent.webp \
  /home/maoyd/finsight/finall_all_front_0516/front/public/pet/agent-drafts/finsight-legal-avatar-animated-transparent.webp
```

同理可恢复 PNG/GIF 源图。

### 5.3 查看归档说明

```bash
sed -n '1,220p' /home/maoyd/finsight/agent-avatar-archive-20260520/README.md
```

