import urllib.request, json, time

payload = json.dumps({
    "message": "读取这个PDF，分析CH1116芯片的引脚定义和接口信息",
    "stream": False,
    "docs": [r"C:\Users\Gugugu\Desktop\keysking\OLED驱动芯片手册_CH1116-defbfae74f48bf57105d60d9d097c386.pdf"]
}).encode("utf-8")

print("=== LUXAR PDF Analysis Test ===")
t0 = time.perf_counter()
req = urllib.request.Request(
    "http://127.0.0.1:8777/api/conversations/clean_test",
    data=payload,
    headers={"Content-Type": "application/json"}
)
resp = urllib.request.urlopen(req, timeout=600)
data = json.loads(resp.read())
t1 = time.perf_counter()

msg = data.get("message", {})
content = msg.get("content", "")
print(f"Time: {t1-t0:.1f}s")
print(f"Response ({len(content)} chars):")
print(content[:2000])
