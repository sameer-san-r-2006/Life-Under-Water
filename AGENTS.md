# Repository Guidelines — Life Under Water

## Scope
Draft 1 is a wired USB-serial IoT water-quality monitoring dashboard. Stack: Arduino C++ / NodeMCU ESP8266, HTML/CSS/vanilla JavaScript, Chart.js, Python FastAPI, PostgreSQL. No Wi-Fi dependency.

## Architecture
Sensors → NodeMCU → USB serial → FastAPI → PostgreSQL → FastAPI API → browser dashboard.
The browser must not read the COM port or access PostgreSQL directly.

## Current scope
Implement Overview, Analytics, Devices, and Predictions. Predictions may compare Random Forest and XGBoost only when trained on verified experimental category labels. The categories are not certified water safety or suitability classifications. The ML dataset export is limited to valid readings with explicit verified labels. No Demo Mode, Settings, Alerts, Reports, or general dashboard CSV export in Draft 1.

## Engineering
- Inspect and reuse existing files before changes.
- Make the smallest complete change; avoid unnecessary dependencies and abstractions.
- Keep raw voltage/counts separate from calibrated values.
- Never invent sensor readings or replace missing values with zero.
- Never present uncalibrated voltage as ppm/NTU or claim water is safe based only on these sensors.
- Use UTC timestamps in storage and local time in the UI.
- Keep secrets in environment variables; never commit credentials.
- Use parameterized SQL and validate API inputs.
- Test the relevant behavior and report only checks actually performed.
