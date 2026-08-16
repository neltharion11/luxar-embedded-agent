# 12 · 全流程切片踩坑记录(问题 → 定位 → 解决 → 沉淀)

> 本篇是 S1–S6 开发过程的完整排障日志。每个问题按
> **现象 → 定位 → 解决 → 沉淀**四步记录,"沉淀"一栏指向固化修复的
> 文件,保证教训不会只活在一次对话里。

## 1. 环境与工具链

### 1.1 Git 无法直连 GitHub

- **现象**:`git clone` 报 `schannel: SEC_E_NO_CREDENTIALS`,换 OpenSSL 后端后报
  `Connection was reset`。
- **定位**:`Test-NetConnection github.com -Port 443` 失败——直连被网络阻断;
  schannel 错误是本机 TLS 凭据问题,与本仓库全局配置
  `http.sslbackend=schannel` 叠加。
- **解决**:探测到本机 Clash Verge 代理端口 7897,用
  `git -c http.sslbackend=openssl -c http.proxy=http://127.0.0.1:7897`
  克隆;首次克隆超时后改用 `git fetch` 断点续传。
- **沉淀**:代理与 SSL 后端写入仓库本地 `.git/config`,后续 git 操作零参数。

### 1.2 沙箱里 `os.mkdir(0o700)` 建出的目录无法访问

- **现象**:`pip ensurepip`、`tempfile.mkdtemp`、pytest 临时目录全部报
  `PermissionError: [WinError 5]`。
- **定位**:逐层复现——普通 `os.makedirs` 正常,`os.mkdir(p, 0o700)` 后
  `scandir/rmtree` 即失败。沙箱过滤器把 POSIX mode 参数变成了真实的
  限制 ACL,连创建者进程都进不去(真 Windows 上 mode 参数本应被忽略)。
- **解决**:在 `F:\LUXAR\.site-tools\sitecustomize.py` 里 monkey-patch
  `os.mkdir` 丢弃 mode 参数,通过 `PYTHONPATH` 加载——不动仓库源码。
- **沉淀**:`.site-tools/sitecustomize.py`(已 gitignore)+ 本 README 开发命令
  中的 `PYTHONPATH` 前缀。

### 1.3 沙箱禁写 conda 环境目录,pyserial 装不进去

- **现象**:`pip install pyserial` 报
  `Permission denied: C:\Users\41562\AppData\Roaming\Python\Python312`。
- **定位**:workspace-write 沙箱只允许写 `F:\LUXAR`,conda 环境在 C: 盘。
- **解决**:`pip install --target F:\LUXAR\.site-tools pyserial`——纯 Python 包,
  装上 `PYTHONPATH` 即生效。
- **沉淀**:`pyproject.toml` 正常声明依赖;`.site-tools` 只是本机安装位置。

### 1.4 pip 空转、Python 版本、ensurepip 三连坑

- **现象**:①pip 25 分钟 CPU 100% 但零下载;②venv 建成 Python 3.11;
  ③`python -m venv` 的 ensurepip 失败。
- **定位**:①观察 pip 缓存目录无新文件 → 解析器卡死 → 杀掉后 `-v` 重跑;
  ②项目要求 `>=3.12,<3.13`,anaconda 是 3.11,`py -0p` 找到
  `F:\MiniMaxH3\Python312`;③ensurepip 失败同样是 1.2 的 mkdir 坑。
- **解决**:换 3.12 重建 venv;从 MiniMaxH3 复制 pip/setuptools 进 venv
  绕过 ensurepip;最终直接启用本机现成的 conda 环境
  `C:\Users\41562\.conda\envs\luxar-learning`(已含全部依赖)。
- **沉淀**:README 的开发命令指向 luxar-learning 环境。

### 1.5 PowerShell 批量替换把测试文件写成了 GBK

- **现象**:`Set-Content` 批量改 test_runner.py 后 pytest 报
  `SyntaxError: unterminated string literal`,文件乱码。
- **定位**:PowerShell 5.1 默认 ANSI(中文系统即 GBK)写出,Python 按 UTF-8
  读失败。
- **解决**:`git checkout HEAD --` 恢复后,只用 edit 工具(UTF-8 安全)重做;
  教训:**批量文本替换一律用 Python 脚本并显式指定 encoding**。
- **沉淀**:无(纯操作纪律)。

## 2. ESP-IDF 工具链

### 2.1 "工具链没装"其实是查错了路径

- **现象**:`C:\Espressif` 只有安装器标记,README 里的 Gugugu 路径全不存在。
- **定位**:读 `eim_idf.json`——Espressif 安装器记录了真实位置:
  框架 `F:\esp\v6.0.2\esp-idf`,工具 `F:\Espressif\tools`。
- **解决**:按记录路径验证,`idf.py --version` 输出 `ESP-IDF v6.0.2`。
- **沉淀**:smoke 测试的路径常量与 PROGRESS.md 环境注。

### 2.2 idf.py 是"注册命令"不是可执行文件

- **现象**:激活脚本里 `idf.py` 能用,但 pytest 子进程直接 spawn 报
  `WinError 193: not a valid application`;`shutil.which("idf.py")` 在激活
  环境里却能命中。
- **定位**:激活脚本把 idf.py 注册成 PowerShell 函数;PATH 里的
  `idf.py` 是 Python 脚本,Windows 无 .py 关联时不可直接执行。
- **解决**:launcher 解析改为**已知安装优先**——用绝对路径的
  `F:\Espressif\tools\python\v6.0.2\venv\Scripts\python.exe` +
  `F:\esp\v6.0.2\esp-idf\tools\idf.py`,两者都存在时优先,否则回退 PATH。
