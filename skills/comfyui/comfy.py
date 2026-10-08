#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""comfy.py — DSH <-> ComfyUI bridge (stdlib only).

Talks to a local ComfyUI server: lists workflows/models, converts a saved UI
workflow into the API format, patches inputs, queues the job, waits, and pulls
the produced files back into a project directory.

Usage examples:
  comfy.py status
  comfy.py workflows
  comfy.py describe "MiniMax_H3_T2V"
  comfy.py run "MiniMax_H3_T2V" --set "#105.prompt=..." --set "#105.width=1344" \
      --project tianlie --shot s01 --wait
  comfy.py run <wf> --check          # convert + submit + cancel (validation only)
  comfy.py watch <prompt_id> --project tianlie --shot s01
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


# --- Windows: the console codepage (GBK/cp936) cannot encode '▶' or Chinese
# text, which every workflow name here contains. Force UTF-8 on stdout/stderr;
# errors="replace" so a report never dies half-printed.
import sys as _sys

for _stream in (_sys.stdout, _sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = Path(__file__).resolve().parent
HOST = os.environ.get("COMFY_HOST", "127.0.0.1:8188")
BASE = f"http://{HOST}"
HOME = Path.home()


# --------------------------------------------------------------- 本机配置（部署时写入）
# local.json 放这台机器的实际路径（上游仓库里没有它）；env 变量优先级更高。
# 路径变了改 local.json 就行，不用动代码。
def _local_config() -> dict:
    f = HERE / "local.json"
    if f.is_file():
        try:
            # utf-8-sig：PowerShell 写出来的 JSON 可能带 BOM，别让 BOM 把配置吃掉
            return json.loads(f.read_text("utf-8-sig"))
        except Exception:
            return {}
    return {}


LOCAL = _local_config()

CACHE = Path(os.environ.get("COMFY_BRIDGE_CACHE", HOME / ".cache" / "dsh-comfy"))
PROJECTS = Path(os.environ.get("COMFY_PROJECTS", LOCAL.get("projects") or HOME / "comfy-projects"))
COMFY_BASE = Path(LOCAL.get("comfy_base") or HOME / "ComfyUI")
LAUNCHER = LOCAL.get("launcher") or str(COMFY_BASE / "启动ComfyUI.sh")


def _workflow_dirs() -> list:
    raw = os.environ.get("COMFY_WORKFLOW_DIRS")
    if raw:
        # 用 os.pathsep：Windows 是 ';'，类 Unix 是 ':'。
        # （旧写法写死 ':'，在 Windows 上会把 F:\... 按盘符切坏）
        return [Path(p) for p in raw.split(os.pathsep) if p.strip()]
    dirs = [Path(p) for p in LOCAL.get("workflow_dirs", [])]
    if not dirs:
        dirs = [HOME / "ComfyUI/user/default/workflows", HOME / "ComfyUI/user"]
    return dirs


WORKFLOW_DIRS = _workflow_dirs()

WIDGET_SCALARS = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"}

_PATHISH = re.compile(
    r"\.(safetensors|ckpt|pt|pth|bin|gguf|onnx|png|jpe?g|webp|gif|mp4|webm|avi|mov|wav|mp3|flac)$",
    re.I,
)


def norm_value(v):
    """Models shared from Windows carry backslash paths; the server wants '/'."""
    if isinstance(v, str) and "\\" in v and _PATHISH.search(v):
        return v.replace("\\", "/")
    return v


# --------------------------------------------------------------------------- http

def http_json(path: str, data=None, method=None, timeout=120):
    url = BASE + path
    body = None
    headers = {}
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = method or "POST"
    req = urllib.request.Request(url, data=body, headers=headers, method=method or "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else {}


def http_bytes(path: str, timeout=300) -> bytes:
    with urllib.request.urlopen(BASE + path, timeout=timeout) as resp:
        return resp.read()


def api_alive() -> bool:
    try:
        http_json("/system_stats", timeout=5)
        return True
    except Exception:
        return False


def object_info(force=False) -> dict:
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE / "object_info.json"
    if not force and cache_file.exists() and time.time() - cache_file.stat().st_mtime < 86400:
        try:
            return json.loads(cache_file.read_text("utf-8"))
        except Exception:
            pass
    data = http_json("/object_info", timeout=180)
    cache_file.write_text(json.dumps(data), "utf-8")
    return data


# ------------------------------------------------------------------ workflow loading

def iter_workflow_files():
    seen = set()
    for root in WORKFLOW_DIRS:
        if not root.exists():
            continue
        for f in sorted(root.rglob("*.json")):
            key = f.resolve()
            if key in seen:
                continue
            seen.add(key)
            yield f


def find_workflow(name: str) -> Path:
    p = Path(name).expanduser()
    if p.exists():
        return p
    needle = name.lower()
    hits = [f for f in iter_workflow_files() if needle in f.name.lower()]
    if not hits:
        raise SystemExit(f"no workflow matches {name!r} under {[str(d) for d in WORKFLOW_DIRS]}")
    exact = [h for h in hits if h.stem.lower() == needle]
    if exact:
        return exact[0]
    return hits[0]


def load_workflow(path: Path):
    wf = json.loads(path.read_text("utf-8"))
    if not isinstance(wf, dict):
        raise SystemExit(f"{path} is not a workflow object")
    api = bool(wf) and all(
        isinstance(v, dict) and "class_type" in v for v in wf.values()
    )
    return wf, api


# ------------------------------------------------------------------------ conversion

def link_type_names(oi: dict) -> set:
    """Output type names => these are link (non-widget) types."""
    types = set()
    for info in oi.values():
        out = info.get("output")
        if isinstance(out, list):
            for o in out:
                if isinstance(o, str):
                    types.add(o)
                elif isinstance(o, dict) and isinstance(o.get("type"), str):
                    types.add(o["type"])
    return types


def norm_link(l):
    """Workflows serialize links either as dicts or as classic 6-item arrays."""
    if isinstance(l, dict):
        return l
    if isinstance(l, (list, tuple)) and len(l) >= 5:
        return {
            "id": l[0], "origin_id": l[1], "origin_slot": l[2],
            "target_id": l[3], "target_slot": l[4],
            "type": l[5] if len(l) > 5 else None,
        }
    raise SystemExit(f"unrecognised link entry: {l!r}")


class Converter:
    """UI workflow (nodes/links/definitions) -> ComfyUI API prompt."""

    def __init__(self, oi: dict):
        self.oi = oi
        self.link_types = link_type_names(oi)
        self.api: dict = {}
        self.notes: list = []

    # ---- node input specs
    def spec_order(self, class_type: str):
        info = self.oi.get(class_type)
        if not info:
            return []
        inp = info.get("input", {}) or {}
        rows = []
        for group in ("required", "optional"):
            for name, spec in (inp.get(group) or {}).items():
                t = spec[0] if isinstance(spec, list) and spec else spec
                opts = spec[1] if isinstance(spec, list) and len(spec) > 1 and isinstance(spec[1], dict) else {}
                rows.append((name, t, opts))
        return rows

    def is_widget(self, name, t, opts) -> bool:
        if opts.get("forceInput"):
            return False
        if isinstance(t, list):
            return True
        if isinstance(t, str) and t in WIDGET_SCALARS:
            return True
        if isinstance(t, str) and t not in self.link_types:
            return True
        return False

    # ---- subgraph helpers
    @staticmethod
    def _sub_defs(wf) -> dict:
        defs = (wf.get("definitions") or {}).get("subgraphs") or []
        return {d["id"]: d for d in defs if isinstance(d, dict) and d.get("id")}

    @staticmethod
    def _widget_like(sg_input: dict, link_types: set) -> bool:
        t = sg_input.get("type")
        if isinstance(t, str) and t in WIDGET_SCALARS:
            return True
        if isinstance(t, str) and t not in link_types:
            return True
        return False

    def _sub_bindings(self, inst, sg, parent) -> dict:
        """slot -> ('link',(origin_id, slot)) | ('value', v) | ('none', None)"""
        by_name = {}
        for i in inst.get("inputs", []) or []:
            by_name[i.get("name")] = i
        wv = list(inst.get("widgets_values") or [])
        wi = 0
        binds = {}
        for j, si in enumerate(sg.get("inputs", []) or []):
            ii = by_name.get(si.get("name"))
            if ii is not None and ii.get("link") is not None:
                l = parent["links"].get(ii["link"])
                if l is not None:
                    binds[j] = ("link", (l["origin_id"], l["origin_slot"]))
                    if self._widget_like(si, self.link_types) and wi < len(wv):
                        wi += 1  # keep positional alignment with serialized values
                    continue
            if self._widget_like(si, self.link_types) and wi < len(wv):
                binds[j] = ("value", wv[wi])
                wi += 1
            else:
                binds[j] = ("none", None)
        if wi != len(wv):
            self.notes.append(
                f"subgraph {sg.get('name')}: consumed {wi} of {len(wv)} widget values"
            )
        return binds

    # ---- flattening contexts
    def _context(self, nodes, links, defs, prefix, parent=None, sgdef=None, bindings=None):
        return {
            # mode 2 = muted (not executed at all); mode 4 = bypassed (pass-through)
            "nodes": {n["id"]: n for n in nodes if n.get("mode") != 2},
            "links": {l["id"]: l for l in (norm_link(x) for x in links)},
            "defs": defs,
            "prefix": prefix,
            "parent": parent,
            "sgdef": sgdef,
            "bindings": bindings or {},
            "subs": {},
        }

    def _sub_ctx(self, ctx, inst):
        if inst["id"] in ctx["subs"]:
            return ctx["subs"][inst["id"]]
        sg = ctx["defs"][inst["type"]]
        binds = self._sub_bindings(inst, sg, ctx)
        sub = self._context(
            sg.get("nodes", []), sg.get("links", []), ctx["defs"],
            f"{ctx['prefix']}{inst['id']}/", parent=ctx, sgdef=sg, bindings=binds,
        )
        ctx["subs"][inst["id"]] = sub
        return sub

    def _bypass_source(self, ctx, node, slot):
        """A bypassed node (mode 4) forwards the matching input to its output."""
        outs = node.get("outputs") or []
        out_type = outs[slot].get("type") if slot < len(outs) else None
        linked = [e for e in (node.get("inputs") or []) if e.get("link") is not None]
        cands = [e for e in linked if out_type is None or e.get("type") == out_type]
        if not cands:
            cands = linked
        if not cands:
            return None
        # prefer the input at the same slot index when types agree
        pick = cands[0]
        if slot < len(linked) and linked[slot] in cands:
            pick = linked[slot]
        l = ctx["links"].get(pick["link"])
        if not l:
            return None
        return self._resolve(ctx, l["origin_id"], l["origin_slot"])

    def _resolve(self, ctx, origin_id, slot):
        node = ctx["nodes"].get(origin_id)
        if node is None:
            return None
        if node.get("mode") == 4:
            return self._bypass_source(ctx, node, slot)
        if node["type"] in ctx["defs"]:
            sub = self._sub_ctx(ctx, node)
            omap = {}
            out_id = sub["sgdef"]["outputNode"]["id"]
            for l in sub["links"].values():
                if l["target_id"] == out_id:
                    omap[l["target_slot"]] = (l["origin_id"], l["origin_slot"])
            if slot not in omap:
                return None
            io, islot = omap[slot]
            return self._resolve(sub, io, islot)
        return (f"{ctx['prefix']}{origin_id}", slot)

    def _input_value(self, ctx, node, entry):
        """Resolve one node['inputs'] entry to a link ref or a literal, or None."""
        link_id = entry.get("link")
        if link_id is None:
            return None
        l = ctx["links"].get(link_id)
        if l is None:
            return None
        in_id = None
        for sid, sg in ctx["defs"].items():
            if ctx.get("sgdef") is sg:
                in_id = sg["inputNode"]["id"]
                break
        if in_id is not None and l["origin_id"] == in_id:
            kind, payload = ctx["bindings"].get(l["origin_slot"], ("none", None))
            if kind == "value":
                return ("value", payload)
            if kind == "link":
                oid, oslot = payload
                res = self._resolve(ctx["parent"], oid, oslot) if ctx["parent"] else None
                return ("link", res) if res else None
            return None
        res = self._resolve(ctx, l["origin_id"], l["origin_slot"])
        return ("link", res) if res else None

    def _widget_names(self, node):
        """Names of widget-backed inputs as serialized by the frontend."""
        names = []
        for entry in node.get("inputs", []) or []:
            w = entry.get("widget")
            if isinstance(w, dict) and w.get("name"):
                names.append(entry.get("name") or w["name"])
        return names

    def _assign_widgets(self, node, api_inputs):
        """Map widgets_values positionally onto the node's widgets (all of them)."""
        vals = list(node.get("widgets_values") or [])
        if not vals:
            return
        named = self._widget_names(node)
        specs = self.spec_order(node["type"])
        spec_by_name = {n: (t, o) for n, t, o in specs}
        if named:
            queue = []
            for n in named:
                t, o = spec_by_name.get(n, (None, {}))
                queue.append((n, t, o))
            if len(queue) < len(vals):
                for n, t, o in specs:
                    if n in named:
                        continue
                    if self.is_widget(n, t, o):
                        queue.append((n, t, o))
                    if len(queue) >= len(vals):
                        break
        else:
            queue = [(n, t, o) for n, t, o in specs if self.is_widget(n, t, o)]
        i = 0
        for (name, t, o) in queue:
            if i >= len(vals):
                break
            api_inputs[name] = norm_value(vals[i])
            i += 1
            if isinstance(o, dict) and o.get("control_after_generate") and i < len(vals):
                i += 1  # skip the frontend-only control widget
        if i != len(vals):
            self.notes.append(f"node {node['id']} {node['type']}: consumed {i}/{len(vals)} widgets")

    def _emit(self, ctx, node):
        nid = f"{ctx['prefix']}{node['id']}"
        api_inputs = {}
        self._assign_widgets(node, api_inputs)
        for entry in node.get("inputs", []) or []:
            name = entry.get("name")
            if name is None or entry.get("link") is None:
                continue
            got = self._input_value(ctx, node, entry)
            if got is None:
                continue
            if got[0] == "link":
                api_inputs[name] = [got[1][0], got[1][1]]
            else:
                api_inputs[name] = got[1]
        self.api[nid] = {
            "class_type": node["type"],
            "inputs": api_inputs,
            "_meta": {"title": node.get("title") or node["type"]},
        }

    def convert(self, wf: dict) -> dict:
        defs = self._sub_defs(wf)
        ctx = self._context(wf.get("nodes", []), wf.get("links", []), defs, "")
        self._walk(ctx)
        self.api = self._prune(self.api)
        return self.api

    def _prune(self, api: dict) -> dict:
        """Keep the ancestor closure of output nodes, like the frontend does."""
        unknown = [nid for nid, n in api.items() if n["class_type"] not in self.oi]
        for nid in unknown:
            self.notes.append(f"dropped node {nid} {api[nid]['class_type']}: not known to this server")
        alive = {nid: n for nid, n in api.items() if nid not in set(unknown)}
        keep = {nid for nid, n in alive.items() if (self.oi.get(n["class_type"]) or {}).get("output_node")}
        if not keep:
            # no SaveImage/SaveVideo-style terminal: fall back to node with no consumers
            consumed = {v[0] for n in alive.values() for v in n["inputs"].values()
                        if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str)}
            keep = {nid for nid in alive if nid not in consumed}
        frontier = list(keep)
        while frontier:
            nid = frontier.pop()
            for v in alive[nid]["inputs"].values():
                if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and v[0] in alive:
                    if v[0] not in keep:
                        keep.add(v[0])
                        frontier.append(v[0])
        dropped = [nid for nid in alive if nid not in keep]
        if dropped:
            self.notes.append(f"pruned {len(dropped)} node(s) not feeding an output: {', '.join(sorted(dropped)[:8])}")
        return {nid: alive[nid] for nid in alive if nid in keep}

    def _walk(self, ctx):
        for node in list(ctx["nodes"].values()):
            if node.get("mode") == 4:
                continue  # bypassed: links pass through, the node itself is not emitted
            if node["type"] in ctx["defs"]:
                sub = self._sub_ctx(ctx, node)
                self._walk(sub)
            else:
                self._emit(ctx, node)


# --------------------------------------------------------------------------- patching

def combo_options(oi: dict, class_type: str, field: str):
    """服务器为某个节点的某个 combo 输入给出的可选值列表，取不到就 None。"""
    spec = ((oi.get(class_type) or {}).get("input") or {})
    for section in ("required", "optional"):
        s = (spec.get(section) or {}).get(field)
        if isinstance(s, list) and s and isinstance(s[0], list):
            return s[0]
    return None


def fix_model_names(api: dict, oi: dict):
    """把模型名对齐到这台服务器实际认的值。

    为什么需要：ComfyUI 的 combo 校验是**严格字符串比对**（execution.py:
    `val not in combo_options`），而模型名的分隔符各机器不一样 —— Windows 上
    `folder_paths` 给出的是 `Qwen\\x.safetensors`，Linux 上是 `Qwen/x.safetensors`。
    源机器（Linux）存的工作流是正斜杠，本机（Windows）认反斜杠，于是同一个文件
    在两边只有一个能过校验（而且报出来的错是 "Value not in list"，看着像缺模型）。

    这里不猜：拿服务器自己的清单逐个核对，能在两种分隔符之间换来换去的就换，
    换完还不在清单里的**报出来**（那才是真的缺文件/名字变了）。
    """
    out = []
    for nid, node in api.items():
        inputs = node.get("inputs") or {}
        for field, val in list(inputs.items()):
            if not isinstance(val, str) or not _PATHISH.search(val):
                continue
            opts = combo_options(oi, node.get("class_type"), field)
            if not opts or val in opts:
                continue
            alt = val.replace("/", "\\") if "/" in val else val.replace("\\", "/")
            if alt in opts:
                inputs[field] = alt
                out.append(f"node {nid} {node.get('class_type')}.{field}: {val!r} -> {alt!r}（对齐本机分隔符）")
            else:
                close = [o for o in opts if Path(o).name == Path(val).name]
                hint = f"；清单里有同名文件 {close[0]!r}" if close else ""
                out.append(f"!! node {nid} {node.get('class_type')}.{field} = {val!r} 不在这台服务器的清单里{hint}")
    return out


def load_api_or_convert(path: Path, oi: dict):
    wf, is_api = load_workflow(path)
    if is_api:
        return wf, fix_model_names(wf, oi)
    conv = Converter(oi)
    api = conv.convert(wf)
    if not api:
        raise SystemExit(f"conversion produced no nodes for {path}")
    return api, conv.notes + fix_model_names(api, oi)


def node_label(nid, node):
    meta = node.get("_meta") or {}
    title = meta.get("title") or node.get("class_type")
    return f"{nid}:{title}"


def resolve_selector(api: dict, selector: str) -> list:
    """'#105', 'KSampler', 'My Title' -> list of node ids."""
    sel = selector.strip()
    if sel.startswith("#"):
        sid = sel[1:]
        if sid not in api:
            raise SystemExit(f"node #{sid} not in workflow")
        return [sid]
    hits = [nid for nid, n in api.items()
            if n.get("class_type") == sel or (n.get("_meta", {}).get("title") == sel)]
    if not hits:
        low = sel.lower()
        hits = [nid for nid, n in api.items()
                if low in (n.get("class_type") or "").lower()
                or low in ((n.get("_meta", {}).get("title") or "").lower())]
    if not hits:
        raise SystemExit(f"selector {selector!r} matched no node")
    return hits


def parse_value(raw: str):
    s = raw.strip()
    if s.startswith("@"):
        p = Path(s[1:]).expanduser()
        return p.read_text("utf-8") if p.exists() else raw
    try:
        return json.loads(s)
    except Exception:
        return raw


def apply_set(api: dict, assignment: str):
    if "=" not in assignment:
        raise SystemExit(f"--set needs node.field=value, got {assignment!r}")
    target, raw = assignment.split("=", 1)
    if "." not in target:
        raise SystemExit(f"--set needs node.field=value, got {assignment!r}")
    selector, field = target.rsplit(".", 1)
    value = parse_value(raw)
    ids = resolve_selector(api, selector)
    if len(ids) > 1:
        raise SystemExit(f"selector {selector!r} is ambiguous: {[node_label(i, api[i]) for i in ids]}")
    nid = ids[0]
    api[nid]["inputs"][field] = value
    return nid, field, value


# ------------------------------------------------------------------ run / watch

def execution_targets(api: dict, oi: dict, want: str | None = None) -> list:
    """提交时显式点名这次要执行的输出节点。

    为什么必须这么做（本机实测，ComfyUI 0.37.0）：`validate_prompt` 用
    `class_.OUTPUT_NODE is True`（**身份**比较）收集输出节点，而 SaveVideo 这类新式
    comfy_api(V3) 节点不走这条路径；object_info 里它明明是 output_node=true（那是用
    `== True` 判的）。结果：只要图里还有别的 output_node（本机一堆 TE_text_display /
    TE_H3_Prompt_Enhancer 都是），服务器就会**执行成功但一个视频都不产出**
    （实测：542ms 返回 success，4 个 SaveVideo 一个没跑）。

    另外：服务器只校验"被点名输出节点的上游"，所以点名范围要**窄**。一份工作流里
    常见 4 条并行分支（Ref2V/T2V/I2V/FL2V），没在用的那几条可能引用着早已删掉的输入图；
    把 4 个 SaveVideo 全点名会连带校验它们 → 直接 400。
    `want`（一般是本次的 --project/--shot）用来在多个保存节点里挑出**这次真正要的那个**：
    这些节点的 filename_prefix 就是 render_shots 设的 `dsh/<项目>/<镜号>`。
    """
    def is_out(node):
        return bool((oi.get(node["class_type"]) or {}).get("output_node"))

    save_like = ("Save", "Combine", "Preview", "Export", "VHS_")
    saves = [nid for nid, n in api.items()
             if is_out(n) and any(h in n["class_type"] for h in save_like)]
    if saves and want:
        needle = want.lower()
        hit = [nid for nid in saves
               if needle in str(api[nid]["inputs"].get("filename_prefix", "")).lower()]
        if hit:
            return hit
    if saves:
        return saves
    return [nid for nid, n in api.items() if is_out(n)]


def submit(api: dict, oi: dict | None = None, want: str | None = None) -> dict:
    payload = {"prompt": api, "client_id": f"dsh-{uuid.uuid4().hex[:8]}"}
    targets = execution_targets(api, oi or object_info(), want)
    if targets:
        payload["partial_execution_targets"] = targets
    return http_json("/prompt", payload)


def history(prompt_id: str):
    try:
        h = http_json(f"/history/{prompt_id}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    return h.get(prompt_id)


def queue_state(prompt_id: str):
    q = http_json("/queue")
    for i, item in enumerate(q.get("queue_running", []) or []):
        if len(item) > 1 and item[1] == prompt_id:
            return "running"
    for i, item in enumerate(q.get("queue_pending", []) or []):
        if len(item) > 1 and item[1] == prompt_id:
            return f"pending#{i + 1}"
    return None


def collect_outputs(entry: dict):
    files = []
    for nid, out in (entry.get("outputs") or {}).items():
        if not isinstance(out, dict):
            continue
        for key, val in out.items():
            if not isinstance(val, list):
                continue
            for item in val:
                if isinstance(item, dict) and item.get("filename"):
                    files.append({"node": nid, "kind": key, **item})
    return files


def download(files, dest: Path, prefix: str):
    dest.mkdir(parents=True, exist_ok=True)
    saved = []
    for i, f in enumerate(files):
        q = urllib.parse.urlencode({
            "filename": f["filename"],
            "subfolder": f.get("subfolder", ""),
            "type": f.get("type", "output"),
        })
        blob = http_bytes(f"/view?{q}")
        name = Path(f["filename"]).name
        target = dest / (f"{prefix}_{name}" if prefix else name)
        n = 1
        while target.exists():
            target = dest / (f"{prefix}_{Path(name).stem}_{n}{Path(name).suffix}")
            n += 1
        target.write_bytes(blob)
        saved.append({"path": str(target), "bytes": len(blob), "kind": f.get("kind"),
                      "node": f.get("node"), "source": f["filename"]})
    return saved


def failure_detail(entry: dict) -> str:
    status = entry.get("status") or {}
    msgs = []
    for m in status.get("messages", []) or []:
        try:
            msgs.append(json.dumps(m, ensure_ascii=False)[:400])
        except Exception:
            msgs.append(str(m)[:400])
    return " | ".join(msgs) or status.get("status_str", "unknown failure")


def wait_for(prompt_id: str, timeout: float, poll: float, quiet=False):
    start = time.time()
    last = None
    while True:
        entry = history(prompt_id)
        if entry:
            status = (entry.get("status") or {}).get("status_str")
            if status == "success" or (entry.get("status") or {}).get("completed"):
                return entry
            if status == "error":
                raise SystemExit("job failed: " + failure_detail(entry))
            return entry
        state = queue_state(prompt_id)
        if state != last and not quiet:
            print(f"[{int(time.time() - start)}s] {state or 'queued'}", file=sys.stderr)
            last = state
        if state is None and time.time() - start > 5:
            entry = history(prompt_id)
            if entry:
                continue
        if time.time() - start > timeout:
            raise SystemExit(f"timeout after {int(timeout)}s; job {prompt_id} still queued. "
                             f"Use `comfy.py watch {prompt_id}`")
        time.sleep(poll)


# --------------------------------------------------------------------------- commands

def cmd_status(args):
    stats = http_json("/system_stats")
    q = http_json("/queue")
    sysinfo = stats.get("system", {})
    print(f"server      : {HOST}  ComfyUI {sysinfo.get('comfyui_version')}  python {sysinfo.get('python_version','')[:6]}")
    for d in stats.get("devices", []) or []:
        free = d.get("vram_free")
        total = d.get("vram_total")
        fmt = lambda b: f"{b/2**30:.1f}G" if isinstance(b, (int, float)) else "?"
        print(f"device      : {d.get('name')}  vram {fmt(free)} free / {fmt(total)} total  torch {d.get('torch_version')}")
    print(f"queue       : {len(q.get('queue_running', []))} running, {len(q.get('queue_pending', []))} pending")
    for item in q.get("queue_running", []) or []:
        print(f"  running   : {item[1]}")
    for item in q.get("queue_pending", []) or []:
        print(f"  pending   : {item[1]}")


def cmd_models(args):
    oi = object_info()
    kinds = {
        "checkpoint": ("CheckpointLoaderSimple", "ckpt_name"),
        "unet": ("UNETLoader", "unet_name"),
        "clip": ("CLIPLoader", "clip_name"),
        "vae": ("VAELoader", "vae_name"),
        "lora": ("LoraLoader", "lora_name"),
        "controlnet": ("ControlNetLoader", "control_net_name"),
        "upscale": ("UpscaleModelLoader", "model_name"),
    }
    want = [args.kind] if args.kind else list(kinds)
    for kind in want:
        cls, field = kinds.get(kind, (None, None))
        if not cls:
            print(f"unknown kind {kind}"); continue
        info = oi.get(cls, {})
        spec = ((info.get("input", {}) or {}).get("required", {}) or {}).get(field)
        vals = spec[0] if isinstance(spec, list) and spec else []
        print(f"== {kind} ({len(vals) if isinstance(vals, list) else 0})")
        for v in (vals if isinstance(vals, list) else []):
            print("   ", v)


def cmd_workflows(args):
    rows = []
    for f in iter_workflow_files():
        if f.parent.name == "node_modules":
            continue
        try:
            d = json.loads(f.read_text("utf-8"))
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        is_api = bool(d) and all(isinstance(v, dict) and "class_type" in v for v in d.values())
        n = len(d) if is_api else len(d.get("nodes", []) or [])
        if n == 0:
            continue
        subs = len(((d.get("definitions") or {}).get("subgraphs") or [])) if not is_api else 0
        rows.append((f, n, is_api, subs))
    rows.sort(key=lambda r: -r[1])
    for f, n, is_api, subs in rows:
        tag = "API " if is_api else "UI  "
        extra = f" subgraphs={subs}" if subs else ""
        print(f"{tag}{n:4d} nodes{extra:14s} {f}")


def cmd_describe(args):
    path = find_workflow(args.workflow)
    api, notes = load_api_or_convert(path, object_info())
    print(f"# {path}")
    print(f"# {len(api)} api nodes")
    for note in notes:
        print(f"# note: {note}")
    pat = (args.filter or "").lower()
    for nid in sorted(api, key=lambda x: (len(x), x)):
        node = api[nid]
        label = node_label(nid, node)
        if pat and pat not in label.lower():
            continue
        print(f"\n#{nid}  {node.get('class_type')}   ({node.get('_meta', {}).get('title')})")
        for k, v in node["inputs"].items():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str):
                print(f"    {k} <- node {v[0]} slot {v[1]}")
            else:
                s = json.dumps(v, ensure_ascii=False)
                if len(s) > 100:
                    s = s[:100] + "..."
                print(f"    {k} = {s}")


def cmd_convert(args):
    path = find_workflow(args.workflow)
    api, notes = load_api_or_convert(path, object_info())
    out = Path(args.out).expanduser() if args.out else path.with_suffix(".api.json")
    out.write_text(json.dumps(api, ensure_ascii=False, indent=2), "utf-8")
    for n in notes:
        print(f"# note: {n}", file=sys.stderr)
    print(f"wrote {out}  ({len(api)} nodes)")


def load_pipelines() -> dict:
    """pipelines.json + 本机覆盖 pipelines.local.json（部署时写入，上游仓库里没有）。

    工作流被用户改过之后节点 id 会变。覆盖文件让本机用正确的 id，
    又不用把上游那份已验证的注册表改花（同名 key 覆盖，其余保留）。
    """
    data: dict = {}
    for name in ("pipelines.json", "pipelines.local.json"):
        f = HERE / name
        if not f.is_file():
            continue
        try:
            part = json.loads(f.read_text("utf-8-sig"))
        except Exception as exc:
            print(f"# warn: {name} 读不了: {exc}", file=sys.stderr)
            continue
        for k, v in part.items():
            if isinstance(v, dict) and isinstance(data.get(k), dict):
                data[k] = {**data[k], **v}
            else:
                data[k] = v
    return data


def registry_entry(path: Path):
    """Find the pipelines.json entry that describes this workflow file.

    取**最具体**的那个匹配：先看文件名完全相等，再看匹配到的工作流/别名串最长的。
    以前是"第一个子串命中"，结果 `video-main`（workflow=▶▷MiniMaxH3-加速视频流整合）
    会把 `▶▷MiniMaxH3-加速视频流整合 有4个提示词 (本机).json` 也抢过去 ——
    于是拿错了节点 id、还把只存在于另一个文件的 auto_drop 套上去。
    """
    data = load_pipelines()
    if not data:
        return None, None
    name = path.name.lower()
    stem = path.stem.lower()
    best = (0, None, None)
    for key, entry in data.items():
        if key.startswith("_") or not isinstance(entry, dict):
            continue
        cands = [entry.get("workflow", "")] + list(entry.get("aliases") or [])
        for c in cands:
            if not c:
                continue
            cname = Path(c).name.lower()
            if cname not in name:
                continue
            score = len(cname) + (1000 if Path(c).stem.lower() == stem else 0)
            if score > best[0]:
                best = (score, key, entry)
    return best[1], best[2]


def drop_nodes(api: dict, selectors):
    """Remove nodes from the prompt and unlink anything that pointed at them."""
    removed = []
    for sel in selectors or []:
        for nid in resolve_selector(api, sel):
            if nid in api:
                removed.append((nid, api[nid].get("class_type")))
                api.pop(nid)
    for node in api.values():
        for field in [k for k, v in node["inputs"].items()
                      if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and v[0] not in api]:
            node["inputs"].pop(field, None)
    return removed


def cmd_run(args):
    path = find_workflow(args.workflow)
    api, notes = load_api_or_convert(path, object_info())
    for note in notes:
        print(f"warn: {note}", file=sys.stderr)
    key, entry = registry_entry(path)
    drops = list(args.drop or [])
    if entry and not args.no_auto_drop:
        auto = list(entry.get("auto_drop") or [])
        if auto:
            print(f"registry {key}: auto-dropping {auto} (DSH writes the final prompt itself)")
            drops += auto
    # auto_drop 是"尽力而为"：某个类在这份工作流里不存在（换了版本/换了文件）时只提示，
    # 不能让整次运行挂掉 —— 之前 resolve_selector 会 SystemExit。
    for sel in drops:
        if sel.startswith("#"):
            continue
        if not any(n.get("class_type") == sel for n in api.values()):
            print(f"note: auto/drop selector {sel!r} 在这份工作流里没有对应节点，跳过")
    for nid, cls in drop_nodes(api, [s for s in drops if s.startswith("#")
                                     or any(n.get("class_type") == s for n in api.values())]):
        print(f"dropped #{nid} ({cls})")
    if entry and not args.no_auto_drop:
        for target, value in (entry.get("auto_set") or {}).items():
            nid, field, applied = apply_set(api, f"{target}={json.dumps(value, ensure_ascii=False)}")
            print(f"registry {key}: #{nid}.{field} = {json.dumps(applied, ensure_ascii=False)}")
    changes = []
    for s in (args.set or []):
        nid, field, value = apply_set(api, s)
        shown = json.dumps(value, ensure_ascii=False)
        changes.append((nid, field, shown[:80]))
    for nid, field, shown in changes:
        print(f"set #{nid}.{field} = {shown}")
    if args.dump:
        Path(args.dump).expanduser().write_text(json.dumps(api, ensure_ascii=False, indent=2), "utf-8")
        print(f"wrote {args.dump}")
    if args.dry_run:
        print(json.dumps(api, ensure_ascii=False, indent=2)[:4000])
        return
    try:
        oi = object_info()
        want = args.shot or getattr(args, "project", None)
        targets = execution_targets(api, oi, want)
        if targets:
            print("execute targets: " + ", ".join(
                f"#{t} {api[t]['class_type']}" for t in targets if t in api))
            if len(targets) > 1:
                print("note: 点名了多个输出节点 —— 服务器会连带校验它们各自的上游；"
                      "没在用的分支若引用着已删掉的输入图会 400。"
                      "用 --shot <本次镜号> 可以把校验缩到这一次真正要出的那条分支。")
        res = submit(api, oi, want)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        print(f"submission rejected (HTTP {e.code}):", file=sys.stderr)
        try:
            err = json.loads(body)
            print(json.dumps(err.get("error", err), ensure_ascii=False, indent=2)[:4000], file=sys.stderr)
            for nid, nd in (err.get("node_errors") or {}).items():
                print(f"node {nid}: " + json.dumps(nd, ensure_ascii=False)[:800], file=sys.stderr)
        except Exception:
            print(body[:2000], file=sys.stderr)
        raise SystemExit(2)
    pid = res.get("prompt_id")
    print(f"queued {pid}  (#{res.get('number')})")
    if args.check:
        try:
            http_json("/queue", {"delete": [pid]})
        except Exception:
            pass
        try:
            http_json("/interrupt", {})
        except Exception:
            pass
        print("validation OK — job cancelled (--check)")
        return
    if args.wait:
        entry = wait_for(pid, timeout=args.timeout, poll=args.poll, quiet=args.quiet)
        files = collect_outputs(entry)
        dest = PROJECTS / args.project / "out" if args.project else Path.cwd() / "out"
        saved = download(files, dest, args.shot or "")
        manifest = dest / "manifest.json"
        prev = json.loads(manifest.read_text("utf-8")) if manifest.exists() else []
        prev.extend(saved)
        manifest.write_text(json.dumps(prev, ensure_ascii=False, indent=2), "utf-8")
        print(f"done: {len(saved)} file(s) -> {dest}")
        for s in saved:
            print("  ", s["path"], f'({s["bytes"]/1e6:.1f} MB)')
    else:
        print(f"not waiting; run: comfy.py watch {pid} --project {args.project or 'default'}"
              + (f" --shot {args.shot}" if args.shot else ""))


def cmd_watch(args):
    entry = wait_for(args.prompt_id, timeout=args.timeout, poll=args.poll, quiet=args.quiet)
    files = collect_outputs(entry)
    dest = PROJECTS / (args.project or "default") / "out"
    saved = download(files, dest, args.shot or "")
    print(f"done: {len(saved)} file(s) -> {dest}")
    for s in saved:
        print("  ", s["path"], f'({s["bytes"]/1e6:.1f} MB)')


def follow_to_literal(api: dict, start: tuple, wanted=("text", "value", "prompt", "string")):
    """Walk a single-input link chain until a node with a literal widget value."""
    seen = set()
    queue = [start]
    while queue:
        nid, field = queue.pop(0)
        if nid in seen:
            continue
        seen.add(nid)
        node = api.get(nid)
        if not node:
            return None
        for key in wanted:
            v = node["inputs"].get(key)
            if v is not None and not (isinstance(v, list) and len(v) == 2 and isinstance(v[0], str)):
                return nid, key, v
        nxt = node["inputs"].get(field)
        if isinstance(nxt, list) and len(nxt) == 2 and isinstance(nxt[0], str):
            queue.append((nxt[0], field))
        for v in node["inputs"].values():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str):
                queue.append((v[0], field))
    return None


