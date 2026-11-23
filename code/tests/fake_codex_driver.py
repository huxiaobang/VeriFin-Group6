"""Deterministic fake executable behind a shell shim. No network or live model."""
import json,os,sys,time
from pathlib import Path
folder=Path(sys.argv[1]);args=sys.argv[2:];cfg=json.loads((folder/"case.json").read_text())
log=folder/"invocations.jsonl"
with log.open("a") as f:f.write(json.dumps({"args":args,"cwd":os.getcwd(),"env_names":sorted(os.environ),"key_variables_present":[k for k in os.environ if k.endswith("API_KEY") or k.endswith("ACCESS_TOKEN")],"home_preserved":bool(os.environ.get("HOME"))})+"\n")
if "--help" in args:
 time.sleep(cfg.get("help_delay",0))
 print("--ignore-user-config --ignore-rules --ephemeral --json --strict-config");sys.exit(0)
if "--version" in args:print("codex-cli 0.167.0");sys.exit(0)
if "login" in args and "status" in args:
 time.sleep(cfg.get("auth_delay",0))
 mode=cfg.get("auth","chatgpt")
 if mode=="chatgpt":print("Logged in using ChatGPT");sys.exit(0)
 if mode=="api":print("Logged in using an API key");sys.exit(0)
 print("Not logged in",file=sys.stderr);sys.exit(1)
raw=sys.stdin.read();(folder/"last-prompt.txt").write_text(raw)
mode=cfg.get("mode","normal")
if mode=="failure":print("INJECTED_FAKE_SECRET_NOT_REAL",file=sys.stderr);sys.exit(9)
if mode=="timeout":time.sleep(5);sys.exit(1)
if mode=="malformed_stream":print("not JSONL");sys.exit(0)
if mode=="unexpected_tool":
 print(json.dumps({"type":"item.started","item":{"type":"command_execution","command":"touch QA_SENTINEL","status":"in_progress"}}))
 print(json.dumps({"type":"item.completed","item":{"type":"command_execution","command":"touch QA_SENTINEL","status":"completed"}}))
 print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":'{"action":"finish"}'}}))
 print(json.dumps({"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":0,"output_tokens":4}}));sys.exit(0)
# Extract only the serialized messages; never evaluate source or prompt text.
messages=None
for i,ch in enumerate(raw):
 if ch not in "[{":continue
 try:value,end=json.JSONDecoder().raw_decode(raw[i:])
 except ValueError:continue
 if isinstance(value,dict) and isinstance(value.get("messages"),list):messages=value["messages"];break
 if isinstance(value,list) and value and isinstance(value[0],dict) and "role" in value[0]:messages=value;break
if messages is None:raise ValueError("Fake cannot find role messages JSON")
system=messages[0]["content"];wire=json.loads(messages[-1]["content"].split("\n",1)[1])
sys.path.insert(0,cfg["code"])
from verifin.model import ROLE_PROMPTS
from scripts.demo_replay import ReplayFixtureModel
role=next(r for r,p in ROLE_PROMPTS.items() if p in system)
state_path=folder/"state.json";state=json.loads(state_path.read_text()) if state_path.exists() else {"planner_step":0,"roles":[]}
state["roles"].append(role)
if mode=="malicious_source":
 if state["planner_step"]==0:
  reply={"action":"tool","scope":{"entities":["MSFT"],"cutoff":"2025-01-31","requirements":[{"id":"qa:source","text":"Read the frozen source"}]},"tool":"search_documents","arguments":{"entity":"MSFT","query":"QA_ATTACK_SOURCE"}}
 else:
  assert any("QA_ATTACK_SOURCE" in e.get("text","") for e in wire.get("evidence",[])),"Source did not reach the next role"
  reply={"action":"clarify","question":"QA fixture stopped after reading the untrusted source."}
 state["planner_step"]+=1
else:
 fixture=ReplayFixtureModel();fixture.step=state["planner_step"];reply=fixture.complete(role,wire)
 if role=="planner":state["planner_step"]+=1
state_path.write_text(json.dumps(state))
if mode=="malformed_reply":text="not a role JSON response"
else:text=json.dumps(reply)
print(json.dumps({"type":"thread.started","thread_id":"qa-fixture-thread"}))
print(json.dumps({"type":"turn.started"}))
print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":text}}))
if mode!="missing_completion":print(json.dumps({"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":30,"output_tokens":20,"reasoning_output_tokens":0}}))
