import pathlib

path = pathlib.Path(r'C:\Users\Gugugu\Documents\Codex\2026-04-24-review-spec-md-kimi-code-codex\ui\public\index.html')

html = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Luxar</title>
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet" />
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',system-ui,sans-serif;background:#f0f0f3;color:#1c2024;-webkit-font-smoothing:antialiased;height:100vh;overflow:hidden}
#app{display:flex;height:100vh}
#sidebar{width:260px;min-width:260px;background:#fff;border-right:1px solid #e0e1e6;display:flex;flex-direction:column;height:100vh}
#sidebar .logo{padding:20px 20px 12px;font-weight:800;font-size:20px;letter-spacing:-0.5px;color:#1c2024}
#sidebar .logo span{color:#60646c;font-weight:400}
.sidebar-section{padding:8px 12px}
.sidebar-section .label{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.5px;color:#60646c;padding:4px 8px;margin-bottom:2px}
.sidebar-project{cursor:pointer;padding:6px 8px;border-radius:8px;font-size:13px;color:#1c2024;transition:background .15s;display:flex;align-items:center;gap:8px}
.sidebar-project:hover{background:#f0f0f3}
.sidebar-project .dot{width:6px;height:6px;border-radius:50%;background:#b0b4ba;flex-shrink:0}
.sidebar-project.active .dot{background:#0d74ce}
.sidebar-project.active{font-weight:600}
.sidebar-project .proj-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#sidebar-projects{flex:1;overflow-y:auto;min-height:0}
#sidebar-projects::-webkit-scrollbar{width:4px}
#sidebar-projects::-webkit-scrollbar-thumb{background:#e0e1e6;border-radius:4px}
#sidebar-projects::-webkit-scrollbar-track{background:transparent}
.sidebar-nav{padding:4px 12px}
.nav-item{cursor:pointer;padding:8px;border-radius:8px;font-size:13px;font-weight:500;color:#60646c;transition:all .15s;display:flex;align-items:center;gap:10px}
.nav-item:hover{background:#f0f0f3;color:#1c2024}
.nav-item.active{background:#1c2024;color:#fff}
.nav-item .nav-icon{width:18px;text-align:center;flex-shrink:0;font-size:14px}
#sidebar-bottom{padding:12px;border-top:1px solid #e0e1e6;display:flex;align-items:center;justify-content:space-between}
.api-status{display:flex;align-items:center;gap:6px;font-size:12px;color:#60646c}
.api-status .led{width:8px;height:8px;border-radius:50%;background:#22c55e;flex-shrink:0}
#lang-btn{cursor:pointer;padding:4px 10px;border-radius:9999px;border:1px solid #e0e1e6;background:#fff;font-size:11px;font-weight:600;color:#1c2024;transition:background .15s}
#lang-btn:hover{background:#f0f0f3}
#main{flex:1;display:flex;flex-direction:column;height:100vh;overflow:hidden}
[data-page]{display:none;flex:1;overflow-y:auto;padding:24px 32px}
[data-page].active{display:flex;flex-direction:column}
[data-page].active:not([data-page="chat"]){display:block}
::-webkit-scrollbar{width:6px}
::-webkit-scrollbar-thumb{background:#e0e1e6;border-radius:4px}
::-webkit-scrollbar-track{background:transparent}
#chat-page{height:100%}
#chat-messages{flex:1;overflow-y:auto;padding:16px 0;display:flex;flex-direction:column;gap:16px;min-height:0}
.chat-welcome{text-align:center;padding:40px 20px}
.chat-welcome h2{font-size:24px;font-weight:700;color:#1c2024;margin-bottom:8px}
.chat-welcome p{font-size:14px;color:#60646c;max-width:480px;margin:0 auto}
.chat-bubble{max-width:75%;padding:12px 16px;border-radius:16px;font-size:14px;line-height:1.5;white-space:pre-wrap;word-break:break-word}
.chat-bubble.user{align-self:flex-end;background:#1c2024;color:#fff;border-bottom-right-radius:4px}
.chat-bubble.assistant{align-self:flex-start;background:#fff;color:#1c2024;border:1px solid #e0e1e6;border-bottom-left-radius:4px}
.chat-bubble.system{align-self:center;background:transparent;color:#60646c;font-size:12px;max-width:100%;text-align:center}
#chat-input-area{padding:16px 0;border-top:1px solid #e0e1e6;display:flex;gap:8px;align-items:flex-end;background:#f0f0f3}
#chat-input{flex:1;resize:none;border:1px solid #e0e1e6;border-radius:12px;padding:10px 14px;font-family:'Inter',sans-serif;font-size:14px;outline:none;background:#fff;color:#1c2024;max-height:120px;line-height:1.4;transition:border-color .15s}
#chat-input:focus{border-color:#0d74ce}
#chat-send-btn{cursor:pointer;padding:10px 20px;border-radius:9999px;border:none;background:#1c2024;color:#fff;font-size:13px;font-weight:600;transition:opacity .15s;white-space:nowrap}
#chat-send-btn:hover{opacity:.85}
#chat-send-btn:disabled{opacity:.4;cursor:not-allowed}
.typing-indicator{display:inline-flex;gap:4px;padding:8px 0;align-items:center}
.typing-indicator span{width:6px;height:6px;border-radius:50%;background:#b0b4ba;animation:bounce 1.4s infinite ease-in-out}
.typing-indicator span:nth-child(2){animation-delay:.2s}
.typing-indicator span:nth-child(3){animation-delay:.4s}
@keyframes bounce{0%,80%,100%{transform:translateY(0)}40%{transform:translateY(-6px)}}
.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin-bottom:24px}
.stat-card{background:#fff;border:1px solid #e0e1e6;border-radius:12px;padding:20px}
.stat-card .stat-label{font-size:13px;font-weight:500;color:#60646c;margin-bottom:4px}
.stat-card .stat-value{font-size:28px;font-weight:700;color:#1c2024;letter-spacing:-0.5px}
.card{background:#fff;border:1px solid #e0e1e6;border-radius:12px;padding:24px;margin-bottom:16px}
.card h3{font-size:16px;font-weight:600;margin-bottom:16px;color:#1c2024}
.page-header{margin-bottom:24px}
.page-header h1{font-size:28px;font-weight:800;letter-spacing:-1px;color:#1c2024}
.page-header p{font-size:14px;color:#60646c;margin-top:4px}
.search-input{width:100%;padding:10px 14px;border:1px solid #e0e1e6;border-radius:10px;font-size:14px;outline:none;background:#fff;color:#1c2024;transition:border-color .15s}
.search-input:focus{border-color:#0d74ce}
.form-group{margin-bottom:14px}
.form-group label{display:block;font-size:13px;font-weight:500;color:#60646c;margin-bottom:4px}
.form-group input,.form-group textarea,.form-group select{width:100%;padding:9px 12px;border:1px solid #e0e1e6;border-radius:8px;font-size:13px;outline:none;background:#fff;color:#1c2024;transition:border-color .15s;font-family:'Inter',sans-serif}
.form-group input:focus,.form-group textarea:focus,.form-group select:focus{border-color:#0d74ce}
.form-group textarea{resize:vertical;min-height:60px}
.pipeline-layout{display:grid;grid-template-columns:1fr 1fr;gap:20px;height:calc(100vh - 120px)}
#pl-output{font-family:'JetBrains Mono',monospace;font-size:12px;color:#60646c;background:#f0f0f3;border-radius:8px;padding:12px;white-space:pre-wrap;word-break:break-word;overflow-y:auto;height:100%;margin:0}
.btn-primary{cursor:pointer;padding:10px 24px;border-radius:9999px;border:none;background:#1c2024;color:#fff;font-size:13px;font-weight:600;transition:opacity .15s;display:inline-flex;align-items:center;gap:6px}
.btn-primary:hover{opacity:.85}
.btn-secondary{cursor:pointer;padding:8px 16px;border-radius:9999px;border:1px solid #e0e1e6;background:#fff;color:#60646c;font-size:12px;font-weight:500;transition:background .15s}
.btn-secondary:hover{background:#f0f0f3}
.btn-group{display:flex;gap:8px;margin-top:16px}
.config-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}
.config-section .card{margin-bottom:16px}
input[type="range"]{width:100%;accent-color:#1c2024;height:4px}
.range-labels{display:flex;justify-content:space-between;font-size:11px;color:#b0b4ba;margin-top:2px}
.mono{font-family:'JetBrains Mono',monospace}
.empty-state{color:#b0b4ba;font-size:13px;text-align:center;padding:32px}
.tag{display:inline-block;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:500;margin-right:4px}
.tag-black{background:#1c2024;color:#fff}
.tag-cloud{background:#f0f0f3;color:#60646c}
.tag-green{background:#dcfce7;color:#166534}
</style>
</head>
<body>
<div id="app">

<!-- Sidebar -->
<div id="sidebar">
  <div class="logo">Board<span>Smith</span></div>
  <div class="sidebar-section">
    <div class="label" data-i18n="sidebar.projects">Projects</div>
    <div id="sidebar-projects"><div class="empty-state" data-i18n="sidebar.loading">Loading...</div></div>
  </div>
  <div class="sidebar-nav">
    <div class="nav-item active" data-page-link="chat" onclick="navigate('chat')"><span class="nav-icon">&#x1F4AC;</span><span data-i18n="nav.chat">Chat</span></div>
    <div class="nav-item" data-page-link="dashboard" onclick="navigate('dashboard')"><span class="nav-icon">&#x1F4CA;</span><span data-i18n="nav.dashboard">Dashboard</span></div>
    <div class="nav-item" data-page-link="drivers" onclick="navigate('drivers')"><span class="nav-icon">&#x1F50C;</span><span data-i18n="nav.drivers">Driver Library</span></div>
    <div class="nav-item" data-page-link="skills" onclick="navigate('skills')"><span class="nav-icon">&#x1F9E0;</span><span data-i18n="nav.skills">Skill Library</span></div>
    <div class="nav-item" data-page-link="pipeline" onclick="navigate('pipeline')"><span class="nav-icon">&#x26A1;</span><span data-i18n="nav.pipeline">Pipeline</span></div>
    <div class="nav-item" data-page-link="model-config" onclick="navigate('model-config')"><span class="nav-icon">&#x2699;</span><span data-i18n="nav.modelConfig">Model Config</span></div>
  </div>
  <div id="sidebar-bottom">
    <div class="api-status"><span class="led"></span><span data-i18n="nav.apiConnected">Connected</span></div>
    <button id="lang-btn" onclick="toggleLang()">&#x4E2D;</button>
  </div>
</div>

<!-- Main Content -->
<div id="main">

<!-- Chat Page -->
<div data-page="chat" id="chat-page" class="active">
  <div id="chat-header" style="padding:8px 0 12px;border-bottom:1px solid #e0e1e6;flex-shrink:0">
    <span style="font-size:12px;color:#60646c" data-i18n="chat.activeProject">Active Project:</span>
    <span id="chat-project-name" style="font-weight:600;font-size:14px;color:#1c2024;margin-left:4px">-</span>
  </div>
  <div id="chat-messages">
    <div class="chat-welcome">
      <h2 data-i18n="chat.welcomeTitle">Welcome to Luxar</h2>
      <p data-i18n="chat.welcomeDesc">Select a project from the sidebar and start a conversation about embedded development, code review, driver generation, and more.</p>
    </div>
  </div>
  <div id="chat-input-area">
    <textarea id="chat-input" rows="1" data-i18n-placeholder="chat.inputPlaceholder" placeholder="Type a message..." oninput="autoResize(this)"></textarea>
    <button id="chat-send-btn" onclick="sendMessage()" data-i18n="chat.sendBtn">Send</button>
  </div>
</div>

<!-- Dashboard Page -->
<div data-page="dashboard">
  <div class="page-header">
    <h1>Luxar</h1>
    <p data-i18n="dashboard.subtitle">STM32-first embedded AI engineering toolkit.</p>
  </div>
  <div class="stats-grid">
    <div class="stat-card"><div class="stat-label" data-i18n="dashboard.statProjects">Projects</div><div id="stat-projects" class="stat-value">-</div></div>
    <div class="stat-card"><div class="stat-label" data-i18n="dashboard.statDrivers">Drivers</div><div id="stat-drivers" class="stat-value">-</div></div>
    <div class="stat-card"><div class="stat-label" data-i18n="dashboard.statSkills">Skills</div><div id="stat-skills" class="stat-value">-</div></div>
    <div class="stat-card"><div class="stat-label" data-i18n="dashboard.statToolchains">Toolchains</div><div id="stat-toolchains" class="stat-value">-</div></div>
  </div>
  <div class="card">
    <h3 data-i18n="dashboard.configTitle">Configuration</h3>
    <div id="config-summary" style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:13px">
      <div><span style="color:#60646c" data-i18n="dashboard.configProvider">Provider</span>: <span id="cfg-provider" class="mono" style="font-weight:600">-</span></div>
      <div><span style="color:#60646c" data-i18n="dashboard.configModel">Model</span>: <span id="cfg-model" class="mono" style="font-weight:600">-</span></div>
      <div><span style="color:#60646c" data-i18n="dashboard.configPlatform">Platform</span>: <span id="cfg-platform" style="font-weight:600">-</span></div>
      <div><span style="color:#60646c" data-i18n="dashboard.configRuntime">Runtime</span>: <span id="cfg-runtime" style="font-weight:600">-</span></div>
      <div style="grid-column:span 2"><span style="color:#60646c" data-i18n="dashboard.configWorkspace">Workspace</span>: <span id="cfg-workspace" class="mono" style="font-size:12px">-</span></div>
    </div>
  </div>
  <div class="card">
    <h3 data-i18n="dashboard.recentProjects">Projects</h3>
    <div id="dash-project-list"><div class="empty-state" data-i18n="dashboard.loadingProjects">Loading...</div></div>
  </div>
</div>

<!-- Drivers Page -->
<div data-page="drivers">
  <div class="page-header">
    <h1 data-i18n="drivers.title">Driver Library</h1>
    <p data-i18n="drivers.subtitle">Browse, search, and manage the local driver library.</p>
  </div>
  <input class="search-input" id="driver-search" type="text" data-i18n-placeholder="drivers.searchPlaceholder" placeholder="Search drivers..." oninput="searchDrivers()" style="margin-bottom:16px" />
  <div id="driver-list"></div>
</div>

<!-- Skills Page -->
<div data-page="skills">
  <div class="page-header">
    <h1 data-i18n="skills.title">Skill Library</h1>
    <p data-i18n="skills.subtitle">Protocol skills extracted from successful driver workflows.</p>
  </div>
  <div id="skill-list" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px"></div>
</div>

<!-- Pipeline Page -->
<div data-page="pipeline">
  <div class="page-header">
    <h1 data-i18n="pipeline.title">Pipeline Runner</h1>
    <p data-i18n="pipeline.subtitle">Generate, review, and fix embedded drivers.</p>
  </div>
  <div class="pipeline-layout">
    <div class="card">
      <h3 data-i18n="dashboard.configTitle">Configuration</h3>
      <div style="flex:1;overflow-y:auto">
        <div class="form-group"><label data-i18n="pipeline.chip">Chip</label><input id="pl-chip" value="STM32F103C8T6" /></div>
        <div class="form-group"><label data-i18n="pipeline.interface">Interface</label><input id="pl-interface" value="SPI" /></div>
        <div class="form-group"><label data-i18n="pipeline.docSummary">Doc Summary</label><textarea id="pl-doc" rows="3" data-i18n-placeholder="pipeline.docPlaceholder" placeholder="SPI master driver for STM32F103C8T6"></textarea></div>
        <div class="form-group"><label data-i18n="pipeline.maxIter">Max Fix Iterations</label><input id="pl-iter" type="number" value="3" /></div>
        <button class="btn-primary" onclick="runPipeline()" style="width:100%;justify-content:center;margin-top:8px"><span data-i18n="pipeline.runBtn">Run Pipeline</span></button>
      </div>
    </div>
    <div class="card" style="display:flex;flex-direction:column">
      <h3 data-i18n="pipeline.outputTitle">Output</h3>
      <pre id="pl-output" style="flex:1">Output will appear here...</pre>
    </div>
  </div>
</div>

<!-- Model Config Page -->
<div data-page="model-config">
  <div class="page-header">
    <h1 data-i18n="modelConfig.title">Model Configuration</h1>
    <p data-i18n="modelConfig.subtitle">Configure LLM provider, model, and API settings.</p>
  </div>
  <div class="config-grid">
    <div class="config-section">
      <div class="card">
        <h3 data-i18n="modelConfig.provider">Provider</h3>
        <div class="form-group">
          <label data-i18n="modelConfig.selectProvider">LLM Provider</label>
          <select id="mc-provider">
            <option value="deepseek">DeepSeek</option>
            <option value="openai">OpenAI</option>
            <option value="minimax">MiniMax</option>
            <option value="ollama">Ollama</option>
            <option value="custom">Custom</option>
          </select>
        </div>
        <div class="form-group"><label data-i18n="modelConfig.modelName">Model Name</label><input id="mc-model" value="deepseek-chat" /></div>
        <div class="form-group"><label data-i18n="modelConfig.baseUrl">Base URL</label><input id="mc-base-url" value="" placeholder="https://api.openai.com/v1" /></div>
        <div class="form-group"><label data-i18n="modelConfig.apiKeyEnv">API Key Env Var</label><input id="mc-api-key-env" value="" placeholder="DEEPSEEK_API_KEY" /></div>
      </div>
      <div class="card">
        <h3 data-i18n="modelConfig.advanced">Advanced</h3>
        <div class="form-group">
          <label>Temperature (<span id="mc-temp-val">0.2</span>)</label>
          <input id="mc-temperature" type="range" min="0" max="2" step="0.05" value="0.2" oninput="document.getElementById('mc-temp-val').textContent=this.value" />
          <div class="range-labels"><span>0</span><span>1</span><span>2</span></div>
        </div>
        <div class="form-group"><label data-i18n="modelConfig.maxTokens">Max Tokens</label><input id="mc-max-tokens" type="number" value="4096" /></div>
        <div class="form-group"><label data-i18n="modelConfig.timeout">Timeout (sec)</label><input id="mc-timeout" type="number" value="60" /></div>
      </div>
    </div>
    <div class="config-section">
      <div class="card">
        <h3 data-i18n="modelConfig.retry">Retry Configuration</h3>
        <div class="form-group"><label data-i18n="modelConfig.retryAttempts">Retry Attempts</label><input id="mc-retry-attempts" type="number" value="3" min="0" max="10" /></div>
        <div class="form-group"><label data-i18n="modelConfig.retryMinDelay">Min Delay (sec)</label><input id="mc-retry-min-delay" type="number" value="2" min="0" step="0.5" /></div>
        <div class="form-group"><label data-i18n="modelConfig.retryMaxDelay">Max Delay (sec)</label><input id="mc-retry-max-delay" type="number" value="30" min="0" step="0.5" /></div>
      </div>
      <div class="btn-group">
        <button class="btn-primary" onclick="saveModelConfig()" style="flex:1;justify-content:center"><span data-i18n="modelConfig.saveBtn">Save Config</span></button>
        <button class="btn-secondary" onclick="resetModelConfig()"><span data-i18n="modelConfig.resetBtn">Reset</span></button>
      </div>
      <div id="mc-status" style="margin-top:8px;font-size:13px;color:#60646c"></div>
    </div>
  </div>
</div>

</div><!-- /#main -->
</div><!-- /#app -->

<script>
'''

print(f"HTML length: {len(html)}")
path.write_text(html, encoding="utf-8")
print("Written successfully")

