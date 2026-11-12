# RhineLabUI 档案可视化

这是 Counselors-Writing 的本地档案查看器。它从知识库导出案例、选题、来源、审核和风格五类档案，可浏览、搜索和下载单条档案。

在 Counselors-Writing 仓库根目录运行。首次使用需要 Node.js 22.12+。

```bash
npm ci --prefix visualization/rhinelab
python scripts/database_check.py
```

浏览器默认打开 `http://127.0.0.1:5173`。写作或复盘后再运行一次脚本，页面会读取最新档案。使用其他端口时传 `--port <端口>`。

档案导出至 `content/archives.json`。该文件、备份和构建目录均被 Git 忽略。页面仅在本机启动。

界面来自 [LBEILC/RhineLabUI](https://github.com/LBEILC/RhineLabUI)，保留了原作者的三维档案阵列、模型和原创配乐。此副本使用知识库档案作为数据源，允许分类为空或各类档案数量不同。原作者署名与许可见 [LICENSE](LICENSE) 和 [贡献署名](PR15-ATTRIBUTION.md)。原项目对第三方名称、视觉元素和字体的权利说明见 [RhineLabUI 授权说明](https://github.com/LBEILC/RhineLabUI#授权与版权声明)。
