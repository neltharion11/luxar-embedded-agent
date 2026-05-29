import urllib.request, json, time

payload = json.dumps({
    "message": "读取这个PDF，分析CH1116芯片的引脚定义和接口信息",
    "stream": True,
    "docs": [r"C:\Users\Gugugu\Desktop\keysking\OLED驱动芯片手册_CH1116-defbfae74f48bf57105d60d9d097c386.pdf"]
}).encode("utf-8")

print("=== LUXAR WebUI Simulation ===")
print(f"User: 读取这个PDF，分析CH1116芯片的引脚定义和接口信息")
print()

t0 = time.perf_counter()
req = urllib.request.Request(
    "http://127.0.0.1:8777/api/conversations/e2e_test",
    data=payload,
    headers={"Content-Type": "application/json", "Accept": "text/event-stream"}
)
resp = urllib.request.urlopen(req, timeout=600)

events = []
current_event = ""
for raw_line in resp:
    line = raw_line.decode("utf-8", errors="replace").strip()
    if line.startswith("event: "):
        current_event = line[7:]
    elif line.startswith("data: "):
        data = line[6:]
        events.append((current_event, data))

        evt = current_event
        if evt == "phase_changed":
            try:
                d = json.loads(data)
                print(f"[{d.get('phase','')}] {d.get('tool','')}", end=" ", flush=True)
            except: pass
        elif evt == "tool_running":
            try:
                d = json.loads(data)
                print(f"-> running: {d.get('tool','')}", flush=True)
            except: pass
        elif evt == "tool_output":
            try:
                d = json.loads(data)
                print(f"   output: {d.get('summary','')[:100]}", flush=True)
            except: pass
        elif evt == "done":
            print("\n[DONE]", flush=True)
            break
        elif evt == "error":
            try:
                d = json.loads(data)
                print(f"\nERROR: {d.get('error','')[:200]}", flush=True)
            except: pass

t1 = time.perf_counter()

# Get final response
req2 = urllib.request.Request("http://127.0.0.1:8777/api/conversations/e2e_test")
resp2 = urllib.request.urlopen(req2, timeout=10)
conv_data = json.loads(resp2.read())
messages = conv_data.get("messages", [])

for msg in messages:
    role = msg.get("role", "")
    if role == "assistant":
        content = msg.get("content", "")
        if content:
            print(f"\nAssistant ({len(content)} chars):")
            print(content[:2000])

print(f"\n=== Total time: {t1-t0:.1f}s ===")
print(f"Total SSE events: {len(events)}")
