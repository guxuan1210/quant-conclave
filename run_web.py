"""Quick-start script for QuantConclave — main dashboard + K-line chart.

Ports come from :mod:`quantconclave.runtime_manifest` so the launcher,
the README, and the runtime-consistency test never drift apart.
"""
import uvicorn
import threading

from quantconclave.runtime_manifest import HOST, main_port, chart_port


def start_main():
    try:
        uvicorn.run("web.app:app", host=HOST, port=main_port())
    except Exception as e:
        print(f"Warning: main server {main_port()}:", e)


def start_chart():
    uvicorn.run("chart_app:app", host=HOST, port=chart_port())


if __name__ == "__main__":
    t1 = threading.Thread(target=start_main, daemon=True)
    t2 = threading.Thread(target=start_chart, daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
