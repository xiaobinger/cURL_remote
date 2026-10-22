"""多语言代码生成：基于 resolve_request 的结构化请求，渲染各语言调用示例。

与本地直发（http_executor）语义对齐：header 补齐默认 Content-Type，
body 按最终文本原样发送（签名已注入），不额外加 User-Agent。
"""
import json

from .curl_builder import render_curl, DEFAULT_CONTENT_TYPE

# 代码预览支持的语言（前端下拉按此顺序展示）
LANGS = [
    ("curl", "cURL"),
    ("python", "Python"),
    ("javascript", "JavaScript"),
    ("typescript", "TypeScript"),
    ("node", "Node.js"),
    ("java", "Java"),
    ("csharp", "C#"),
    ("go", "Go"),
    ("php", "PHP"),
    ("rust", "Rust"),
]


def _esc_c(s):
    """C 系语言双引号字符串转义（保留非 ASCII，Java/C#/Go/Rust/PHP 均兼容 UTF-8 源码）"""
    return (s.replace("\\", "\\\\").replace('"', '\\"')
             .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))


def _esc_py(s):
    return json.dumps(s, ensure_ascii=False)


def _final_headers(req):
    """与 http_executor 对齐：body 存在且缺 Content-Type 时补默认值"""
    headers = [list(h) for h in req["headers"]]
    if req["body"] is not None and not any(
            k.lower() == "content-type" for k, _ in headers):
        ct = DEFAULT_CONTENT_TYPE.get(req["body_type"], "application/octet-stream")
        headers.append(["Content-Type", ct])
    return headers


def _content_type(req, headers):
    for k, v in headers:
        if k.lower() == "content-type":
            return v
    return ""


# ──────────────────── 各语言渲染 ────────────────────

def _render_python(req):
    headers = _final_headers(req)
    lines = ["import requests", "", f'url = {_esc_py(req["url"])}']
    if headers:
        lines.append("headers = {")
        for k, v in headers:
            lines.append(f"    {_esc_py(k)}: {_esc_py(v)},")
        lines.append("}")
    if req["body"] is not None:
        lines.append(f'body = {_esc_py(req["body"])}')
    lines.append("")
    args = [f'{_esc_py(req["method"])}', "url"]
    if headers:
        args.append("headers=headers")
    if req["body"] is not None:
        args.append('data=body.encode("utf-8")')
    lines.append(f'response = requests.request({", ".join(args)})')
    lines += ["print(response.status_code)", "print(response.text)"]
    return "\n".join(lines)


def _render_fetch(req, typed=False):
    headers = _final_headers(req)
    lines = [f'const url = "{_esc_c(req["url"])}";']
    opt = []
    opt.append(f'  method: "{req["method"]}",')
    if headers:
        hk = "const headers: Record<string, string> = {" if typed else "const headers = {"
        lines.append(hk)
        for k, v in headers:
            lines.append(f'  "{_esc_c(k)}": "{_esc_c(v)}",')
        lines.append("};")
        opt.append("  headers,")
    if req["body"] is not None:
        lines.append(f'const body = "{_esc_c(req["body"])}";')
        opt.append("  body,")
    lines.append("")
    resp_t = ": Response" if typed else ""
    lines.append(f"const resp{resp_t} = await fetch(url, {{")
    lines.extend(opt)
    lines.append("});")
    lines.append("console.log(resp.status, await resp.text());")
    return "\n".join(lines)


def _render_node(req):
    headers = _final_headers(req)
    lines = ['const axios = require("axios");', "", "const resp = await axios.request({"]
    lines.append(f'  method: "{req["method"].lower()}",')
    lines.append(f'  url: "{_esc_c(req["url"])}",')
    if headers:
        lines.append("  headers: {")
        for k, v in headers:
            lines.append(f'    "{_esc_c(k)}": "{_esc_c(v)}",')
        lines.append("  },")
    if req["body"] is not None:
        lines.append(f'  data: "{_esc_c(req["body"])}",')
    lines.append("});")
    lines.append("console.log(resp.status, resp.data);")
    return "\n".join(lines)


def _render_java(req):
    headers = _final_headers(req)
    body = req["body"]
    lines = ["import okhttp3.*;", "", "public class Main {",
             "    public static void main(String[] args) throws Exception {",
             "        OkHttpClient client = new OkHttpClient();"]
    if body is not None:
        ct = _content_type(req, headers) or "text/plain"
        lines.append(f'        MediaType mediaType = MediaType.parse("{_esc_c(ct)}");')
        lines.append(f'        RequestBody body = RequestBody.create(mediaType, "{_esc_c(body)}");')
    lines.append("        Request request = new Request.Builder()")
    lines.append(f'            .url("{_esc_c(req["url"])}")')
    lines.append(f'            .method("{req["method"]}", {"body" if body is not None else "null"})')
    for k, v in headers:
        if k.lower() == "content-type" and body is not None:
            continue  # OkHttp 由 MediaType 携带
        lines.append(f'            .addHeader("{_esc_c(k)}", "{_esc_c(v)}")')
    lines.append("            .build();")
    lines.append("        Response response = client.newCall(request).execute();")
    lines.append("        System.out.println(response.code());")
    lines.append("        System.out.println(response.body().string());")
    lines.append("    }")
    lines.append("}")
    return "\n".join(lines)


