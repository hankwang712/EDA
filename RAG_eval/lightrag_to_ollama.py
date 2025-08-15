import requests
from flask import Flask, request, jsonify, Response
import logging
import json
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict, field
from datetime import datetime
from enum import Enum, auto
import time

app = Flask(__name__)
logging.basicConfig(level=logging.DEBUG)

# === 配置结构 === #
@dataclass
class ServerConfig:
    lightrag_url: str = "http://localhost:9621"
    ollama_url: str = "http://localhost:11434"
    model_name: str = "LightragModel:latest"
    only_need_context_prefix: str = "/返回查询结果"
    mode_prefixes: Dict[str, str] = field(default_factory=lambda: {
        "local": "/具体地：",
        "global": "/概括地：",
        "naive": "/简单地：",
        "mix": "/混合地："
    })
    forward_mode: bool = False
    prefix_mode_switch: bool = True

config = ServerConfig()


# === 错误结构 === #
class ErrorCode(Enum):
    InvalidRequest = auto()
    InternalError = auto()

class McpError(Exception):
    def __init__(self, code: ErrorCode, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass
class TestResult:
    name: str
    success: bool
    duration: float
    error: Optional[str] = None
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

class TestStats:
    def __init__(self):
        self.results: List[TestResult] = []
        self.start_time = datetime.now()

    def add_result(self, result: TestResult):
        self.results.append(result)

    def export_results(self, path: str = "test_results.json"):
        results_data = {
            "start_time": self.start_time.isoformat(),
            "end_time": datetime.now().isoformat(),
            "results": [asdict(r) for r in self.results],
            "summary": {
                "total": len(self.results),
                "passed": sum(1 for r in self.results if r.success),
                "failed": sum(1 for r in self.results if not r.success),
                "total_duration": sum(r.duration for r in self.results),
            },
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(results_data, f, ensure_ascii=False, indent=2)
        print(f"Test results saved to: {path}")

    def print_summary(self):
        total = len(self.results)
        passed = sum(1 for r in self.results if r.success)
        failed = total - passed
        duration = sum(r.duration for r in self.results)
        print("\n=== Test Summary ===")
        print(f"Total tests: {total}, Passed: {passed}, Failed: {failed}, Total time: {duration:.2f}s")
        if failed:
            for r in self.results:
                if not r.success:
                    print(f" - {r.name}: {r.error}")

test_stats = TestStats()

@app.route('/')
def index():
    return 'Hello, LightRAG is running!'


def determine_mode_and_strip_prefix(content: str):
    if config.prefix_mode_switch:
        for mode, prefix in config.mode_prefixes.items():
            if content.startswith(prefix):
                return mode, content[len(prefix):]
    return "hybrid", content


def call_lightrag_api(query: str, mode: str = "hybrid") -> Optional[Dict[str, Any]]:
    headers = {"Content-Type": "application/json"}
    only_need_context = query.startswith(config.only_need_context_prefix)
    lightrag_request = {
        "query": query,
        "mode": mode,
        "only_need_context": only_need_context
    }
    try:
        start = time.time()
        response = requests.post(f"{config.lightrag_url}/query", headers=headers, json=lightrag_request)
        duration = time.time() - start

        logging.debug(f"Sent request to Lightrag API: {lightrag_request}")
        logging.debug(f"Lightrag API returned status {response.status_code}")

        if response.status_code == 200:
            try:
                json_data = response.json()
                logging.debug(f"Lightrag API response content: {json_data}")
                return json_data
            except ValueError:
                raise McpError(ErrorCode.InternalError, "响应解析失败")
        else:
            raise McpError(ErrorCode.InternalError, f"响应状态码异常: {response.status_code}")
    except requests.RequestException as e:
        raise McpError(ErrorCode.InternalError, f"请求异常: {str(e)}")

def call_lightrag_api_stream(query: str, mode: str = "hybrid"):
    def generate_stream():
        try:
            headers = {"Content-Type": "application/json"}
            only_need_context = query.startswith(config.only_need_context_prefix)
            lightrag_request = {
                "query": query,
                "mode": mode,
                "only_need_context": only_need_context
            }

            response = requests.post(
                f"{config.lightrag_url}/query/stream",
                headers=headers,
                json=lightrag_request,
                stream=True
            )

            if response.status_code != 200:
                logging.error(f"[stream] query/stream 返回错误码: {response.status_code}")
                yield json.dumps({"error": f"下游流式接口异常: {response.status_code}", "done": True}) + "\n"
                return

            for line in response.iter_lines(decode_unicode=True):
                if line.strip():
                    try:
                        data = json.loads(line)
                        token = data.get("response", "")
                        chunk = {
                            "model": config.model_name,
                            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "message": {
                                "role": "assistant",
                                "content": token,
                                "images": None
                            },
                            "done": False
                        }
                        yield json.dumps(chunk, ensure_ascii=False) + "\n"
                    except Exception as e:
                        yield json.dumps({"error": f"token解析失败: {str(e)}", "done": True}) + "\n"

            # 最终结束标志
            yield json.dumps({"model": config.model_name,
                              "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                              "message": {"role": "assistant", "content": "", "images": None},
                              "done": True}) + "\n"

        except Exception as e:
            yield json.dumps({"error": f"连接错误: {str(e)}", "done": True}) + "\n"

    return Response(generate_stream(), content_type="application/x-ndjson")


@app.route('/api/chat', methods=['POST'])
def chat():
    
    start_time = time.time()
    data = request.get_json()
    if "model" not in data:
        logging.warning("请求中缺少 'model' 字段，使用默认模型")
        data["model"] = config.model_name 
    logging.debug(f"Received data: {data}")

    if not data or "messages" not in data or not data["messages"] or "model" not in data:
        return jsonify({"error": "Invalid request format"}), 400

    is_streaming = data.get("stream", True)
    content = data["messages"][-1]["content"]
    mode, stripped_content = determine_mode_and_strip_prefix(content)

    if config.forward_mode:
        return forward_request_to_ollama(data)

    try:
        if is_streaming:
            return call_lightrag_api_stream(stripped_content,mode=mode)
        else:
            result = call_lightrag_api(stripped_content, mode=mode)
            duration = time.time() - start_time

            result_content = result.get("response") if result else None
            if result_content:
                test_stats.add_result(TestResult(name="chat", success=True, duration=duration))
                ollama_response = format_response(data["model"], result_content, duration)
                return jsonify(ollama_response)
            else:
                raise McpError(ErrorCode.InvalidRequest, "响应内容中无'response'")

    except McpError as e:
        duration = time.time() - start_time
        test_stats.add_result(TestResult(name="chat", success=False, duration=duration, error=e.message))
        return jsonify({"error": e.message}), 500


def forward_request_to_ollama(data):
    try:
        with requests.post(f"{config.ollama_url}/api/chat", json=data, stream=True) as response:
            if response.status_code != 200:
                return jsonify({"error": "Failed to forward request to OLLAMA API"}), response.status_code
            return Response(response.iter_content(chunk_size=8192), content_type=response.headers.get('Content-Type'))
    except requests.RequestException as e:
        return jsonify({"error": f"Failed to forward request to OLLAMA API: {str(e)}"}), 500


def format_response(model: str, content: str, duration: float):
    return {
        "model": model,
        "created_at": datetime.now().isoformat(),
        "message": {"role": "assistant", "content": content, "images": None},
        "done": True,
        "total_duration": duration,
        "load_duration": 200000,
        "prompt_eval_count": 10,
        "prompt_eval_duration": 300000000,
        "eval_count": 0,
        "eval_duration": 0
    }

@app.route('/api/version', methods=['GET'])
def get_version():
    if config.forward_mode:
        response = requests.get(f"{config.ollama_url}/api/version")
        return (response.content, response.status_code, response.headers.items())

    version_info = {"version": "0.3.35", "description": "A fake API server for OpenWebUI"}
    try:
        response = requests.get(f"{config.ollama_url}/api/version")
        if response.status_code == 200:
            version_info = response.json()
    except requests.RequestException:
        pass

    return jsonify(version_info)


@app.route('/api/tags', methods=['GET'])
def get_tags():
    if config.forward_mode:
        response = requests.get(f"{config.ollama_url}/api/tags")
        return (response.content, response.status_code, response.headers.items())

    fake_model_info = {
        "name": config.model_name,
        "model": config.model_name,
        "modified_at": datetime.now().isoformat(),
        "size": 9999999,
        "digest": "fakehash",
        "details": {
            "parent_model": "",
            "format": "gguf",
            "family": "llama",
            "families": ["llama"],
            "parameter_size": "72B",
            "quantization_level": "float16"
        }
    }
    return jsonify({"models": [fake_model_info]})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=3030)