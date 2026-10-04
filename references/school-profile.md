# 学校 / 学生画像

这是 skill「越用越懂学校、越用越懂学生」的落地方式：把每次写作沉淀下来的、关于这所学校和学生群体的判断，存进画像，下次写之前读一遍校准口吻。

## 一、画像存哪

```
data/profiles/school.json
```

## 二、字段

```json
{
  "school_name": "某高校",
  "school_type": "985 / 211 / 双一流 / 高职 / 地方本科 / 民办",
  "student_profile": "学生群体特征，如：以工科为主、考研比例高、就业压力大、家庭经济差异明显…",
  "common_topics": ["考研", "宿舍矛盾", "心理焦虑", "就业"],
  "sensitive_points": ["奖学金评定", "评优评先", "家校矛盾"],
  "title_style_preference": "这所学校推文惯用/受欢迎的文风（如：口语短标题、悬念式）",
  "updated_at": "2026-09-09"
}
```

## 三、读写约定

```bash
python scripts/kb.py profile get student_profile        # 读某个字段
python scripts/kb.py profile set common_topics '["考研","就业"]'   # 写某个字段（7 键白名单，未知 key 拒绝）
python scripts/kb.py profile dump                        # 打印整个画像
```

> 命令已统一到 `kb.py`（M5 起）；旧 `scripts/profiles.py` 保留为薄封装（转调 kb.py）。可写字段白名单：school_name / school_type / student_profile / common_topics / sensitive_points / title_style_preference；updated_at 由系统托管（ISO8601 带时区）。

## 四、使用纪律

- **交付前读**：写正文前读一遍画像，校准口吻和选题（例如学生普遍焦虑就业，就别写「躺平可耻」这种踩雷选题）。
- **复盘后写**：每次复盘（见 `references/dissemination-review.md`），把新观察到的学生特征 / 敏感点 / 文风偏好回写进画像。
- **画像不是结论，是线索**：它帮你更快进入状态，但不能替代对当次素材的具体分析。
