
<div align="center">

🔥  
###  Huo Tian Da You for Home Assistant

![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.8.0-blue.svg?color=red)
![Latest Release](https://img.shields.io/github/v/release/knoop7/HuoTianDaYou?color=red)

Aggregate multiple AI agents to create truly intelligent and seamless smart home conversations!

[Installation](#installation) • [Key Features](#key-features) • [Notes](#important-notes)

[OpenAI](https://openai.com/) \ [Gemini](https://gemini.google.com/) \ [Claude](https://www.anthropic.com/) \ [Zhipu AI](https://open.bigmodel.cn/) \ 
[Tongyi Qianwen](https://tongyi.aliyun.com/) \ [Baichuan](https://www.baichuan-ai.com/) \ [Moonshot](https://moonshot.cn/) \ [MiniMax](https://www.minimax.chat/)  
[iFlytek Spark](https://xinghuo.xfyun.cn/) \ [01.AI](https://yi.01.ai/) \ [DeepSeek](https://deepseek.com/) \ [Ollama](https://ollama.ai/) \ [LLAMA](https://ai.meta.com/llama/) \ [Mistral](https://mistral.ai/) \ [Groq](https://groq.com/)

</div>

---

<br/>

### | Self-Introduction of the Integration |

##### **Huo Tian Da You · AI Aggregator** is an AI management component built for Home Assistant. It focuses on multi-agent orchestration, intelligent prompt generation, and enhanced natural language interaction. With support for various popular AI models, it enables automatic agent switching, web search, response summarization, and context-aware prompts—bringing real conversational intelligence into your smart home.

The system is designed with modular architecture, including:

| Component | Description | Core Responsibilities |
|:--|:--|:--|
| 🔷 **PromptManager**<br/>Prompt Generator | Recognizes user intent<br/>Generates custom prompts<br/>Extracts key info from search results | Provides contextually relevant prompts for improved query accuracy |
| 🟩 **ContentProcessor**<br/>Content Cleaner | Cleans web content from ads/noise<br/>Extracts structured data<br/>Formats data for AI processing | Supplies AIs with clean, readable content for enhanced understanding |
| 🟦 **AIManager**<br/>Agent Controller | Manages multi-agent invocation<br/>Supports sequential execution and fallback<br/>Offers configurable response modes | Ensures reliable, coherent, and high-availability replies |
| 🟪 **WebSearch**<br/>Search Engine Connector | Supports Google, Bing, Baidu, etc.<br/>Preprocesses search results<br/>Callable as standalone tool | Supplies timely and accurate external knowledge |
| 🟥 **FallbackAgent**<br/>Primary Conversation Handler | Integrates prompts, search, and AI agents<br/>Handles dialog flow and fallback strategies<br/>Outputs final responses | Acts as the main entry for consistent and quality conversations |

---

### Key Features

- ✅ **Auto Agent Switching**: No manual selection needed—system picks the best-fit AI for each query
- ✅ **Smart Answer Merging**: Automatically selects or combines responses from multiple AIs
- ✅ **Real-time Web Search**: Integrates live data into AI replies through search engines
- ✅ **Dynamic Prompt Generation**: Creates optimized prompts based on context and query type
- ✅ **Structured Web Extraction**: Helps AIs better understand complex webpage info
- ✅ **Flexible Response Modes**: Toggle between concise, annotated, or analytical modes

---

### Installation

#### ✅ Method 1: Install via HACS (Recommended)

1. Open the Home Assistant sidebar and go to **HACS**
2. Click the upper-right menu → Select **“Custom Repositories”**
3. Enter the repository URL:

   ```
   https://github.com/knoop7/HuoTianDaYou
   ```

4. Choose `Integration` as the category, then click Add  
5. Back in HACS, search and install **“火天大有” (Huo Tian Da You)**  
6. After installation, go to **Settings → Devices & Services → Add Integration**  
7. Search for `火天大有`, click to configure

> 💡 After installation, the integration is ready to use immediately. The default name “Huo Tian Da You” is powerful enough—you don’t have to rename it 😎

[![Install quickly via HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=knoop7&repository=huotiandayoupe&category=integration)

---

#### 🧰 Method 2: Manual Installation (Advanced Users)

1. Download the source code or release package  
2. Copy the folder into the following Home Assistant path:

   ```
   config/custom_components/huotiandayou
   ```

3. Restart Home Assistant  
4. Go to **Settings → Devices & Services → Add Integration**  
5. Search and add `火天大有`, then follow setup instructions

---

### Configuration Tips

- 🔧 Custom AI APIs are supported—OpenAI and Gemini are great starting points  
- ⚙️ Default config suits most users, but YAML support allows advanced customization  
- 🚀 Integration runs without restart (a UI refresh might help for first-time use)

---

### Usage Examples

#### 📍 Weather Inquiry
User asks: “What’s the weather like in Beijing tomorrow?”  
System flow:
- Recognizes this as a weather query  
- Uses WebSearch to fetch weather data  
- Extracts temperature, wind, humidity as structured info  
- Builds optimized prompt and returns AI-generated reply

#### 📍 Stock Inquiry
User asks: “How much is Alibaba stock now?”  
System flow:
- Identifies it as a finance query  
- Fetches real-time stock prices and changes  
- Uses a stock-specific prompt template  
- AI returns detailed price and trend analysis

---

### Important Notes

- Requires Home Assistant 2024.8.0 or higher
- Official APIs like OpenAI or Claude are strongly recommended
- If the integration doesn’t appear, try reloading or restarting HA
- Minimize control entities to keep UI clean and focused
- Avoid unsupported non-standard entities to ensure compatibility

---

<div align="center">

Maintained by [@knoop7](https://github.com/knoop7)  
If you enjoy this project, feel free to leave a ⭐️ and share your feedback!

</div>

