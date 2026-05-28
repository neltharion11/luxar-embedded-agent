import urllib.request, json, time

payload = json.dumps({
    "message": "读取PDF分析CH1116芯片引脚和接口",
    "stream": True,
    "docs": [r"C:\Users\Gugugu\Desktop\keysking\OLED驱动芯片手册_CH1116-defbfae74f48bf57105d60d9d097c386.pdf"]
}).encode("utf-8")

print("Testing with __global__...")
t0 = time.perf_counter()
req = urllib.request.Request(
    "http://127.0.0.1:8777/api/conversations/__global__",
    data=payload,
    headers={"Content-Type": "application/json", "Accept": "text/event-stream"}
)
resp = urllib.request.urlopen(req, timeout=600)

phase = ""
for raw_line in resp:
    line = raw_line.decode("utf-8", errors="replace").strip()
    if line.startswith("event: "):
        phase = line[7:]
    elif line.startswith("data: "):
        data = line[6:]
        if phase == "token":
            try:
                d = json.loads(data)
                print(d.get("token",""), end="", flush=True)
            except: pass
        elif phase == "tool_running":
            try:
                d = json.loads(data)
                print(f"\n[running: {d.get('tool','')}]", flush=True)
            except: pass
        elif phase == "error":
            try:
                d = json.loads(data)
                print(f"\n[ERROR: {d.get('error','')[:200]}]", flush=True)
            except: pass
        elif phase == "done":
            break

t1 = time.perf_counter()
print(f"\n\nTime: {t1-t0:.1f}s")
