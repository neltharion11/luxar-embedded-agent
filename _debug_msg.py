from luxar.server.chat_support import prepare_agent_context
from luxar.core.config_manager import ConfigManager
from luxar.core.llm_client import LLMClient

cm = ConfigManager()
cfg = cm.load()
client = LLMClient(cfg)

conv = []
msg = "hello with doc"
docs = [r"C:\Users\Gugugu\Desktop\keysking\OLED驱动芯片手册_CH1116-defbfae74f48bf57105d60d9d097c386.pdf"]

api_msgs = prepare_agent_context(conv, msg, "", cfg, cm, client, None, docs)

roles = [m["role"] for m in api_msgs]
print("Roles:", roles)
print("Total:", len(api_msgs))

for i, m in enumerate(api_msgs):
    has_tc = "tool_calls" in m
    has_id = "tool_call_id" in m
    print(f"  [{i}] role={m['role']} tc={has_tc} tcid={has_id}")

print("\nSending...")
try:
    for event in client.complete_stream(messages=api_msgs, tools=[]):
        t = event.get("type", "")
        c = event.get("content", "")[:40]
        if t == "tool_call":
            print(f"  tool_call: {event.get('name','')}")
        elif t == "token":
            print(c, end="", flush=True)
except Exception as e:
    print(f"\nERROR: {e}")