def cmd_plan(args):
    """Print the live wiring of a workflow as ready-to-use --set selectors."""
    path = find_workflow(args.workflow)
    api, _ = load_api_or_convert(path, object_info())
    oi = object_info()
    print(f"# {path.name}   ({len(api)} api nodes)")

    def link(v):
        return v if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) else None

    def chase_image(nid, depth=0):
        """Follow a chain to the LoadImage that ultimately feeds it."""
        node = api.get(nid)
        if not node or depth > 6:
            return None
        if node["class_type"] == "LoadImage":
            return nid
        for v in node["inputs"].values():
            if link(v):
                hit = chase_image(v[0], depth + 1)
                if hit:
                    return hit
        return None

    # ---- video pipelines -------------------------------------------------
    for nid, n in api.items():
        ct = n["class_type"]
        if not ct.startswith("MiniMaxH3") or ct.endswith("Patch"):
            continue
        mode = {"MiniMaxH3ReferenceToVideo": "reference-to-video (多参考图)",
                "MiniMaxH3ImageToVideo": "image-to-video (首帧/首尾帧)",
                "MiniMaxH3TextToVideo": "text-to-video"}.get(ct, ct)
        print(f"\nvideo-node   #{nid}  {ct}   [{mode}]")
        for knob in ("prompt", "width", "height", "length", "ref_image_size"):
            v = n["inputs"].get(knob)
            if v is None:
                continue
            if link(v):
                hit = follow_to_literal(api, (v[0], knob))
                if hit and hit[1] in ("text", "value", "prompt"):
                    print(f"  {knob:12s} -> #{hit[0]}.{hit[1]} = "
                          f"{json.dumps(hit[2], ensure_ascii=False)[:110]}")
                elif knob in ("width", "height"):
                    src = api.get(v[0], {})
                    extra = "  ".join(f"#{v[0]}.{k}={json.dumps(val, ensure_ascii=False)}"
                                      for k, val in src["inputs"].items()
                                      if not link(val) and k in ("aspect_ratio", "megapixels"))
                    print(f"  {knob:12s} -> #{v[0]} {src.get('class_type')}  {extra}")
                else:
                    print(f"  {knob:12s} -> #{v[0]} {api.get(v[0], {}).get('class_type')}")
            else:
                print(f"  {knob:12s} = {json.dumps(v, ensure_ascii=False)}")
        for key, val in n["inputs"].items():
            if key.startswith(("ref_images", "first_frame", "last_frame")) and link(val):
                img = chase_image(val[0])
                if img:
                    print(f"  {key:12s} -> #{img}.image = "
                          f"{json.dumps(api[img]['inputs'].get('image'), ensure_ascii=False)}")

    # ---- image pipelines -------------------------------------------------
    for nid, n in api.items():
        ct = n["class_type"]
        if "KSampler" not in ct:
            continue
        info = oi.get(ct, {})
        if not (info.get("output") and "LATENT" in info["output"]):
            continue
        steps = n["inputs"].get("steps")
        print(f"\nsampler      #{nid}  {ct}   steps={steps} cfg={n['inputs'].get('cfg')}")
        pos = link(n["inputs"].get("positive"))
        if pos:
            hit = follow_to_literal(api, (pos[0], "positive"))
            if hit:
                print(f"  prompt     -> #{hit[0]}.{hit[1]} = "
                      f"{json.dumps(hit[2], ensure_ascii=False)[:110]}")
        seed = n["inputs"].get("seed")
        if link(seed):
            hit = follow_to_literal(api, (seed[0], "seed"), wanted=("seed", "value"))
            if hit:
                print(f"  seed       -> #{hit[0]}.{hit[1]} = {json.dumps(hit[2], ensure_ascii=False)}")
        elif seed is not None:
            print(f"  seed       = {json.dumps(seed, ensure_ascii=False)}")
        lat = link(n["inputs"].get("latent_image"))
        if lat:
            src = api.get(lat[0], {})
            print(f"  latent     -> #{lat[0]} {src.get('class_type')}")
            if src.get("class_type") == "ComfySwitchNode":
                sw = src["inputs"].get("switch")
                branches = {}
                for side in ("on_true", "on_false"):
                    b = link(src["inputs"].get(side))
                    branches[side] = f"#{b[0]} {api.get(b[0], {}).get('class_type')}" if b else "-"
                print(f"    switch={json.dumps(sw)}  true={branches['on_true']}  false={branches['on_false']}"
                      f"   (true = 文生图空 latent, false = 图生图/编辑)")
            for knob in ("width", "height"):
                v = link(src["inputs"].get(knob))
                if v:
                    rs = api.get(v[0], {})
                    print(f"    {knob} <- #{v[0]} {rs.get('class_type')} "
                          f"aspect_ratio={json.dumps(rs['inputs'].get('aspect_ratio'), ensure_ascii=False)} "
                          f"megapixels={rs['inputs'].get('megapixels')}")

    # ---- shared knobs ----------------------------------------------------
    for nid, n in api.items():
        if n["class_type"] == "RandomNoise":
            hit = follow_to_literal(api, (nid, "noise_seed"), wanted=("seed", "noise_seed", "value"))
            if hit:
                print(f"\nseed         #{hit[0]}.{hit[1]} = {json.dumps(hit[2], ensure_ascii=False)}")
    for nid, n in api.items():
        if n["class_type"] == "ResolutionSelector":
            print(f"resolution   #{nid}.aspect_ratio = {json.dumps(n['inputs'].get('aspect_ratio'), ensure_ascii=False)}"
                  f"   #{nid}.megapixels = {n['inputs'].get('megapixels')}")
    for nid, n in api.items():
        if (oi.get(n["class_type"]) or {}).get("output_node") and "filename_prefix" in n["inputs"]:
            print(f"output       #{nid} {n['class_type']}  filename_prefix = "
                  f"{json.dumps(n['inputs'].get('filename_prefix'), ensure_ascii=False)}")
    for nid, n in api.items():
        if n["class_type"] == "LoadImage":
            print(f"input-image  #{nid}.image = {json.dumps(n['inputs'].get('image'), ensure_ascii=False)}")
    for nid, n in api.items():
        if n["class_type"].startswith("H3Prompt"):
            txt = n["inputs"].get("text") if "text" in n["inputs"] else n["inputs"].get("输入提示词")
            print(f"prompt-node  #{nid} {n['class_type']} = {json.dumps(txt, ensure_ascii=False)[:100]}")


