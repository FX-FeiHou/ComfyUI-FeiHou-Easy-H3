# 标准版 v1.4.3 前端冲突修复

标准版不再使用与 ComfyUI-MiniMaxH3-Easy 共用的 `__h3Easy*Installed` 标记；主节点、加载器、Remix 加载器、中转、输出以及媒体监听/画布安装标记使用 `__feihouStandardH3*` 前缀。节点识别只依据真实类型，不凭显示标题或其他插件的原型标记认领节点。

参考模式缺少图片/视频或含 `__MINIMAX_H3_UNRESOLVED_REF_*` 时现在明确停止，避免参考图丢失后静默变成无图生成。纯文生仍可选择图生/首尾帧模式且不接素材。没有更改已保存控件的排列、模型加载、采样算法或上传接口。

安装后需要重启 ComfyUI，并硬刷新浏览器（Ctrl+Shift+R）或关闭原标签页再打开；仅重新排队不能清除已在内存中安装的旧补丁。不要同时保留两份标准版插件在 custom_nodes 下。

本地测试覆盖两种前端补丁顺序的媒体传输模拟、真实类名匹配、重命名节点、RH 节点排除及后端空媒体保护。尚未进行用户环境中的完整 GPU 视频生成验收。RH 及第三方插件的文件未修改。

## English

The standard edition uses private installation markers and exact node-class ownership instead of shared flags or editable titles. Reference mode now fails explicitly on missing visual media or unresolved reference placeholders. Text-only generation remains available in image/first-last-frame mode without media. Restart ComfyUI and fully reload the browser after installation; old in-memory patches cannot be removed by queuing again. RH and third-party files are unchanged. Both wrapper orders are covered by a transport simulation, not an end-to-end GPU test.
