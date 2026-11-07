import json
import socket
import sys
import uuid

from .server import SOCKET_PATH, serve


def request(method, params, timeout=60):
    payload = {
        "id": uuid.uuid4().hex,
        "method": method,
        "params": params,
    }
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(max(timeout + 5, 10))
        client.connect(str(SOCKET_PATH))
        client.sendall(json.dumps(payload).encode() + b"\n")
        response = client.makefile("rb").readline(2_097_153)
    if not response or len(response) > 2_097_152:
        raise RuntimeError("invalid response")
    return json.loads(response)


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "server":
        serve()
        return 0
    if len(sys.argv) >= 5 and sys.argv[1] == "exec" and sys.argv[2] == "--timeout":
        response = request("exec", {"argv": sys.argv[4:], "timeout": int(sys.argv[3])}, int(sys.argv[3]))
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return 0 if response.get("ok") and response["result"]["exit_code"] == 0 else 1
    if len(sys.argv) == 4 and sys.argv[1] == "call":
        params = json.loads(sys.argv[3])
        if not isinstance(params, dict):
            raise ValueError("call parameters must be a JSON object")
        response = request(sys.argv[2], params)
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return 0 if response.get("ok") else 1
    print("usage: python -m agentd server | exec --timeout SECONDS ARGV... | call METHOD JSON", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
