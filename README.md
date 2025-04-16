

<div align="center">

🔥  
###  火天大有 For Home Assistant

![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.8.0-blue.svg?color=red)
![Latest Release](https://img.shields.io/github/v/release/knoop7/HuoTianDaYou?color=red)

聚合多种AI能力，打造真正聪明的智能家居对话体验！

[安装](#安装步骤) • [主要功能](#主要功能特点) • [注意事项](#注意事项)

[OpenAI](https://openai.com/) \ [Gemini](https://gemini.google.com/) \ [Claude](https://www.anthropic.com/) \ [智谱清言](https://open.bigmodel.cn/) \ 
[通义千问](https://tongyi.aliyun.com/) \ [百川](https://www.baichuan-ai.com/) \ [月之暗面](https://moonshot.cn/) \ [MiniMax](https://www.minimax.chat/)  
[讯飞星火](https://xinghuo.xfyun.cn/) \  [零一万物](https://yi.01.ai/) \  [DeepSeek](https://deepseek.com/) \  [Ollama](https://ollama.ai/) \  [LLAMA](https://ai.meta.com/llama/) \  [Mistral](https://mistral.ai/) \  [Groq](https://groq.com/) 
</div>


### | 集 成 的 自 我 介 绍 |

#####  **火天大有 · 聚合AI** 是一个为 Home Assistant 打造的智能 AI 管理组件，专注于多代理协同、智能提示生成与语义增强交互。它不仅支持多种主流 AI 模型接入，还能实现自动代理切换、联网搜索、响应总结与上下文感知提示词动态生成，为你的家庭自动化系统注入真正“能听懂人话”的超级能力。

系统采用模块化设计，主要组件包括：

| 组件名称 | 功能简介 | 核心职责 |
|:--|:--|:--|
| 🔷 **PromptManager**<br/>提示词管理器 | 智能识别用户意图<br/>生成定制提示词<br/>分析搜索结果提取关键信息 | 提供精准、上下文相关的提示词，优化对话输入 |
| 🟩 **ContentProcessor**<br/>内容处理器 | 网页净化去噪<br/>提取结构化数据<br/>输出统一格式供代理处理 | 为 AI 提供高质量、易理解的上下文内容 |
| 🟦 **AIManager**<br/>AI 管理器 | 多代理调用管理<br/>支持串行执行与失败回退<br/>响应模式可配置（简洁 / 代理名 / 详细） | 管理代理协作流程，保障高可用与统一响应格式 |
| 🟪 **WebSearch**<br/>联网搜索模块 | 支持 Google / Bing / 百度 等搜索引擎<br/>搜索结果预处理<br/>可作为独立联网模块调用 | 提供准确、实时的搜索结果供内容处理与 AI 使用 |
| 🟥 **FallbackAgent**<br/>对话入口代理 | 整合提示词、搜索和代理交互<br/>管理对话流程与回退策略<br/>输出最终响应 | 统一交互入口，确保每次用户提问都有高质量反馈 |


#### 主要功能特点

- ✅ **自动代理切换**：无须手动选择，系统自动判断哪个 AI 最适合回答
- ✅ **AI 响应总结优化**：自动挑选或整合多个代理的回答
- ✅ **联网搜索支持**：结合搜索引擎提取新鲜数据，智能筛选并生成提示
- ✅ **动态提示词生成**：根据上下文自动构建提示，适配不同查询类型
- ✅ **网页结构化内容提取**：让 AI 更精准地理解复杂网页信息
- ✅ **多种交互模式**：灵活切换简洁、标注、分析型交互方式

<br/>

---

<br/>


### 安装步骤

#### ✅ 方法一：通过 HACS 安装（推荐）

1. 打开 Home Assistant 左侧菜单，点击 **HACS**  
2. 进入右上角菜单 → 选择 **“自定义存储库”**  
3. 填入仓库地址：

   ```
   https://github.com/knoop7/HuoTianDaYou
   ```

4. 类型选择 `集成 (Integration)`，点击添加  
5. 返回 HACS 主界面，搜索并安装 **“火天大有”**  
6. 安装完成后，前往 **“设置 → 设备与服务 → 添加集成”**  
7. 搜索 `火天大有`，点击添加并完成配置  

> 💡 安装后即可快速使用，推荐启用推荐属性（名字霸气，不改也行 😎）
  
[![快速通过 HACS 链接安装](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=knoop7&repository=huotiandayoupe&category=integration)

<br/>


#### 🧰 方法二：手动安装（适合进阶用户）

1. 下载项目源代码或 Release 包  
2. 将目录整体复制到 Home Assistant 的路径：

   ```
   config/custom_components/huotiandayou
   ```

3. 重启 Home Assistant  
4. 在 **“设置 → 设备与服务”** 中点击 **添加集成**  
5. 搜索并添加 `火天大有`，按照提示完成配置

<br/>

###  配置提示

- 🔧 支持自定义 AI 接口，推荐先从 OpenAI / Gemini 等官方服务开始  
- ⚙️ 默认配置适用于大多数场景，也支持 YAML 手动配置高级参数  
- 🚀 安装后无需重启即可使用（首次加载建议刷新页面）

<br/>

---

<br/>

### 使用示例

#### 📍天气查询
用户提问：“北京明天天气怎么样？”  
系统处理流程：
- 识别为天气类问题  
- 使用 WebSearch 获取天气数据  
- 抽取温度、风力、湿度等结构化内容  
- 生成优化提示词并调用 AI 返回回答

#### 📍股票查询
用户提问：“阿里巴巴股票现在是多少钱？”  
系统处理流程：
- 分类识别为股票类  
- 获取股票价格、涨跌幅等关键信息  
- 构建股票类提示词模板  
- 使用 AI 回复详细价格分析

<br/>

---

<br/>

### 注意事项

- 最低要求 Home Assistant 2024.8.0
- 建议使用官方大模型接口（如 OpenAI、Claude）
- 如遇实体不显示，尝试重启或重新加载集成
- 控制实体建议精简添加，避免信息冗余
- 排除 Home Assistant 无法识别的非标准实体

<br/>

---

<br/>

<div align="center">

由 [@knoop7](https://github.com/knoop7) 开发维护  
如果你喜欢这个项目，欢迎 Star ⭐️ 和反馈！

</div>

