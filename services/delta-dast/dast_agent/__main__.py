"""실행: python -m dast_agent serve --port 8765 --target-port 8766"""

import argparse
import asyncio
import socket

import uvicorn

from .fixture import create_fixture_app
from .server import create_app


async def serve(port: int, target_port: int):
    target = create_fixture_app(port=target_port)
    control = create_app(port=port, target_port=target_port, on_run_start=target.state.reset)
    # Bind both sockets before starting either server; never leave a half-started pair.
    sockets = []
    servers = []
    tasks = []
    try:
        for selected_port, app in ((port, control), (target_port, target)):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sockets.append(sock)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", selected_port))
            sock.listen(128)
            sock.setblocking(False)
            servers.append(uvicorn.Server(uvicorn.Config(
                app, host="127.0.0.1", port=selected_port, log_level="warning", access_log=False,
            )))
        print(f"제어 화면: http://127.0.0.1:{port}", flush=True)
        print(f"격리된 보안 실습용 게시판: http://127.0.0.1:{target_port}/vulnerable/board", flush=True)
        tasks = [asyncio.create_task(server.serve(sockets=[sock]))
                 for server, sock in zip(servers, sockets)]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for server in servers:
            server.should_exit = True
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for sock in sockets:
            sock.close()


def main():
    parser = argparse.ArgumentParser(description="고정 localhost 게시판 저장형 XSS 검증")
    parser.add_argument("command", nargs="?", choices=["serve"], default="serve")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--target-port", type=int, default=8766)
    args = parser.parse_args()
    try:
        asyncio.run(serve(args.port, args.target_port))
    except (OSError, ValueError) as exc:
        parser.exit(1, f"서버를 시작하지 못했습니다: {type(exc).__name__}\n")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
