"""Launch the live data-ops dashboard.

  python run_dashboard.py                 # → http://127.0.0.1:8800
  python run_dashboard.py --port 9000 --reload

Reads outputs/ live on every request — no build step, no DB. Start a pipeline run in
another terminal and refresh; the new run shows up.
"""

import argparse

import uvicorn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8800)
    ap.add_argument("--reload", action="store_true")
    a = ap.parse_args()
    print(f"Janus dashboard -> http://{a.host}:{a.port}")
    uvicorn.run("web.dashboard:app", host=a.host, port=a.port, reload=a.reload)


if __name__ == "__main__":
    main()