def cmd_history(args):
    if args.prompt_id:
        entry = history(args.prompt_id)
        if not entry:
            print("not found"); return
    else:
        h = http_json("/history")
        ids = list(h)[-args.limit:]
        for pid in ids:
            st = (h[pid].get("status") or {})
            print(f"{pid}  {st.get('status_str')}  outputs={len(collect_outputs(h[pid]))}")
        return
    print(json.dumps({"status": entry.get("status"), "outputs": collect_outputs(entry)},
                     ensure_ascii=False, indent=2)[:4000])


def cmd_cancel(args):
    if args.prompt_id:
        http_json("/queue", {"delete": [args.prompt_id]})
        print("deleted", args.prompt_id)
    else:
        http_json("/interrupt", {})
        print("interrupted running job")


def upload_image(src_path: str, subfolder: str = "", overwrite: bool = True) -> dict:
    """POST a file to /upload/image; returns the server's {name, subfolder, type}."""
    src = Path(src_path).expanduser()
    if not src.exists():
        raise SystemExit(f"no such file: {src}")
    boundary = "----dsh" + uuid.uuid4().hex
    ctype = mimetypes.guess_type(src.name)[0] or "application/octet-stream"
    parts = []
    for name, val in (("type", "input"), ("subfolder", subfolder or ""),
                      ("overwrite", "true" if overwrite else "false")):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{val}\r\n".encode())
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{src.name}\"\r\n"
        f"Content-Type: {ctype}\r\n\r\n".encode() + src.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        BASE + "/upload/image", data=b"".join(parts), method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.loads(resp.read().decode("utf-8"))