- **沉淀**:`tests/smoke/test_device_smoke.py::_resolve_launcher`、
  `tests/smoke/test_espidf_cli.py::_resolve_launcher`。

### 2.3 idf.py 依赖一整套环境变量

- **现象**:smoke 里 idf.py 依次报
  `IDF_PYTHON_ENV_PATH ... doesn't exist`、
  `TypeError: ... os.getenv('ESP_IDF_VERSION') ... got 'NoneType'`、
  `"cmake" must be available on the PATH`。
- **定位**:缺少 `IDF_PYTHON_ENV_PATH`、`ESP_IDF_VERSION`、含工具链的 PATH。
- **解决**:smoke 用 monkeypatch 设置 `IDF_PATH`、`IDF_TOOLS_PATH`、
  `IDF_PYTHON_ENV_PATH`、`ESP_IDF_VERSION`;整次 pytest 在 dot-source
  激活脚本的 PowerShell 会话里启动,让 PATH 继承给所有子进程。
- **沉淀**:`tests/smoke/test_device_smoke.py::_require_smoke` +
  README 的 smoke 运行命令。

### 2.4 `idf.py create-project` 的 `--path` 语义和文档想的不一样

- **现象**:实测发现项目被**直接创建在 `--path` 给出的目录里**,NAME 参数
  只决定主源文件名(`main\blink.c`)——适配器原来传父目录会把项目
  直接铺在父目录里。
- **定位**:`idf.py create-project --help` + 一次性实测。
- **解决**:适配器把 `--path` 改为完整目标目录
  `str(parent_root / project_name)`,cwd 保持父目录;单元测试同步修正。
- **沉淀**:`src/luxar/adapters/espidf_project.py::_run_create` +
  `tests/adapters/test_espidf_project.py`。

### 2.5 中文 Windows 让 idf_monitor 崩溃

- **现象**:监控采集真实抓到了引导日志(`rst:0x1 (POWERON_RESET),boot:`),
  随后 idf_monitor 抛 `UnicodeEncodeError: 'gbk' codec can't encode ...`。
- **定位**:子进程 stdout 默认走中文系统 GBK 编码,ESP32 日志里的字符
  编码不了。
- **解决**:smoke 环境加 `PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8`,
  强制 idf.py 整棵进程树用 UTF-8。
- **沉淀**:`tests/smoke/test_device_smoke.py::_require_smoke`。

### 2.6 沙箱禁止命名管道,构建卡在 cmake

- **现象**:构建进行到 cmake 时
  `PermissionError: [WinError 5]`(asyncio 的 `_winapi.CreateFile`)。
- **定位**:idf.py 用管道捕获 cmake 输出,而沙箱两个受限模式都禁止
  程序打开命名管道——这是文档化的边界,不是代码 bug。
- **解决**:按规则对该命令做一次性提权重试(danger-full-access,经用户
  审批),同一命令再次成功。
- **沉淀**:README 注明 smoke 需在激活的完整环境运行;此为运行环境
  限制,仓库代码无需改动。

## 3. LangGraph 中断语义(0.2 时代的直觉全错)

- **现象**:设计阶段假设 `interrupt()` 会抛 `GraphInterrupt`。
- **定位**:安装 langgraph 1.2.11 后写探针脚本实测:
  ①`invoke()/stream()` 暂停时**不抛异常**,快照里出现内部键
  `__interrupt__`(`Interrupt` 对象元组);②恢复用
  `Command(resume={"approved": bool})` + 相同 `thread_id`;③带
  checkpointer 的图会把 State 序列化进 checkpoint,恢复后 Pydantic
  对象是**重建的新实例**。
- **解决**:Runner 检测 `__interrupt__` 键、剥离出 `ApprovalRequest`、
  返回 `WorkflowRunResult`;集成测试把 `is` 断言改成 `==`。
- **沉淀**:spec §8 的语义记录 + `tests/application/test_approval.py` 锁定。

## 4. 测试自身的坑(教训归测试,不归产品)

- **"SECRET_PATH\slow" 不会被脱敏**:脱敏器只处理真实绝对路径,测试数据
  必须用 `C:\tools\SECRET\slow` 这类真路径,并断言 `<external-path>`。
- **日志解析的 claimed 区间**:相邻 `E (` 行会被前一条诊断的上下文区间
  吞掉,限量断言会莫名变 1——测试日志行之间加空行。
- **新增返回字段忘改断言**:`repair_project` 增加 `repair_origin` 后,
  节点测试的精确字典断言必须同步(这是精确断言该付的代价)。
- **`"" in message` 恒为 True**:空串包含断言永远通过,删掉或用
  "名称无效" 这类固定文案断言。
- **Web 审批测试的同步**:SSE 流不结束 POST 就不返回,测试用后台线程 +
  `app.state.pending_approvals` 轮询再决策,这是留给测试观察审批状态的
  官方缝。

## 5. 复习自查

- 沙箱 mkdir 坑的根因是什么?为什么 sitecustomize 方案不污染仓库?
- idf.py 启动器解析为什么"已知安装优先"?
- create-project 的 `--path` 语义错在哪里?适配器怎么改的?
- idf_monitor 的 GBK 崩溃是 LUXAR 代码的问题吗?为什么?
- 命名管道限制属于哪一层?遇到它应该怎么处理(而不是改代码绕过)?
- checkpoint 序列化对测试断言有什么影响?
