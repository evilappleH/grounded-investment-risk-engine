# Local Risk Control Console

This is a loopback-only user interface for configuring a read-only real or
paper/simulated broker connection, selecting an AI provider, synchronizing or
manually entering a portfolio, and running deterministic risk checks.

Security properties in V1:

- binds to `127.0.0.1` by default;
- permits Moomoo/IBKR real-account reads as well as simulation/Paper reads;
- keeps API keys and account settings in process memory only;
- does not return API keys from the server;
- broker clients expose portfolio reads only; no place/modify/cancel method;
- has no broker-write mode and creates no order or execution authorization;
- separates facts, inferences, and recommendations in the risk result.

Run locally:

```powershell
python -m investment_agents.user_app.server --open-browser
```

Then open `http://127.0.0.1:8765`.

Do not use `investment_agents/user_app/static/index.html` as the working app.
Opening that file directly is a styled, bilingual preview only; browser security
prevents it from reaching the local account and risk-analysis API. Use the
launcher or server command above for broker connections and analysis.

The console supports English and Chinese. English is the first-use default,
the header provides a manual switch, and only that language preference is
stored in browser local storage.

On Windows, the convenience launcher uses `python` from `PATH` and opens the
correct local URL automatically:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_local_risk_ui.ps1
```

Or pass an explicit interpreter:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_local_risk_ui.ps1 -PythonPath C:\path\to\python.exe
```

Local test checklist:

1. Start Moomoo OpenD on port `11111`, or enable the IBKR TWS/IB Gateway API
   and its Read-Only API setting on the matching real/Paper port.
2. Start the console and confirm the readiness pills show the SDK and selected
   gateway port as ready.
3. Select the broker environment and click **检测只读账户**. Choose a discovered
   account rather than typing an ID.
4. Click **连接并分析实仓**. The app synchronizes, disconnects the broker session,
   and automatically displays the risk report.
5. Enter a proposed side, symbol, quantity, price, planned time, and review
   horizon. The app reports risk deltas and whether a fresh portfolio snapshot
   will be mandatory at the planned time.

If no gateway port is listening, the UI reports that condition directly. It
does not fall back to manual holdings or pretend that the portfolio is live.