def _render_csharp(req):
    headers = _final_headers(req)
    body = req["body"]
    m = req["method"]
    lines = ["using System.Net.Http;", "using System.Text;", "",
             "var client = new HttpClient();",
             f'var request = new HttpRequestMessage(HttpMethod.{m.title()}, "{_esc_c(req["url"])}");']
    ct = _content_type(req, headers)
    for k, v in headers:
        if k.lower() == "content-type" and body is not None:
            continue  # C# 里 Content-Type 属于 Content
        lines.append(f'request.Headers.TryAddWithoutValidation("{_esc_c(k)}", "{_esc_c(v)}");')
    if body is not None:
        lines.append(f'request.Content = new StringContent("{_esc_c(body)}", Encoding.UTF8, "{_esc_c(ct or "text/plain")}");')
    lines.append("var response = await client.SendAsync(request);")
    lines.append("Console.WriteLine((int)response.StatusCode);")
    lines.append("Console.WriteLine(await response.Content.ReadAsStringAsync());")
    return "\n".join(lines)


def _render_go(req):
    headers = _final_headers(req)
    body = req["body"]
    lines = ["package main", "", "import (", '\t"fmt"', '\t"io"', '\t"net/http"']
    if body is not None:
        lines.append('\t"strings"')
    lines += [")", "", "func main() {"]
    if body is not None:
        lines.append(f'\tbody := strings.NewReader("{_esc_c(body)}")')
        lines.append(f'\treq, _ := http.NewRequest("{req["method"]}", "{_esc_c(req["url"])}", body)')
    else:
        lines.append(f'\treq, _ := http.NewRequest("{req["method"]}", "{_esc_c(req["url"])}", nil)')
    for k, v in headers:
        lines.append(f'\treq.Header.Set("{_esc_c(k)}", "{_esc_c(v)}")')
    lines += ["\tresp, err := http.DefaultClient.Do(req)",
              "\tif err != nil {", "\t\tpanic(err)", "\t}",
              "\tdefer resp.Body.Close()", "",
              "\tdata, _ := io.ReadAll(resp.Body)",
              '\tfmt.Println(resp.StatusCode)', '\tfmt.Println(string(data))', "}"]
    return "\n".join(lines)


def _render_php(req):
    headers = _final_headers(req)
    body = req["body"]
    lines = ["<?php", "$ch = curl_init();", "",
             f'curl_setopt($ch, CURLOPT_URL, "{_esc_c(req["url"])}");']
    if req["method"] != "GET":
        lines.append(f'curl_setopt($ch, CURLOPT_CUSTOMREQUEST, "{req["method"]}");')
    lines.append("curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);")
    if headers:
        lines.append("curl_setopt($ch, CURLOPT_HTTPHEADER, [")
        for k, v in headers:
            lines.append(f'    "{_esc_c(k)}: {_esc_c(v)}",')
        lines.append("]);")
    if body is not None:
        lines.append(f"curl_setopt($ch, CURLOPT_POSTFIELDS, '{body.replace(chr(92), chr(92)*2).replace(chr(39), chr(92)+chr(39))}');")
    lines += ["", "$resp = curl_exec($ch);", "$code = curl_getinfo($ch, CURLINFO_HTTP_CODE);",
              "curl_close($ch);", "", "echo $code, \"\\n\";", "echo $resp;"]
    return "\n".join(lines)


def _render_rust(req):
    headers = _final_headers(req)
    body = req["body"]
    m = req["method"].lower()
    lines = ['// Cargo.toml: reqwest = { version = "0.12", features = ["blocking"] }',
             "", "fn main() -> Result<(), Box<dyn std::error::Error>> {",
             "    let client = reqwest::blocking::Client::new();",
             f'    let resp = client.request(reqwest::Method::{m.upper()}, "{_esc_c(req["url"])}")']
    for k, v in headers:
        lines.append(f'        .header("{_esc_c(k)}", "{_esc_c(v)}")')
    if body is not None:
        lines.append(f'        .body("{_esc_c(body)}")')
    lines.append("        .send()?;")
    lines.append('    println!("{}\\n{}", resp.status(), resp.text()?);')
    lines.append("    Ok(())")
    lines.append("}")
    return "\n".join(lines)


_RENDERERS = {
    "python": _render_python,
    "javascript": _render_fetch,
    "typescript": lambda req: _render_fetch(req, typed=True),
    "node": _render_node,
    "java": _render_java,
    "csharp": _render_csharp,
    "go": _render_go,
    "php": _render_php,
    "rust": _render_rust,
}


def render_code(req, lang):
    """把结构化请求渲染为指定语言的调用代码。lang 见 LANGS。"""
    lang = (lang or "curl").lower()
    if lang == "curl":
        return render_curl(req)
    fn = _RENDERERS.get(lang)
    if fn is None:
        raise ValueError(f"不支持的语言: {lang}")
    return fn(req)