def cmd_upload(args):
    print(json.dumps(upload_image(args.image, args.subfolder or ""), ensure_ascii=False))


# --------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="DSH <-> ComfyUI bridge")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status").set_defaults(func=cmd_status)

    p = sub.add_parser("models"); p.add_argument("kind", nargs="?"); p.set_defaults(func=cmd_models)

    sub.add_parser("workflows").set_defaults(func=cmd_workflows)

    p = sub.add_parser("describe"); p.add_argument("workflow"); p.add_argument("--filter")
    p.set_defaults(func=cmd_describe)

    p = sub.add_parser("plan", help="show the live wiring as ready-to-use --set selectors")
    p.add_argument("workflow"); p.set_defaults(func=cmd_plan)

    p = sub.add_parser("convert"); p.add_argument("workflow"); p.add_argument("--out")
    p.set_defaults(func=cmd_convert)

    p = sub.add_parser("run")
    p.add_argument("workflow")
    p.add_argument("--set", action="append", metavar="NODE.FIELD=VALUE")
    p.add_argument("--drop", action="append", metavar="SELECTOR",
                   help="remove nodes from the prompt (e.g. unused H3PromptPolish enhancers)")
    p.add_argument("--no-auto-drop", action="store_true",
                   help="ignore auto_drop/auto_set from pipelines.json")
    p.add_argument("--project"); p.add_argument("--shot")
    p.add_argument("--wait", action="store_true")
    p.add_argument("--check", action="store_true", help="submit, then cancel (validation only)")
    p.add_argument("--dry-run", action="store_true", help="print converted prompt, submit nothing")
    p.add_argument("--dump", metavar="FILE")
    p.add_argument("--timeout", type=float, default=1800)
    p.add_argument("--poll", type=float, default=5)
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("watch"); p.add_argument("prompt_id")
    p.add_argument("--project"); p.add_argument("--shot")
    p.add_argument("--timeout", type=float, default=3600); p.add_argument("--poll", type=float, default=5)
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("history"); p.add_argument("prompt_id", nargs="?"); p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("cancel"); p.add_argument("prompt_id", nargs="?"); p.set_defaults(func=cmd_cancel)

    p = sub.add_parser("upload"); p.add_argument("image"); p.add_argument("--subfolder")
    p.set_defaults(func=cmd_upload)

    args = ap.parse_args()
    if args.cmd != "help" and not api_alive():
        raise SystemExit(f"ComfyUI not reachable at {BASE} — start it with {LAUNCHER}")
    args.func(args)


if __name__ == "__main__":
    main()
